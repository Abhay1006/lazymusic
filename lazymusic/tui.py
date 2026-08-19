"""lazymusic - a lazygit-style panel UI for Apple Music.

Three stacked panels on the left (player, playlists, search) and a main track
list on the right. Tab or the number keys move focus; the focused panel gets a
bright border, and the main panel follows whatever the focused list is showing.

Frames are composed as whole rows and written home-cursor-first with per-line
erases rather than a screen clear, so the display never flickers.
"""

import os
import queue
import select
import shutil
import subprocess
import sys
import termios
import threading
import time
import tty

from . import music
from .fmt import bar, box, mmss, pad, row, truncate, width_of
from .osa import MusicError

ALT_ON, ALT_OFF = "\033[?1049h", "\033[?1049l"
CUR_HIDE, CUR_SHOW = "\033[?25l", "\033[?25h"
HOME, CLR_EOL, CLR_EOS = "\033[H", "\033[K", "\033[J"

FRAME = 0.2       # seconds between repaints
STATE_PLAYING = 1.0  # seconds between state reads while playing
STATE_IDLE = 3.0     # ...and while paused or stopped, where nothing moves
LOAD_DELAY = 0.18  # settle time before loading the highlighted playlist
CACHE_TTL = 60.0   # seconds a cached playlist stays fresh; `R` clears it early

PLAYER, PLAYLISTS, SEARCH, TRACKS = 1, 2, 3, 4
NORMAL, FILTER, COMMAND = "normal", "filter", "command"
PANELS = (PLAYER, PLAYLISTS, SEARCH, TRACKS)
TITLES = {PLAYER: "Player", PLAYLISTS: "Playlists", SEARCH: "Search",
          TRACKS: "Tracks"}

PLAYER_HEIGHT = 7  # 5 content rows plus two borders

HELP_LEFT = [
    ("MOTION", ""),
    ("j / k", "down / up"),
    ("<count>j", "e.g. 12j moves 12 rows"),
    ("gg / G", "first / last"),
    ("<count>G", "go to row <count>"),
    ("ctrl-d / u", "half page"),
    ("ctrl-f / b", "full page"),
    ("{ / }", "up / down 10"),
    ("H / M / L", "top / middle / bottom"),
    ("zz / zt / zb", "centre / top / bottom view"),
    ("", ""),
    ("PANELS", ""),
    ("h / l", "left stack / main panel"),
    ("ctrl-w hjkl", "focus by direction"),
    ("ctrl-w 1-4", "focus by number"),
    ("tab", "cycle panels"),
]

HELP_RIGHT = [
    ("SEARCH", ""),
    ("/", "filter this panel"),
    ("esc", "clear filter"),
    ("S", "search the library"),
    ("*", "search artist under cursor"),
    ("ctrl-o", "back to previous view"),
    ("R", "reload library from Music"),
    ("", ""),
    ("PLAYBACK", ""),
    ("space", "play / pause"),
    ("n / p", "next / previous track"),
    ("[ / ]", "seek back / forward 10s"),
    ("+ / -", "volume"),
    ("s / r / f", "shuffle / repeat / favourite"),
    ("enter", "play selection"),
    ("q  or  :q", "quit"),
]

# Shown in the third help column, and offered by tab-completion on the `:` line.
HELP_COMMANDS = [
    ("COMMANDS", ""),
    (":search <q>", "search the library"),
    (":filter <t>", "filter panel; bare clears"),
    (":play [q]", "resume, or find and play"),
    (":pl <name>", "play a playlist"),
    (":vol 60 | +10", "set or nudge volume"),
    (":seek 90 | -15", "jump to, or by, seconds"),
    (":shuffle [on|off]", ""),
    (":repeat [off|one|all]", ""),
    (":pause :next :prev", ""),
    (":fav", "favourite current track"),
    (":reload", "re-read the library"),
    (":42", "jump to row 42"),
    (":help", "this panel"),
    (":q", "quit"),
    ("", ""),
    ("tab", "completes on the : line"),
]


class Keys:
    """Raw-mode keyboard reader that decodes the arrow-key escape sequences."""

    def __init__(self, stream=sys.stdin):
        self.fd = stream.fileno()
        self._saved = None

    def __enter__(self):
        self._saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        return self

    def __exit__(self, *exc):
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def poll(self, timeout):
        if not select.select([self.fd], [], [], timeout)[0]:
            return []
        data = os.read(self.fd, 1024).decode("utf-8", "replace")
        keys, i = [], 0
        while i < len(data):
            ch = data[i]
            if ch == "\033":
                name, consumed = self._escape(data, i)
                keys.append(name)
                i += consumed
                continue
            if ch in ("\r", "\n"):
                keys.append("enter")
            elif ch in ("\x7f", "\b"):
                keys.append("backspace")
            elif ch == "\t":
                # Terminals send Ctrl+I as Tab; they are the same byte, so the
                # vim jumplist-forward binding is simply not available here.
                keys.append("tab")
            elif ch < " ":
                keys.append("ctrl-" + chr(ord(ch) + 96))
            else:
                keys.append(ch)
            i += 1
        return keys

    @staticmethod
    def _escape(data, i):
        """Decode one escape sequence starting at `i`; return (name, length)."""
        if data[i + 1:i + 2] != "[":
            return "esc", 1
        j = i + 2
        while j < len(data) and data[j] in "0123456789;":
            j += 1
        if j >= len(data):
            return "esc", 1
        params, final = data[i + 2:j], data[j]
        length = j - i + 1
        simple = {"A": "up", "B": "down", "C": "right", "D": "left",
                  "H": "home", "F": "end", "Z": "shift-tab"}
        if final in simple:
            return simple[final], length
        if final == "~":
            return {"1": "home", "4": "end", "5": "pageup", "6": "pagedown",
                    "7": "home", "8": "end"}.get(params, "unknown"), length
        return "unknown", length


class Bus:
    """Runs Music.app calls on a worker thread so the UI never blocks on them.

    Every osascript round trip costs 70-150ms. Doing that on the draw loop meant
    the interface froze for a sizeable slice of every second, which is what made
    keystrokes feel like they arrived late. Jobs go out on a queue and results
    come back on another, applied by the UI thread in `drain` - so callbacks
    still run single-threaded and nothing needs a lock.
    """

    def __init__(self):
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.inflight = set()
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    def submit(self, key, call, then=None):
        """Queue `call`. A key already in flight is dropped, not stacked up."""
        if key in self.inflight:
            return False
        self.inflight.add(key)
        self.jobs.put((key, call, then))
        return True

    def _loop(self):
        while True:
            key, call, then = self.jobs.get()
            try:
                self.results.put((key, then, call(), None))
            except MusicError as e:
                self.results.put((key, then, None, e))
            except Exception as e:                      # never kill the worker
                self.results.put((key, then, None, MusicError(str(e))))

    def drain(self):
        """Apply finished jobs on the UI thread. Returns True if any landed."""
        got = False
        while True:
            try:
                key, then, value, err = self.results.get_nowait()
            except queue.Empty:
                return got
            self.inflight.discard(key)
            got = True
            if then is not None:
                then(value, err)

    @property
    def busy(self):
        return bool(self.inflight)


class Lane:
    """One scrollable list: the rows, which is selected, and what is on screen.

    `all_items` is everything; `items` is what a `/` filter has left visible.
    """

    def __init__(self, label=lambda item: str(item)):
        self.all_items = []
        self.items = []
        self.cursor = 0
        self.scroll = 0
        self.filter = ""
        self.label = label

    def set_items(self, items):
        self.all_items = list(items)
        self.apply_filter(self.filter)

    def apply_filter(self, text):
        self.filter = text
        if not text:
            self.items = list(self.all_items)
            return
        needle = text.lower()
        self.items = [i for i in self.all_items if needle in self.label(i).lower()]
        self.cursor = min(self.cursor, max(0, len(self.items) - 1))

    def move(self, delta, view):
        if not self.items:
            return False
        before = self.cursor
        self.cursor = max(0, min(self.cursor + delta, len(self.items) - 1))
        self.clamp(view)
        return self.cursor != before

    def goto(self, index, view):
        if not self.items:
            return False
        before = self.cursor
        self.cursor = max(0, min(index, len(self.items) - 1))
        self.clamp(view)
        return self.cursor != before

    def screen_row(self, where, view):
        """vim H / M / L: jump to the top, middle or bottom visible row."""
        last = min(len(self.items), self.scroll + view) - 1
        target = {"H": self.scroll,
                  "M": (self.scroll + last) // 2,
                  "L": last}[where]
        return self.goto(target, view)

    def reposition(self, where, view):
        """vim zz / zt / zb: move the view so the cursor sits at a given spot."""
        offset = {"zt": 0, "zz": view // 2, "zb": view - 1}[where]
        self.scroll = max(0, min(self.cursor - offset,
                                 max(0, len(self.items) - view)))

    def clamp(self, view):
        view = max(1, view)
        self.cursor = max(0, min(self.cursor, max(0, len(self.items) - 1)))
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + view:
            self.scroll = self.cursor - view + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.items) - view)))

    @property
    def selected(self):
        if not self.items:
            return None
        return self.items[max(0, min(self.cursor, len(self.items) - 1))]


class App:
    def __init__(self):
        self.focus = PLAYLISTS
        self.state = music.State()
        self.lanes = {
            PLAYLISTS: Lane(lambda p: p[0]),
            SEARCH: Lane(lambda t: t.name + " " + t.artist),
            TRACKS: Lane(lambda t: t.name + " " + t.artist),
        }
        self.mode = NORMAL
        self.buffer = ""          # text typed after `/` or `:`
        self.count = ""           # vim count prefix, e.g. the 12 in `12j`
        self.pending = ""         # `g`, `z` or `ctrl-w` awaiting a second key
        self._history = []        # previous main-panel views, for ctrl-o
        self._last_left = PLAYLISTS
        self.help = False
        self.main_title = "Tracks"
        self.main_playlist = ""   # playlist the main panel is showing, if any
        self.message = ""
        self.message_until = 0.0
        self.running = True
        self._views = {}          # panel -> visible row count, set while drawing
        self._last_state = 0.0
        self._pos_at = 0.0
        self._pending = None      # (playlist_name, load_after_monotonic)
        self._cache = {}          # playlist name -> (fetched_at, tracks)
        self._last_frame = ""
        self._completions = []    # tab-completion candidates on the `:` line
        self.bus = Bus()

    # ------------------------------------------------------------ state ----

    def start(self):
        # The track list can only be asked for once the playlists have landed,
        # so it is chained off that rather than fired alongside it.
        self.refresh(force=True)
        self.load_playlists(then=lambda: self.queue_playlist_load(immediate=True))

    def load_playlists(self, then=None):
        def done(items, err):
            if err:
                self.notify(str(err))
                return
            lane = self.lanes[PLAYLISTS]
            lane.set_items(items)
            lane.clamp(self._views.get(PLAYLISTS, 10))
            if then:
                then()
        self.bus.submit("playlists", music.playlists, done)

    def reload(self):
        """`R`: forget cached track lists and re-read everything from Music.

        Music.app is not asked to notify anyone when the library changes, so a
        song added while lazymusic is open cannot show up on its own.
        """
        self._cache.clear()
        self.notify("reloading library…", 5)
        self.load_playlists(then=lambda: self.queue_playlist_load(immediate=True))

    def refresh(self, force=False):
        # Adaptive: a paused track cannot change on its own, so polling it hard
        # just burns CPU on osascript processes. Poll briskly while playing and
        # back off when nothing is moving.
        now = time.monotonic()
        gap = STATE_PLAYING if self.state.playing else STATE_IDLE
        if not force and now - self._last_state < gap:
            return
        self._last_state = now
        self.bus.submit("status", lambda: music.status(autolaunch=True),
                        self._got_status)

    def _got_status(self, state, err):
        if err:
            self.notify(str(err))
            return
        self.state = state
        self._pos_at = time.monotonic()

    @property
    def position(self):
        if not self.state.playing:
            return self.state.position
        return min(self.state.track.duration or 1e9,
                   self.state.position + (time.monotonic() - self._pos_at))

    def notify(self, text, seconds=2.5):
        self.message = text
        self.message_until = time.monotonic() + seconds

    def act(self, fn, *args):
        """Send a command, then re-read state. Both run on the worker thread.

        Commands are keyed by identity so mashing a key queues one of each
        rather than a hundred; the queue is FIFO, so ordering still holds.
        """
        key = "act:%s" % getattr(fn, "__name__", str(fn))
        self.bus.submit(key, lambda: fn(*args), self._acted)

    def _acted(self, _value, err):
        if err:
            self.notify(str(err))
        self._last_state = 0.0          # pull fresh state on the next tick
        self.refresh(force=True)

    # ------------------------------------------------- main panel loading ----

    def queue_playlist_load(self, immediate=False):
        """Ask for the highlighted playlist's tracks after a short settle delay.

        Holding down a cursor key would otherwise fire one query per keypress;
        waiting for the cursor to stop means only the playlist you land on is
        actually fetched.
        """
        sel = self.lanes[PLAYLISTS].selected
        if not sel:
            return
        self._pending = (sel[0], 0.0 if immediate else time.monotonic() + LOAD_DELAY)

    def tick_loads(self):
        if not self._pending:
            return
        name, due = self._pending
        if time.monotonic() < due:
            return
        self._pending = None
        cached = self._cache.get(name)
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            self.show_main(name, cached[1], playlist=name)
            return

        def done(tracks, err):
            if err:
                self.notify(str(err))
                return
            self._cache[name] = (time.monotonic(), tracks)
            # The cursor may have moved on while this was in flight; only paint
            # it if this is still the playlist the user is sitting on.
            sel = self.lanes[PLAYLISTS].selected
            if sel and sel[0] == name:
                self.show_main(name, tracks, playlist=name)

        # A stale copy keeps the panel populated while the refresh is in flight.
        if cached:
            self.show_main(name, cached[1], playlist=name)
        self.bus.submit("tracks", lambda: music.playlist_tracks(name), done)

    def show_main(self, title, tracks, playlist=""):
        lane = self.lanes[TRACKS]
        if title != self.main_title:
            lane.cursor = lane.scroll = 0
            lane.filter = ""
        lane.set_items(tracks)
        self.main_title = title
        self.main_playlist = playlist

    # ------------------------------------------------------------- draw ----

    def render(self):
        cols, rows = shutil.get_terminal_size((90, 28))
        cols, rows = max(60, cols), max(14, rows)
        height = rows - 1                       # last row is the key bar
        left_w = max(30, min(46, cols * 38 // 100))
        right_w = cols - left_w

        player_h = min(PLAYER_HEIGHT, max(3, height - 8))
        rest = height - player_h
        lists_h = max(3, rest // 2)
        search_h = max(3, rest - lists_h)

        left = []
        left += self.panel_player(left_w, player_h)
        left += self.panel_list(PLAYLISTS, left_w, lists_h)
        left += self.panel_list(SEARCH, left_w, search_h)
        left = left[:height] + [" " * left_w] * max(0, height - len(left))
        right = self.panel_main(right_w, height)

        lines = [l + r for l, r in zip(left, right)]
        lines.append(self.key_bar(cols))

        # The last row gets no trailing newline. Writing one on the bottom line
        # scrolls the whole grid up, which silently eats the top border.
        visible = lines[:rows]
        out = [HOME, (CLR_EOL + "\r\n").join(visible), CLR_EOL, CLR_EOS]
        frame = "".join(out)
        if frame == self._last_frame:
            return                      # nothing changed; skip the repaint
        self._last_frame = frame
        sys.stdout.write(frame)
        sys.stdout.flush()

    def panel_player(self, width, height):
        st = self.state
        inner = width - 2
        body = []
        if not st.running:
            body.append([("  Music.app is starting…", "grey")])
        elif st.stopped:
            body.append([("  Nothing playing", "grey")])
            body.append([])
            body.append([("  Pick a playlist and press ", "grey"), ("enter", "bold")])
        else:
            t = st.track
            icon, tone = ("▶", "brightgreen") if st.playing else ("⏸", "yellow")
            body.append([("  ", ), (icon, tone), ("  ", ), (t.name, "bold")])
            body.append([("     ", ), (t.artist, "white")])

            elapsed, total = mmss(self.position), mmss(t.duration)
            bw = max(6, inner - width_of(elapsed) - width_of(total) - 8)
            frac = (self.position / t.duration) if t.duration else 0.0
            body.append([("   ", ), (elapsed, "grey"), (" ", ),
                         (bar(frac, bw), "brightgreen"), (" ", ), (total, "grey")])

            flags = [("shuffle", st.shuffle), ("repeat %s" % st.repeat,
                                               st.repeat != "off"),
                     ("♥", t.loved)]
            segs = [("   ", )]
            for name, on in flags:
                segs.append((name + "  ", "brightcyan" if on else "grey"))
            body.append(segs)
            body.append([("   ", ),
                         ("vol %s %d%%" % (bar(st.volume / 100.0, 10, "▪", "·", "▪"),
                                           st.volume), "grey")])
        return box(width, height, self.title_for(PLAYER), body,
                   focused=self.focus == PLAYER)

    def title_for(self, panel):
        title = "%d %s" % (panel, TITLES[panel])
        lane = self.lanes.get(panel)
        if lane is None or not lane.filter:
            return title
        caret = "▏" if self.mode == FILTER and self.focus == panel else ""
        return "%s  /%s%s" % (title, lane.filter, caret)

    def panel_list(self, panel, width, height):
        lane = self.lanes[panel]
        view = max(1, height - 2)
        self._views[panel] = view
        lane.clamp(view)
        focused = self.focus == panel
        inner = width - 2
        body = []
        if not lane.items:
            hint = ("press / to search" if panel == SEARCH else "no playlists")
            body.append([("  " + hint, "grey")])
        else:
            # Column widths are settled once per panel, not per row - measuring
            # each row separately is what makes a list look ragged.
            if panel == PLAYLISTS:
                right_w = max([len(str(n)) for _, n in lane.items] + [3])
            else:
                right_w = max(10, min(26, inner // 3))
            right_w = min(right_w, max(0, inner - 8))
            for i in range(lane.scroll, min(len(lane.items), lane.scroll + view)):
                body.append(self.list_row(panel, lane.items[i], i, lane, inner,
                                          focused, right_w))
        footer = ""
        if len(lane.items) > view:
            footer = "%d/%d" % (lane.cursor + 1, len(lane.items))
        return box(width, height, self.title_for(panel), body, focused=focused,
                   footer=footer)

    def list_row(self, panel, item, index, lane, width, focused, right_w):
        if panel == PLAYLISTS:
            left, right = item[0], str(item[1]).rjust(right_w)
        else:
            left, right = item.name, truncate(item.artist, right_w)
        left_w = max(4, width - right_w - 3)
        selected = index == lane.cursor
        mark = "▸ " if selected else "  "
        left = pad(truncate(left, left_w), left_w)
        if selected:
            # A single padded span, so the highlight runs unbroken to the edge.
            style = ("brightcyan", "bold") if focused else ("cyan",)
            return [(pad(mark + left + " " + right, width),) + style]
        return [(mark,), (left,), (" ",), (right, "grey")]

    def panel_main(self, width, height):
        if self.help:
            return self.panel_help(width, height)
        lane = self.lanes[TRACKS]
        view = max(1, height - 2)
        self._views[TRACKS] = view
        lane.clamp(view)
        focused = self.focus == TRACKS
        body = []
        if not lane.items:
            body.append([("  nothing to show", "grey")])
        else:
            inner = width - 2
            index_w = len(str(len(lane.items)))
            right_w = min(max(12, inner // 3), max(0, inner - index_w - 12))
            left_w = max(6, inner - right_w - index_w - 4)
            for i in range(lane.scroll, min(len(lane.items), lane.scroll + view)):
                t = lane.items[i]
                selected = i == lane.cursor
                playing = bool(self.state.track.pid) and t.pid == self.state.track.pid
                num = str(i + 1).rjust(index_w)
                mark = "▸" if selected else ("▶" if playing else " ")
                name = pad(truncate(t.name, left_w), left_w)
                artist = truncate(t.artist, right_w)
                if selected:
                    style = ("brightcyan", "bold") if focused else ("cyan",)
                    text = "%s %s %s %s" % (mark, num, name, artist)
                    body.append([(pad(text, inner),) + style])
                elif playing:
                    body.append([("%s %s %s %s" % (mark, num, name, artist),
                                  "brightgreen")])
                else:
                    body.append([("%s %s " % (mark, num), "grey"), (name,),
                                 (" ",), (artist, "grey")])
        footer = ""
        if len(lane.items) > view:
            footer = "%d/%d" % (lane.cursor + 1, len(lane.items))
        title = "4 %s" % truncate(self.main_title, max(8, width - 24))
        if lane.filter:
            caret = "▏" if self.mode == FILTER and focused else ""
            title += "  /%s%s" % (lane.filter, caret)
        return box(width, height, title, body, focused=focused, footer=footer)

    def panel_help(self, width, height):
        inner = width - 2
        if inner >= 118:
            columns = [HELP_LEFT, HELP_RIGHT, HELP_COMMANDS]
        elif inner >= 76:
            columns = [HELP_LEFT, HELP_RIGHT + [("", "")] + HELP_COMMANDS]
        else:
            columns = [HELP_LEFT + HELP_RIGHT + HELP_COMMANDS]
        col_w = inner // len(columns)

        def cell(entry, key_w):
            key, what = entry
            if not key and not what:
                return [(" " * col_w,)]
            if not what:                       # a section heading
                return [("  " + pad(key, col_w - 2), "yellow", "bold")]
            return [("  ",), (pad(key, key_w), "brightcyan", "bold"),
                    (pad(truncate(what, col_w - key_w - 3), col_w - key_w - 2),)]

        # Each column sizes its own key field - the command names are far longer
        # than the motion keys, and a shared width either wraps them or wastes
        # half the motion column.
        key_ws = [min(max(len(k) for k, _ in col) + 2, col_w // 2 + 4)
                  for col in columns]

        body = [[]]
        for i in range(max(len(c) for c in columns)):
            segs = []
            for col, key_w in zip(columns, key_ws):
                segs += cell(col[i], key_w) if i < len(col) else [(" " * col_w,)]
            body.append(segs)
        body.append([])
        body.append([("  press ", "grey"), ("?", "bold"), (" or ", "grey"),
                     ("esc", "bold"), (" to close", "grey")])
        return box(width, height, "Keys", body, focused=False)

    def key_bar(self, cols):
        # The bottom line doubles as vim's command line: `/` and `:` prompts are
        # echoed here, and a half-typed count or prefix is shown on the right.
        if self.mode == FILTER:
            return row([("/", "brightcyan", "bold"), (self.buffer,),
                        ("▏", "brightcyan")], cols)
        if self.mode == COMMAND:
            segs = [(":", "brightcyan", "bold"), (self.buffer,),
                    ("▏", "brightcyan")]
            if self._completions:
                segs.append(("   " + "  ".join(self._completions), "grey"))
            return row(segs, cols)
        if self.message and time.monotonic() < self.message_until:
            return row([(" " + self.message, "yellow")], cols)

        if self.help:
            pairs = [("?/esc", "close help")]
        else:
            pairs = [("space", "play/pause"), ("j/k", "move"), ("h/l", "panel"),
                     ("enter", "play"), ("/", "filter"), (":", "command"),
                     ("?", "help"), ("q", "quit")]
        segs = [(" ",)]
        for key, what in pairs:
            segs.append((key, "brightcyan", "bold"))
            segs.append((" " + what + "   ", "grey"))

        # Echo any half-finished vim input, as vim shows it in the corner.
        hint = self.count + self.pending.replace("ctrl-", "^")
        if hint:
            left = row(segs, max(0, cols - len(hint) - 1))
            return left + row([(hint, "yellow", "bold"), (" ",)], len(hint) + 1)
        return row(segs, cols)
    # ------------------------------------------------------------- keys ----
    #
    # A small vim state machine. Digits accumulate a count, `g` / `z` / `ctrl-w`
    # are pending prefixes waiting for a second key, and `/` and `:` switch into
    # line-editing modes. Anything not claimed by the vim layer falls through to
    # the player controls.
    #
    # Horizontal motions (`w`, `b`, `e`, `f<char>`, `0`, `$`) are deliberately
    # absent: the panels are one-dimensional lists, so there is nothing to move
    # across. That frees `f` for favourite and `s` / `r` for shuffle / repeat.

    def handle(self, key):
        if key == "ctrl-c":
            self.running = False
            return
        if self.mode == FILTER:
            return self.filter_key(key)
        if self.mode == COMMAND:
            return self.command_key(key)
        if self.help:
            if key in ("?", "esc", "q", "enter"):
                self.help = False
            return
        self.normal_key(key)

    # -- normal mode ---------------------------------------------------------

    def normal_key(self, key):
        if self.pending:
            return self.pending_key(key)
        if key.isdigit() and (key != "0" or self.count):
            self.count += key
            return
        if key in ("g", "z", "ctrl-w"):
            self.pending = key
            return
        count = self.take_count()
        if self.motion_key(key, count):
            return
        if self.player_key(key, count):
            return
        self.mode_key(key)

    def pending_key(self, key):
        prefix, self.pending = self.pending, ""
        count = self.take_count()
        lane, view = self.focused_lane()
        if prefix == "g":
            if key == "g" and lane:
                # `gg` goes to the top, `<count>gg` to that item.
                lane.goto(count - 1 if count else 0, view)
        elif prefix == "z":
            if key in ("z", "t", "b") and lane:
                lane.reposition("z" + key, view)
        elif prefix == "ctrl-w":
            if key in ("h", "left"):
                self.focus_column(left=True)
            elif key in ("l", "right"):
                self.focus_column(left=False)
            elif key in ("j", "down"):
                self.focus_stack(1)
            elif key in ("k", "up"):
                self.focus_stack(-1)
            elif key in ("1", "2", "3", "4"):
                self.focus = int(key)

    def motion_key(self, key, count):
        lane, view = self.focused_lane()
        if lane is None:
            return key in ("j", "k", "down", "up", "G", "H", "M", "L")
        n = count or 1
        if key in ("j", "down"):
            self.after_move(lane.move(n, view))
        elif key in ("k", "up"):
            self.after_move(lane.move(-n, view))
        elif key == "G":
            self.after_move(lane.goto(count - 1 if count else len(lane.items) - 1,
                                      view))
        elif key in ("ctrl-d", "pagedown"):
            self.after_move(lane.move(max(1, view // 2) * n, view))
        elif key in ("ctrl-u", "pageup"):
            self.after_move(lane.move(-max(1, view // 2) * n, view))
        elif key == "ctrl-f":
            self.after_move(lane.move(view * n, view))
        elif key == "ctrl-b":
            self.after_move(lane.move(-view * n, view))
        elif key == "}":
            self.after_move(lane.move(10 * n, view))
        elif key == "{":
            self.after_move(lane.move(-10 * n, view))
        elif key in ("H", "M", "L"):
            self.after_move(lane.screen_row(key, view))
        elif key == "home":
            self.after_move(lane.goto(0, view))
        elif key == "end":
            self.after_move(lane.goto(len(lane.items) - 1, view))
        else:
            return False
        return True

    def after_move(self, moved):
        if moved and self.focus == PLAYLISTS:
            self.queue_playlist_load()

    def player_key(self, key, count):
        n = count or 1
        if key == " ":
            self.act(music.toggle)
        elif key in ("n", ">", "ctrl-n"):
            self.act(music.next_track)
        elif key in ("p", "<", "ctrl-p"):
            self.act(music.prev_track)
        elif key in ("right", "]"):
            self.act(music.seek, 10 * n, True)
        elif key in ("left", "["):
            self.act(music.seek, -10 * n, True)
        elif key in ("+", "="):
            self.act(music.nudge_volume, 5 * n)
        elif key in ("-", "_"):
            self.act(music.nudge_volume, -5 * n)
        elif key == "s":
            self.act(music.toggle_shuffle)
        elif key == "r":
            self.act(music.cycle_repeat, self.state.repeat)
        elif key == "f":
            self.act(music.toggle_loved)
        else:
            return False
        return True

    def mode_key(self, key):
        if key == "q":
            self.running = False
        elif key == "?":
            self.help = True
        elif key == "enter":
            self.activate()
        elif key == "tab":
            self.focus = PANELS[(PANELS.index(self.focus) + 1) % len(PANELS)]
        elif key == "shift-tab":
            self.focus = PANELS[(PANELS.index(self.focus) - 1) % len(PANELS)]
        elif key == "h":
            self.focus_column(left=True)
        elif key == "l":
            self.focus_column(left=False)
        elif key == "/":
            self.mode, self.buffer = FILTER, ""
        elif key == ":":
            self.mode, self.buffer = COMMAND, ""
        elif key == "S":
            self.mode, self.buffer = COMMAND, "search "
        elif key == "*":
            self.search_under_cursor()
        elif key == "ctrl-o":
            self.pop_view()
        elif key == "R":
            self.reload()
        elif key == "esc":
            self.count, self.pending = "", ""
            lane, view = self.focused_lane()
            if lane is not None and lane.filter:
                lane.apply_filter("")
                lane.clamp(view)

    # -- focus ---------------------------------------------------------------

    def focused_lane(self):
        lane = self.lanes.get(self.focus)
        return lane, self._views.get(self.focus, 10)

    def focus_column(self, left):
        """`h` / `l` cross between the left stack and the main panel."""
        if left:
            if self.focus == TRACKS:
                self.focus = self._last_left
        else:
            if self.focus != TRACKS:
                self._last_left = self.focus
                self.focus = TRACKS

    def focus_stack(self, delta):
        stack = (PLAYER, PLAYLISTS, SEARCH)
        if self.focus not in stack:
            self.focus = PLAYLISTS
            return
        i = max(0, min(stack.index(self.focus) + delta, len(stack) - 1))
        self.focus = self._last_left = stack[i]

    def take_count(self):
        count, self.count = self.count, ""
        return int(count) if count else 0

    # -- filter and command line ---------------------------------------------

    def filter_key(self, key):
        lane, view = self.focused_lane()
        if key == "esc":
            self.mode = NORMAL
            if lane is not None:
                lane.apply_filter("")
                lane.clamp(view)
        elif key == "enter":
            self.mode = NORMAL
        elif key == "backspace":
            self.buffer = self.buffer[:-1]
            if lane is not None:
                lane.apply_filter(self.buffer)
                lane.clamp(view)
        elif len(key) == 1 and key.isprintable():
            self.buffer += key
            if lane is not None:
                lane.apply_filter(self.buffer)
                lane.clamp(view)

    def command_key(self, key):
        if key == "esc":
            self.mode, self.buffer, self._completions = NORMAL, "", []
            return
        if key != "tab":
            self._completions = []
        if key == "tab":
            self.complete_command()
        elif key == "backspace":
            if not self.buffer:
                self.mode = NORMAL
            self.buffer = self.buffer[:-1]
        elif key == "enter":
            line, self.buffer = self.buffer, ""
            self.mode, self._completions = NORMAL, []
            self.run_command(line.strip())
        elif len(key) == 1 and key.isprintable():
            self.buffer += key

    def complete_command(self):
        """Tab on the `:` line: complete a command, or list what would match."""
        if " " in self.buffer:
            return                              # only the command word completes
        stem = self.buffer
        names = sorted(n for n in self.COMMANDS if n.startswith(stem))
        if not names:
            self.notify("no command starts with %r" % stem)
            return
        # Extend to the longest prefix every candidate shares.
        common = names[0]
        for n in names[1:]:
            while not n.startswith(common):
                common = common[:-1]
        self.buffer = common
        self._completions = names if len(names) > 1 else []

    def run_command(self, line):
        if not line:
            return
        if line.isdigit():                     # `:42` jumps to item 42
            lane, view = self.focused_lane()
            if lane is not None:
                lane.goto(int(line) - 1, view)
                self.after_move(True)
            return
        name, _, rest = line.partition(" ")
        rest = rest.strip()
        handler = self.COMMANDS.get(name)
        if handler is None:
            self.notify("not a command: %s  (try :help)" % name)
            return
        try:
            handler(self, rest)
        except MusicError as e:
            self.notify(str(e))

    def cmd_quit(self, rest):
        self.running = False

    def cmd_search(self, rest):
        if not rest:
            self.notify("usage: :search <query>")
            return
        self.notify("searching…", 30)

        def done(hits, err):
            if err:
                self.notify(str(err))
                return
            self.message = ""
            lane = self.lanes[SEARCH]
            lane.set_items(hits)
            lane.cursor = lane.scroll = 0
            self.push_view()
            self.show_main("Search: %s" % rest, hits)
            self.focus = self._last_left = SEARCH
            if not hits:
                self.notify("nothing in your library matches that")

        self.bus.submit("search", lambda: music.search(rest), done)

    def cmd_play(self, rest):
        if not rest:
            self.act(music.play)
            return

        def done(hits, err):
            if err:
                self.notify(str(err))
            elif not hits:
                self.notify("nothing in your library matches that")
            else:
                self.act(music.play_track, hits[0].pid)
                self.notify("playing %s" % hits[0].label)

        self.bus.submit("playquery", lambda: music.search(rest), done)

    def cmd_playlist(self, rest):
        if not rest:
            self.notify("usage: :pl <playlist>")
            return
        self.act(music.play_playlist, rest)
        self.notify("playing %s" % rest)

    def cmd_vol(self, rest):
        if not rest:
            self.notify("volume %d" % self.state.volume)
        elif rest[0] in "+-":
            self.act(music.nudge_volume, int(rest))
        else:
            self.act(music.set_volume, int(rest))

    def cmd_seek(self, rest):
        if rest and rest[0] in "+-":
            self.act(music.seek, float(rest), True)
        elif rest:
            self.act(music.seek, float(rest))

    def cmd_shuffle(self, rest):
        if rest in ("on", "off"):
            self.act(music.set_shuffle, rest == "on")
        else:
            self.act(music.toggle_shuffle)

    def cmd_repeat(self, rest):
        if rest in music.REPEAT_MODES:
            self.act(music.set_repeat, rest)
        else:
            self.act(music.cycle_repeat, self.state.repeat)

    def cmd_fav(self, rest):
        self.act(music.toggle_loved)

    def cmd_pause(self, rest):
        self.act(music.pause)

    def cmd_next(self, rest):
        self.act(music.next_track)

    def cmd_prev(self, rest):
        self.act(music.prev_track)

    def cmd_filter(self, rest):
        lane, view = self.focused_lane()
        if lane is not None:
            lane.apply_filter(rest)
            lane.clamp(view)

    def cmd_reload(self, rest):
        self.reload()

    def cmd_help(self, rest):
        self.help = True

    COMMANDS = {
        "q": cmd_quit, "quit": cmd_quit, "qa": cmd_quit, "x": cmd_quit,
        "search": cmd_search, "s": cmd_search, "find": cmd_search,
        "play": cmd_play, "pl": cmd_playlist, "playlist": cmd_playlist,
        "vol": cmd_vol, "volume": cmd_vol, "seek": cmd_seek,
        "shuffle": cmd_shuffle, "repeat": cmd_repeat,
        "fav": cmd_fav, "love": cmd_fav, "pause": cmd_pause,
        "next": cmd_next, "prev": cmd_prev,
        "filter": cmd_filter, "f": cmd_filter, "reload": cmd_reload,
        "help": cmd_help, "h": cmd_help,
    }

    # -- actions -------------------------------------------------------------

    def search_under_cursor(self):
        """vim `*`: search the library for the artist under the cursor."""
        lane, _ = self.focused_lane()
        item = lane.selected if lane else None
        artist = getattr(item, "artist", "")
        if not artist:
            self.notify("no artist under the cursor")
            return
        self.cmd_search(artist)

    def push_view(self):
        self._history.append((self.main_title, self.lanes[TRACKS].all_items,
                              self.main_playlist))
        del self._history[:-20]

    def pop_view(self):
        """vim ctrl-o: step back to the previous main-panel view."""
        if not self._history:
            self.notify("no earlier view")
            return
        title, tracks, playlist = self._history.pop()
        self.main_title = ""          # force the cursor to reset
        self.show_main(title, tracks, playlist)

    def activate(self):
        if self.focus == PLAYLISTS:
            sel = self.lanes[PLAYLISTS].selected
            if sel:
                self.act(music.play_playlist, sel[0])
                self.notify("playing %s" % sel[0])
        elif self.focus in (SEARCH, TRACKS):
            track = self.lanes[self.focus].selected
            if not track:
                return
            if self.focus == TRACKS and self.main_playlist:
                self.act(music.play_track_in_playlist, track.pid,
                         self.main_playlist)
            else:
                self.act(music.play_track, track.pid)
            self.notify("playing %s" % track.label)


class WindowName:
    """Call the window `lazymusic` while the UI is up, then put the name back.

    tmux names a window after whatever is running in the foreground, and since
    the launcher is `python3 -m lazymusic` that shows up as `python`. A manual
    `rename-window` fixes it, but it also switches tmux's automatic renaming
    off for that window - so the previous setting is noted on the way in and
    restored on the way out, along with the old name if it had been set by
    hand. Outside tmux the same thing is done with the terminal's title escape.
    """

    NAME = "lazymusic"

    def __init__(self):
        self.in_tmux = bool(os.environ.get("TMUX"))
        self._name = ""
        self._auto = ""

    def __enter__(self):
        sys.stdout.write("\033]2;%s\007" % self.NAME)
        sys.stdout.flush()
        if self.in_tmux:
            self._name = self._tmux("display-message", "-p", "#W")
            self._auto = self._tmux("show-window-options", "-v", "automatic-rename")
            self._tmux("rename-window", self.NAME)
        return self

    def __exit__(self, *exc):
        if self.in_tmux:
            if self._auto == "off" and self._name:
                self._tmux("rename-window", self._name)
            else:
                self._tmux("set-window-option", "-u", "automatic-rename")
        sys.stdout.write("\033]2;\007")
        sys.stdout.flush()
        return False

    @staticmethod
    def _tmux(*args):
        """Best effort: a window name is never worth taking the player down."""
        try:
            p = subprocess.run(("tmux",) + args, capture_output=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return ""
        return p.stdout.decode("utf-8", "replace").strip()


def run_tui():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        from .cli import print_status
        print_status(music.status())
        return 0

    app = App()
    sys.stdout.write(ALT_ON + CUR_HIDE)
    sys.stdout.flush()
    try:
        with WindowName(), Keys() as keys:
            app.start()
            while app.running:
                app.render()
                for key in keys.poll(FRAME):
                    app.handle(key)
                    if not app.running:
                        break
                app.bus.drain()
                app.tick_loads()
                app.refresh()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(CUR_SHOW + ALT_OFF)
        sys.stdout.flush()
    return 0
