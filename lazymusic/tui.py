"""lazymusic - a lazygit-style panel UI for Apple Music.

Two stacked panels on the left (player, playlists) and a main panel on the
right. Tab or the number keys move focus; the focused panel gets a bright
border, and the main panel follows whatever the focused list is showing.

The main panel is the only one wide enough to read a track and its artist side
by side, so everything big goes there rather than into a column of its own: the
track list, search results, the cover, lyrics, the queue and the key sheet all
take it over in turn. `VIEW_LANE` maps each of those to the lane it scrolls.

Frames are composed as whole rows, and only the rows that changed since the
last frame are written - each placed with an absolute cursor move, inside a
synchronized update, so the display never flickers. Every panel border is
anchored the same way (see `fmt.box`), which is what keeps the layout standing
when a title or a lyric is in a script the terminal measures differently.
"""

import codecs
import math
import os
import queue
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
import unicodedata

from . import art, lyrics, music
from .fmt import (Line, Tab, box, clean, fg, mmss, pad, row, truncate, widest,
                  width_of, wrap)
from .osa import MusicError

ALT_ON, ALT_OFF = "\033[?1049h", "\033[?1049l"
CUR_HIDE, CUR_SHOW = "\033[?25l", "\033[?25h"
CLR_EOL, CLR_ALL = "\033[K", "\033[2J"
# Autowrap off: a row the terminal draws wider than we measured must be clipped
# at the right edge, not wrapped onto the next row - wrapping on the bottom row
# scrolls the whole screen and takes the top border with it.
WRAP_OFF, WRAP_ON = "\033[?7l", "\033[?7h"
# Synchronized output (DEC mode 2026): the terminal holds the frame until it is
# complete. Terminals that do not know the mode ignore it.
SYNC_ON, SYNC_OFF = "\033[?2026h", "\033[?2026l"

SELECTED_BG = "48;5;236"   # the band behind the cursor row in a focused panel
EQ_BARS = "▁▂▃▄▅▆▇"         # the little equaliser beside whatever is playing
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Bus jobs worth a spinner on the key bar; the status poll is not one of them.
SLOW_JOBS = ("search", "tracks", "playlists", "playquery", "lyrics", "queue")

FRAME = 0.2       # seconds between repaints
STATE_PLAYING = 1.0  # seconds between state reads while playing
STATE_IDLE = 3.0     # ...and while paused or stopped, where nothing moves
LOAD_DELAY = 0.18  # settle time before loading the highlighted playlist
HANDOFF = 1.5      # seconds before a track ends to hand playback to the queue
CACHE_TTL = 60.0   # seconds a cached playlist stays fresh; `R` clears it early

PLAYER, PLAYLISTS, TRACKS = 1, 2, 3
NORMAL, FILTER, COMMAND = "normal", "filter", "command"
PANELS = (PLAYER, PLAYLISTS, TRACKS)
TITLES = {PLAYER: "Player", PLAYLISTS: "Playlists", TRACKS: "Tracks"}

PLAYER_HEIGHT = 7  # 5 content rows plus two borders

# What the main panel is currently showing. A cover, a lyric and a queue all
# want the full width of the right-hand column, and the left stack is already
# three boxes deep, so these take the main panel over rather than adding a
# fifth box to a layout that is out of room. `help` has always worked this way.
V_TRACKS, V_HELP, V_ART, V_LYRICS, V_QUEUE = (
    "tracks", "help", "art", "lyrics", "queue")

# The lane each view scrolls, keyed the same way `lanes` and `_views` are.
# Cover art and the help sheet have nothing to scroll and so appear in neither.
VIEW_LANE = {V_TRACKS: TRACKS, V_LYRICS: "lyrics", V_QUEUE: "queue",
             V_HELP: "help"}
VIEW_TITLE = {V_ART: "Now Playing", V_LYRICS: "Lyrics", V_QUEUE: "Up Next"}

# Cover size. Each half block is one character cell, so how big a cover looks on
# screen is set by the font, not by us - at a large font size a big cover is a
# handful of enormous squares that read as a broken photo. Kept small it reads
# as deliberate pixel art instead, which is the whole trick. LAZYMUSIC_ART_ROWS
# overrides the cap for anyone on a small font with cells to spare.
ART_MIN = 4        # below this there is no picture left to see
ART_MAX = 12       # above this it stops looking intentional
ART_GUTTER = 2     # blank columns between the cover and the details beside it
ART_TEXT_MIN = 18  # if the details cannot get this much width, drop the cover
CACHE_KEEP = 24   # covers and lyrics held in memory before the oldest is dropped
MINI_ART_TEXT = 22  # the player's mini cover needs this much width left beside it


def art_rows_for(height, inner):
    """How tall the cover should be in this panel, or 0 if it does not fit."""
    cap = ART_MAX
    override = os.environ.get("LAZYMUSIC_ART_ROWS", "").strip()
    if override.isdigit():
        cap = max(ART_MIN, int(override))
    # Two rows of padding above and below, and the details need room to its side.
    beside = (inner - 2 - ART_GUTTER - ART_TEXT_MIN) // 2
    rows = min(cap, height - 4, beside)
    return rows if rows >= ART_MIN else 0

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
    ("ctrl-w 1-3", "focus by number"),
    ("tab", "cycle panels"),
    ("", ""),
    ("VIEWS", ""),
    ("a", "cover art"),
    ("y", "lyrics"),
    ("u", "up next"),
    ("esc", "back to tracks"),
    ("", ""),
    ("QUEUE", ""),
    ("A", "queue this track"),
    ("D", "remove from queue"),
    ("", ""),
    ("LYRICS", ""),
    ("enter", "sing from this line"),
    ("j / k", "scroll (stops following)"),
]

HELP_RIGHT = [
    ("SEARCH", ""),
    ("/", "filter this panel"),
    ("esc", "clear filter"),
    ("S", "search → panel 3"),
    ("*", "search artist under cursor"),
    ("ctrl-o", "back to previous view"),
    ("R", "reload library from Music"),
    ("ctrl-l", "redraw the screen"),
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
    (":art :lyrics :upnext", ""),
    (":art test", "which glyphs render"),
    (":upnext clear", "empty the queue"),
    (":sleep 30 | off", "pause in 30 minutes"),
    (":offset +0.5", "lyrics later; bare resets"),
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
        # A read can end in the middle of a multi-byte character (typing Hindi
        # into a filter does this); the decoder holds the partial bytes over.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

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
        return self.decode(self._decoder.decode(os.read(self.fd, 1024)))

    def decode(self, data):
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
        if data[i + 1:i + 2] == "O" and i + 2 < len(data):
            # SS3: what arrows and home/end send in application cursor mode.
            # Read as two plain keys, `ESC O A` would queue a track (`A`).
            return {"A": "up", "B": "down", "C": "right", "D": "left",
                    "H": "home", "F": "end"}.get(data[i + 2], "unknown"), 3
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
    """Runs Music.app calls on worker threads so the UI never blocks on them.

    Every osascript round trip costs 70-150ms. Doing that on the draw loop meant
    the interface froze for a sizeable slice of every second, which is what made
    keystrokes feel like they arrived late. Jobs go out on a queue and results
    come back on another, applied by the UI thread in `drain` - so callbacks
    still run single-threaded and nothing needs a lock.

    Slow work gets a lane of its own. A lyrics lookup can sit on the network for
    several seconds, and on a single worker every play/pause pressed in that
    time waited behind it; the cover is the same story at a smaller scale. Jobs
    are routed by the prefix of their key, so callers never have to say.
    """

    LANES = {"lyrics": "net", "art": "art"}

    def __init__(self):
        self.results = queue.Queue()
        self.inflight = set()
        self._lanes = {}

    def submit(self, key, call, then=None):
        """Queue `call`. A key already in flight is dropped, not stacked up."""
        if key in self.inflight:
            return False
        self.inflight.add(key)
        self._lane(self.LANES.get(key.split(":")[0], "music")).put(
            (key, call, then))
        return True

    def _lane(self, name):
        jobs = self._lanes.get(name)
        if jobs is None:
            jobs = self._lanes[name] = queue.Queue()
            threading.Thread(target=self._loop, args=(jobs,), daemon=True,
                             name="lazymusic-%s" % name).start()
        return jobs

    def _loop(self, jobs):
        while True:
            key, call, then = jobs.get()
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

    def loading(self, names=SLOW_JOBS):
        """True while a job the user is waiting on is still out."""
        return any(key.split(":")[0] in names for key in self.inflight)


def fold(text):
    """`text` lower-cased with its accents dropped, for forgiving matching."""
    text = text.casefold()
    if text.isascii():
        return text
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


class Lane:
    """One scrollable list: the rows, which is selected, and what is on screen.

    `all_items` is everything; `items` is what a `/` filter has left visible.
    """

    def __init__(self, label=lambda item: str(item)):
        self.all_items = []
        self._folded = None       # lower-cased, accent-free labels, built lazily
        self.items = []
        self.cursor = 0
        self.scroll = 0
        self.filter = ""
        self.label = label

    def set_items(self, items):
        self.all_items = list(items)
        self._folded = None
        self.apply_filter(self.filter)

    def apply_filter(self, text):
        """Keep the items matching every word of `text`, in any order.

        Matching ignores case and accents, so `beyonce` finds Beyoncé and
        `satie gymno` finds Gymnopédie No. 1 by Erik Satie.
        """
        self.filter = text
        words = fold(text).split()
        if not words:
            self.items = list(self.all_items)
            return
        if self._folded is None or len(self._folded) != len(self.all_items):
            self._folded = [fold(self.label(i)) for i in self.all_items]
        self.items = [item for item, hay in zip(self.all_items, self._folded)
                      if all(w in hay for w in words)]
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
            TRACKS: Lane(lambda t: t.name + " " + t.artist),
            "lyrics": Lane(lambda line: line[1]),
            "queue": Lane(lambda t: t.name + " " + t.artist),
            "help": Lane(lambda segs: ""),
        }
        self.mode = NORMAL
        self.buffer = ""          # text typed after `/` or `:`
        self.count = ""           # vim count prefix, e.g. the 12 in `12j`
        self.pending = ""         # `g`, `z` or `ctrl-w` awaiting a second key
        self._history = []        # previous main-panel views, for ctrl-o
        self._last_left = PLAYLISTS
        self.view = V_TRACKS
        self.main_title = "Tracks"
        self.main_playlist = ""   # playlist the main panel is showing, if any
        self.message = ""
        self.message_kind = "info"  # "info", "ok" or "error": sets the colour
        self.message_until = 0.0
        self.running = True
        self._views = {}          # panel -> visible row count, set while drawing
        self._origin = {}         # panel -> screen column, set while drawing
        self._last_state = 0.0
        self._pos_at = 0.0
        self._pending = None      # (playlist_name, load_after_monotonic)
        self._cache = {}          # playlist name -> (fetched_at, tracks)
        self._screen = []         # the rows last written, for diffing
        self._size = None         # terminal size those rows were drawn for
        self._title_sent = None   # the window title last written
        self._completions = []    # tab-completion candidates on the `:` line
        self._art = {}            # (persistent ID, rows, glyph) -> cover rows
        self._accent = {}         # persistent ID -> (r, g, b) or None
        self._art_src = ""        # track whose artwork is saved in scratch (art lane only)
        self._lyric_delay = 0.0   # seconds the lyric runs behind the music (:offset)
        self._lyric_layout = (None, [], [])  # (key, wrapped rows, first row per line)
        self._sleep_at = 0.0      # monotonic time the sleep timer pauses at
        self._lyrics = {}         # persistent ID -> Lyrics
        self._lyric_pid = ""      # track the lyrics lane is currently holding
        self._follow = True       # lyrics scroll with playback until you scroll
        self._queue_kind = "none"  # "queue", "playlist" or "none" in Up Next
        self._help_width = -1     # width the key sheet was last composed for
        self._glyph_sample = False  # `:art test` shows the glyph families
        self._queue_items = []      # what is sitting in the queue playlist
        self._handoff_until = 0.0   # suppresses a second handoff on one change
        self._shuffle_restore = False  # shuffle we switched off for the queue
        self._restore_at = 0.0      # next attempt at putting shuffle back
        self._restore_tries = 0
        # sips needs somewhere to put the decoded cover, and the raw artwork has
        # to land on disk before it can be decoded at all. One directory for the
        # session, emptied on the way out.
        self._scratch = tempfile.mkdtemp(prefix="lazymusic-")
        self.bus = Bus()

    def close(self):
        shutil.rmtree(self._scratch, ignore_errors=True)

    # ------------------------------------------------------------ state ----

    def start(self):
        # The track list can only be asked for once the playlists have landed,
        # so it is chained off that rather than fired alongside it.
        self.refresh(force=True)
        self.load_playlists(then=lambda: self.queue_playlist_load(immediate=True))
        # The handoff has to know what is queued even if the panel is never
        # opened, and the queue playlist survives between sessions.
        self.refresh_queue_items()

    def load_playlists(self, then=None):
        def done(items, err):
            if err:
                self.fail(err)
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
            self.fail(err)
            return
        before_pid = self.state.track.pid
        before_playlist = self.state.playlist
        self.state = state
        self._pos_at = time.monotonic()
        if state.track.pid != before_pid:
            self.on_track_change(before_pid, before_playlist)

    def on_track_change(self, before_pid="", before_playlist=""):
        """Whatever the main panel is showing now belongs to a different song.

        The cover is not fetched here: the draw path asks for it by height, and
        it cannot know the height until it knows how big the panel came out.
        """
        self._follow = True
        self._lyric_delay = 0.0
        # A queued track that has played has done its job. Dropping it here is
        # what makes the queue drain as it goes rather than replaying from the
        # top the next time it starts. Queued first, so the refresh below reads
        # the list after the removal.
        if before_playlist == music.QUEUE and before_pid:
            self.bus.submit("drain",
                            lambda: music.queue_remove_pid(before_pid),
                            self._drained)
        if self.view in (V_LYRICS, V_ART) and lyrics.mode() != "off":
            self.want_lyrics()
        if self.view == V_QUEUE:
            self.want_queue()
        else:
            self.refresh_queue_items()

    def _drained(self, _value, err):
        if err:
            self.fail(err)

    @property
    def position(self):
        if not self.state.playing:
            return self.state.position
        return min(self.state.track.duration or 1e9,
                   self.state.position + (time.monotonic() - self._pos_at))

    def notify(self, text, seconds=2.5, kind="info"):
        self.message = text
        self.message_kind = kind
        self.message_until = time.monotonic() + seconds

    def fail(self, err):
        """Put an error on the key bar, in red and for long enough to read."""
        self.notify(str(err), 4.0, "error")

    @property
    def tone(self):
        """The accent colour: picked from the cover when there is one.

        It paints the progress bar, the playing marker and the line being sung,
        so the whole interface takes on the colours of each song as it plays.
        """
        rgb = self._accent.get(self.state.track.pid)
        return fg(*rgb) if rgb else "brightgreen"

    def act(self, fn, *args):
        """Send a command, then re-read state. Both run on the worker thread.

        Commands are keyed by identity so mashing a key queues one of each
        rather than a hundred; the queue is FIFO, so ordering still holds.
        """
        key = "act:%s" % getattr(fn, "__name__", str(fn))
        self.bus.submit(key, lambda: fn(*args), self._acted)

    def _acted(self, _value, err):
        if err:
            self.fail(err)
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
                self.fail(err)
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
        # Anything that fills the main panel with a list is asking for that list
        # to be looked at, so a cover or a lyric sitting over it steps aside.
        if self.view in (V_ART, V_LYRICS, V_QUEUE):
            self.view = V_TRACKS
        lane = self.lanes[TRACKS]
        if title != self.main_title:
            lane.cursor = lane.scroll = 0
            lane.filter = ""
        lane.set_items(tracks)
        self.main_title = title
        self.main_playlist = playlist

    # ------------------------------------------------- now-playing views ----

    def set_view(self, view):
        """Switch the main panel to `view`, or back to the track list if it is
        already there - so every view key is its own toggle, as `?` always was."""
        self.view = V_TRACKS if self.view == view else view
        if view == V_ART:
            self._glyph_sample = False
        if self.view == V_LYRICS:
            self._follow = True
            self.want_lyrics()
        elif self.view == V_QUEUE:
            self.want_queue()
        # Focus follows, so j/k scroll what you just asked to look at. The cover
        # does not scroll, so moving focus there would only strand the cursor.
        if self.view in (V_LYRICS, V_QUEUE):
            self.focus = TRACKS

    @staticmethod
    def _trim(cache):
        """Keep a fetch cache from growing for the whole life of the session."""
        while len(cache) > CACHE_KEEP:
            cache.pop(next(iter(cache)))

    def want_art(self, rows):
        """Fetch and render the current cover at `rows` high, once.

        Called from the draw path, which is the only place the panel height is
        known. `submit` drops a duplicate key, so asking every frame queues one
        job; an empty result is cached too, so a track with no cover is not
        re-fetched on every repaint. The artwork is pulled out of Music once per
        track and every size is drawn from that one copy.
        """
        if not art.color_enabled():
            return
        pid = self.state.track.pid
        # The glyph decides how many pixels a row holds, so it belongs in the
        # key: switching it must not hand back the old rendering.
        mode = art.glyph_mode()
        key = (pid, rows, mode)
        if not pid or key in self._art:
            return
        source = os.path.join(self._scratch, "cover")
        decoded = os.path.join(self._scratch, "cover-%d.bmp" % rows)

        def job():
            # Runs on the art lane, which is serial, so `_art_src` needs no lock.
            if self._art_src != pid:
                saved = music.save_artwork(source, pid)
                if saved is music.MOVED:
                    return None             # asked again on the next frame
                self._art_src = pid if saved else ""
                if not saved:
                    return [], None
            return art.cover(source, rows, decoded, mode)

        def done(value, err):
            if err:
                self.fail(err)
                value = ([], None)      # cache the miss rather than retry forever
            if value is None:
                return
            cover, colour = value
            self._art[key] = cover
            self._trim(self._art)
            if pid not in self._accent or colour:
                self._accent[pid] = colour
                self._trim(self._accent)

        self.bus.submit("art:%d" % rows, job, done)

    def want_lyrics(self):
        track = self.state.track
        if not track.pid or track.pid in self._lyrics:
            return

        def done(value, err):
            if err:
                # A lyric that cannot be found is not worth an error row; note
                # it on the key bar and cache the miss so it is asked for once.
                self._lyrics[track.pid] = lyrics.Lyrics()
                self.notify("lyrics: %s" % err, 3.0, "error")
            else:
                self._lyrics[track.pid] = value
            self._trim(self._lyrics)

        self.bus.submit("lyrics", lambda: lyrics.for_track(track), done)

    def want_queue(self):
        """Refill Up Next.

        The lazymusic queue wins when it has anything in it; otherwise the panel
        falls back to showing what is left of the playlist now playing, which is
        the best guess available given Music.app publishes no queue of its own.
        """
        playlist = self.state.playlist

        def job():
            queued = music.queue_tracks()
            if queued:
                return ("queue", queued)
            if not playlist:
                return ("none", [])
            return ("playlist", music.playlist_tracks(playlist))

        def done(value, err):
            if err:
                self.fail(err)
                return
            kind, tracks = value
            self._queue_kind = kind
            if kind == "playlist":
                self._cache[playlist] = (time.monotonic(), tracks)
                tracks = self.after_current(tracks)
            lane = self.lanes["queue"]
            lane.set_items(tracks)
            lane.clamp(self._views.get("queue", 10))

        self.bus.submit("queue", job, done)

    def refresh_queue_items(self):
        """Re-read the queue playlist into memory.

        The handoff below needs to know what is queued whether or not the panel
        is open, so this is kept current rather than being fetched on demand at
        the moment a track ends, when there is no time to spare.
        """
        def done(value, err):
            if err:
                self.fail(err)
                return
            self._queue_items = list(value)
        self.bus.submit("queueitems", music.queue_tracks, done)

    def tick_queue(self):
        """Hand playback to the queue as the current track runs out.

        Music.app advances within whatever playlist is playing and offers no way
        to insert anything after the current track, so a queue can only take
        over by starting itself. Doing that a moment *before* the end rather
        than reacting after it means there is no silence and no wrong track in
        between - the last second of the outgoing song is cut, which nobody
        hears. The position used is the interpolated one, so watching for the
        end costs no extra round trip.

        Once the queue is playing, Music carries on through it natively and this
        stands down; `on_track_change` drains each track as it finishes.
        """
        now = time.monotonic()
        if now < self._handoff_until:
            return              # a handoff just fired; let the state catch up
        state = self.state
        # Shuffle goes back once playback has left the queue for something
        # else. Waiting for it to be *playing* is not fussiness: Music refuses
        # property writes while it has no current playlist, and that is exactly
        # where it lands when the queue runs out - so the write is held until
        # there is something for it to apply to.
        if (self._shuffle_restore and state.playing
                and state.playlist != music.QUEUE):
            self.restore_shuffle()
            return
        if not self._queue_items:
            return
        if not state.playing or state.playlist == music.QUEUE:
            return
        duration = state.track.duration
        if not duration or duration - self.position > HANDOFF:
            return
        self.start_queue()

    def start_queue(self):
        """Start the queue playlist, in order.

        It has to be `play playlist`: `current playlist` is read-only and
        playing a track object leaves the old playlist in place, so Music would
        carry on into that instead of through the queue, and nothing would drain.

        `play playlist` obeys shuffle, though, which would scramble a list whose
        entire point is its order - so shuffle steps aside while the queue runs
        and is put back when it is done.
        """
        if not self._queue_items:
            self.notify("nothing queued - press A on a track to queue it")
            return
        # Nothing else in here may act on the player state until the change has
        # been read back: for a second or two it still describes the old
        # playlist, which would look like the queue having been left - firing a
        # second handoff, or putting shuffle back on top of the one we just
        # turned off.
        self._handoff_until = time.monotonic() + 8.0
        stand_down = self.state.shuffle

        def job():
            if stand_down:
                music.set_shuffle(False)
            music.play_queue()
            # We cut in while Music is about to advance on its own, and the two
            # can collide and leave the queue loaded but stopped. One nudge
            # settles it; `play` on something already playing does nothing.
            time.sleep(0.4)
            if music.status().player != "playing":
                music.play()

        if stand_down:
            self._shuffle_restore = True
            self._restore_tries = 0
            self._restore_at = 0.0
        self.notify("up next: %s" % self._queue_items[0].label)
        self.bus.submit("startqueue", job, self._acted)

    RESTORE_TRIES = 12          # about a minute of trying, then let it go

    def sleep_left(self):
        """Time left on the sleep timer as `25m` or `40s`, or None if unset."""
        if not self._sleep_at:
            return None
        left = max(0, int(self._sleep_at - time.monotonic()))
        return "%dm" % ((left + 59) // 60) if left >= 60 else "%ds" % left

    def tick_sleep(self):
        """Pause when the sleep timer runs out."""
        if self._sleep_at and time.monotonic() >= self._sleep_at:
            self._sleep_at = 0.0
            if self.state.playing:
                self.act(music.pause)
            self.notify("sleep timer: paused. good night ☾", 6.0, "ok")

    def restore_shuffle(self):
        """Put shuffle back once the queue is no longer what is playing.

        Music refuses property writes while it has no current playlist, which is
        exactly the state it lands in when the queue runs out and playback
        stops. So the flag is only cleared once the write is confirmed, and the
        attempt is repeated at intervals until something is playing again. This
        is housekeeping rather than anything asked for, so a failure is quiet.
        """
        now = time.monotonic()
        if not self._shuffle_restore or now < self._restore_at:
            return
        self._restore_at = now + 4.0
        self._restore_tries += 1
        if self._restore_tries > self.RESTORE_TRIES:
            self._shuffle_restore = False       # give up rather than nag
            return

        def done(_value, err):
            if not err:
                self._shuffle_restore = False
                self._restore_tries = 0
        self.bus.submit("reshuffle", lambda: music.set_shuffle(True), done)

    def enqueue(self, track):
        """Add `track` to the queue playlist, creating it on first use."""
        def done(_value, err):
            if err:
                self.fail(err)
                return
            self.notify("queued %s" % track.label, kind="ok")
            self.after_queue_change(start_if_idle=True)
        # Keyed by track: `submit` drops a key already in flight, and queueing
        # several songs in quick succession must not lose all but the first.
        self.bus.submit("enqueue:%s" % track.pid,
                        lambda: music.queue_add(track.pid), done)

    def after_queue_change(self, start_if_idle=False):
        """Re-read the queue, and refresh the panel if it is the one showing."""
        def then(value, err):
            if err:
                self.fail(err)
                return
            self._queue_items = list(value)
            if self.view == V_QUEUE:
                self.want_queue()
            # With nothing playing there is no current track to wait for, so a
            # queued song starts now instead of sitting there until you notice.
            if start_if_idle and self.state.stopped and self._queue_items:
                self.start_queue()
        self.bus.submit("queueitems", music.queue_tracks, then)

    def dequeue(self):
        """Drop the highlighted track from the queue, by position."""
        if self._queue_kind != "queue":
            self.notify("nothing queued - press A on a track to queue it")
            return
        lane = self.lanes["queue"]
        if not lane.items:
            return
        track = lane.selected
        # The lane may be filtered, so its cursor is not the playlist position.
        index = lane.all_items.index(track) + 1

        def done(_value, err):
            if err:
                self.fail(err)
                return
            self.notify("removed %s" % track.label, kind="ok")
            self.after_queue_change()
        self.bus.submit("dequeue:%d" % index,
                        lambda: music.queue_remove(index), done)

    def after_current(self, tracks):
        """The tracks following the playing one, or all of them if it is absent."""
        pid = self.state.track.pid
        for i, track in enumerate(tracks):
            if track.pid == pid:
                return tracks[i + 1:]
        return list(tracks)

    # ------------------------------------------------------------- draw ----

    def compose(self, cols, rows):
        """Every screen row of a `cols` x `rows` frame, each anchored in place."""
        height = rows - 1                       # last row is the key bar
        left_w = max(30, min(46, cols * 38 // 100))
        right_w = cols - left_w
        player_h = min(PLAYER_HEIGHT, max(3, height - 5))
        self._origin = {PLAYER: 0, PLAYLISTS: 0, TRACKS: left_w}

        left = []
        left += self.panel_player(left_w, player_h)
        left += self.panel_list(PLAYLISTS, left_w, height - player_h)
        left = left[:height] + ["\033[1G" + " " * left_w] * max(0, height - len(left))
        right = self.panel_main(right_w, height)

        lines = [l + r for l, r in zip(left, right)]
        # The key bar is the one row not drawn as a box, so it is cleared to
        # the edge instead: a message can be shorter than the one before it.
        lines.append(self.key_bar(cols) + CLR_EOL)
        return lines[:rows]

    def render(self):
        cols, rows = shutil.get_terminal_size((90, 28))
        cols, rows = max(60, cols), max(14, rows)
        lines = self.compose(cols, rows)
        out = []
        if (cols, rows) != self._size:
            # A resize leaves the old frame smeared across the new grid.
            self._size, self._screen = (cols, rows), []
            out.append(CLR_ALL)
        title = self.window_title()
        if title != self._title_sent:
            self._title_sent = title
            out.append("\033]2;%s\007" % title)
        # Only rows that changed are sent, each placed by an absolute cursor
        # move. Nothing is ever written with a newline, so the bottom row can
        # never scroll the screen and eat the top border.
        for y, line in enumerate(lines):
            if y < len(self._screen) and self._screen[y] == line:
                continue
            out.append("\033[%d;1H%s" % (y + 1, line))
        self._screen = lines
        if not out:
            return                      # nothing changed; skip the repaint
        sys.stdout.write(SYNC_ON + "".join(out) + SYNC_OFF)
        sys.stdout.flush()

    def window_title(self):
        """What the terminal tab says: the song, so it can be seen from afar."""
        track = self.state.track
        if self.state.stopped or not track:
            return WindowName.NAME
        icon = "▶" if self.state.playing else "⏸"
        return clean("%s %s" % (icon, track.label))

    def frame(self, panel, width, height, title, body, footer="", focused=None):
        """A bordered panel, anchored at the column `compose` placed it in."""
        if focused is None:
            focused = self.focus == panel
        return box(width, height, title, body, focused=focused, footer=footer,
                   x=self._origin.get(panel))

    # -- little pieces shared by the panels -----------------------------------

    def spinner(self):
        return SPINNER[int(time.monotonic() * 10) % len(SPINNER)]

    def equaliser(self):
        """Three bouncing bars while playing, flat while paused.

        Music.app gives no audio levels, so this is decoration rather than a
        meter - but it says at a glance which row is the one playing.
        """
        if not self.state.playing:
            return "▁▁▁"
        now, top = time.monotonic(), len(EQ_BARS) - 1
        return "".join(EQ_BARS[int((math.sin(now * speed + phase) + 1) / 2 * top + 0.5)]
                       for speed, phase in ((7.1, 0.0), (9.7, 1.9), (5.3, 3.7)))

    def progress(self, width):
        """Elapsed time, a bar with a playhead, and the length, in `width` columns."""
        track = self.state.track
        elapsed, total = mmss(self.position), mmss(track.duration)
        bar_w = max(6, width - len(elapsed) - len(total) - 2)
        frac = (self.position / track.duration) if track.duration else 0.0
        head = int(round(min(1.0, max(0.0, frac)) * (bar_w - 1)))
        return [(elapsed, "grey"), (" ",), ("━" * head, self.tone),
                ("●", self.tone, "bold"), ("─" * (bar_w - head - 1), "grey"),
                (" ",), (total, "grey")]

    def flags(self):
        st, track = self.state, self.state.track
        repeat = "repeat" if st.repeat == "off" else "repeat " + st.repeat
        segs = [("⇄ shuffle", self.tone if st.shuffle else "grey"), ("  ",),
                ("↻ " + repeat, self.tone if st.repeat != "off" else "grey"),
                ("  ",),
                ("♥", "brightred") if track.loved else ("♡", "grey")]
        return segs

    def volume(self, cells=10):
        level = max(0, min(100, self.state.volume))
        filled = int(round(level / 100.0 * cells))
        segs = [("vol ", "grey"), ("▪" * filled, self.tone),
                ("·" * (cells - filled), "grey"), (" %d%%" % level, "grey")]
        left = self.sleep_left()
        if left is not None:
            segs.append(("   ☾ %s" % left, "yellow"))
        return segs

    def now_playing(self, width, compact=False):
        """The track details: five rows for the player, more for the cover view."""
        st, track = self.state, self.state.track
        icon, tone = ("▶", self.tone) if st.playing else ("⏸", "yellow")
        if compact:
            return [
                [(icon, tone), (" ",), (truncate(track.name, max(4, width - 6)), "bold"),
                 (" ",), (self.equaliser(), self.tone)],
                [("  ",), (track.artist, "white")],
                self.progress(width),
                self.flags(),
                self.volume(),
            ]
        names = wrap(track.name, width) or [""]
        if len(names) > 2:
            names = [names[0], truncate(" ".join(names[1:]), width)]
        # Singles are routinely tagged with the album named after the track, and
        # printing the same words twice just eats a row.
        album = "" if track.album.strip() == track.name.strip() else track.album
        out = [[(name, "bold")] for name in names]
        out += [[(track.artist, "white")], [(album, "grey")], [],
                [(icon + " ", tone)] + self.progress(width - 2),
                self.flags(), [], self.volume()]
        if self._queue_items:
            out += [[], [("next  ", "grey"), (self._queue_items[0].label, "white")]]
        return out

    # -- the left stack --------------------------------------------------------

    def mini_art_rows(self, inner, rows):
        """How tall the player's own little cover is, or 0 to leave it out."""
        if not art.color_enabled() or rows < 3:
            return 0
        if os.environ.get("LAZYMUSIC_MINI_ART", "").strip().lower() in ("0", "off", "no"):
            return 0
        if inner - (rows * 2 + 3) < MINI_ART_TEXT:
            return 0
        if self._art.get((self.state.track.pid, rows, art.glyph_mode())) == []:
            return 0                    # known to have no cover: take the room back
        return rows

    def panel_player(self, width, height):
        st = self.state
        inner, rows = width - 2, height - 2
        body = []
        if not st.running:
            body.append([("  ",), (self.spinner() + " Music.app is starting…", "grey")])
        elif st.stopped:
            body += [[], [("  ■  nothing playing", "grey")], [],
                     [("  pick a playlist and press ", "grey"), ("enter", "bold")]]
            if self._queue_items:
                body.append([("  or ", "grey"), (":upnext play", "bold"),
                             (" for the %d queued" % len(self._queue_items), "grey")])
        else:
            mini = self.mini_art_rows(inner, rows)
            cover = None
            if mini:
                cover = self._art.get((st.track.pid, mini, art.glyph_mode()))
                if cover is None:
                    self.want_art(mini)
            text_x = mini * 2 + 3 if mini else 2
            details = self.now_playing(inner - text_x - 1, compact=True)
            for i in range(rows):
                line = [(" ",)]
                if mini and cover:
                    line.append((cover[i],) if i < len(cover) else (" " * mini * 2,))
                elif mini and i == mini // 2:
                    line.append((pad(self.spinner().center(mini * 2), mini * 2), "grey"))
                line.append((Tab(text_x),))
                if i < len(details):
                    line += details[i]
                body.append(line)
        return self.frame(PLAYER, width, height, self.title_for(PLAYER), body)

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
            if self.bus.loading(("playlists",)):
                body.append([("  ",), (self.spinner() + " loading playlists…", "grey")])
            else:
                body.append([("  no playlists" if not lane.filter else "  no match", "grey")])
        else:
            # Column widths are settled once per panel, not per row - measuring
            # each row separately is what makes a list look ragged.
            count_w = max(len(str(n)) for _, n in lane.items)
            for i in range(lane.scroll, min(len(lane.items), lane.scroll + view)):
                body.append(self.list_row(lane.items[i], i == lane.cursor,
                                          focused, inner, count_w))
        footer = ""
        if len(lane.items) > view:
            footer = "%d/%d" % (lane.cursor + 1, len(lane.items))
        return self.frame(panel, width, height, self.title_for(panel), body,
                          footer=footer)

    def list_row(self, item, selected, focused, inner, count_w):
        """One playlist: a marker, its name, and its track count flush right."""
        name, count = item
        playing = name == self.state.playlist and not self.state.stopped
        count_x = inner - count_w
        name = truncate(name, max(4, count_x - 3))
        mark = ("▸", "brightcyan", "bold") if selected else (
            ("♪", self.tone) if playing else (" ",))
        if selected and focused:
            style = ("brightwhite", "bold")
        elif selected:
            style = ("cyan",)
        else:
            style = (self.tone, "bold") if playing else ()
        segs = [mark, (Tab(2),), (name,) + style, (Tab(count_x),),
                (str(count).rjust(count_w), "grey")]
        return Line(segs, (SELECTED_BG,)) if selected and focused else segs

    # -- the main panel ----------------------------------------------------------

    def panel_main(self, width, height):
        if self.view == V_HELP:
            return self.panel_help(width, height)
        if self.view == V_ART:
            return self.panel_art(width, height)
        if self.view == V_LYRICS:
            return self.panel_lyrics(width, height)
        if self.view == V_QUEUE:
            return self.panel_queue(width, height)
        return self.panel_tracks(width, height)

    def main_box(self, width, height, title, body, footer=""):
        """Draw the main panel, numbered and bordered like the others."""
        return self.frame(TRACKS, width, height,
                          "3 %s" % truncate(title, max(8, width - 24)), body,
                          footer=footer)

    def track_rows(self, lane, inner, view, focused):
        """Rows of a track list: number, title, artist and length, in columns.

        Each column starts at a `Tab`, so a title in a script the terminal
        measures differently from us can only ever shift its own column.
        """
        items = lane.items
        num_w = len(str(len(items)))
        longest = max((t.duration for t in items), default=0)
        dur_w = len(mmss(longest)) if longest else 0
        artist_w = max(10, min(28, inner // 3))
        name_x = num_w + 3
        dur_x = inner - dur_w if dur_w else inner
        artist_x = dur_x - artist_w - (2 if dur_w else 0)
        if artist_x - name_x < 10:          # cramped: the lengths go first
            dur_w, dur_x = 0, inner
            artist_x = inner - artist_w
        artist_w = dur_x - artist_x - (2 if dur_w else 0)
        name_w = max(4, artist_x - name_x - 2)
        playing_pid = "" if self.state.stopped else self.state.track.pid
        eq = self.equaliser()[1]
        out = []
        for i in range(lane.scroll, min(len(items), lane.scroll + view)):
            track = items[i]
            selected = i == lane.cursor
            playing = bool(playing_pid) and track.pid == playing_pid
            band = selected and focused
            if selected:
                mark = ("▸", "brightcyan", "bold")
            elif playing:
                mark = (eq, self.tone)
            else:
                mark = (" ",)
            if playing:
                name_style, artist_style = (self.tone, "bold"), (self.tone,)
            elif band:
                name_style, artist_style = ("brightwhite", "bold"), ("white",)
            elif selected:
                name_style, artist_style = ("cyan",), ("cyan",)
            else:
                name_style, artist_style = (), ("grey",)
            segs = [mark, (Tab(2),), (str(i + 1).rjust(num_w), "grey"),
                    (Tab(name_x),), (truncate(track.name, name_w),) + name_style,
                    (Tab(artist_x),), (truncate(track.artist, artist_w),) + artist_style]
            if dur_w and track.duration:
                segs += [(Tab(dur_x),), (mmss(track.duration).rjust(dur_w), "grey")]
            out.append(Line(segs, (SELECTED_BG,)) if band else segs)
        return out

    @staticmethod
    def list_footer(lane, view):
        """`12/40` while the list scrolls, plus its running time when known."""
        n = len(lane.items)
        footer = "%d/%d" % (lane.cursor + 1, n) if n > view else ""
        total = sum(getattr(t, "duration", 0) or 0 for t in lane.items)
        if total >= 60:
            hours, minutes = divmod(int(total) // 60, 60)
            length = "%dh %02dm" % (hours, minutes) if hours else "%dm" % minutes
            footer = "%s · %s" % (footer, length) if footer else length
        return footer

    def panel_tracks(self, width, height):
        lane = self.lanes[TRACKS]
        view = max(1, height - 2)
        self._views[TRACKS] = view
        lane.clamp(view)
        focused = self.focus == TRACKS
        if lane.items:
            body = self.track_rows(lane, width - 2, view, focused)
        elif self.bus.loading(("tracks", "search")):
            body = [[("  ",), (self.spinner() + " loading…", "grey")]]
        else:
            body = [[("  nothing to show" if not lane.filter else "  no match", "grey")]]
        title = "3 %s" % truncate(self.main_title, max(8, width - 24))
        if lane.filter:
            caret = "▏" if self.mode == FILTER and focused else ""
            title += "  /%s%s" % (lane.filter, caret)
        return self.frame(TRACKS, width, height, title, body,
                          footer=self.list_footer(lane, view))

    def panel_glyphs(self, width, height):
        """Every glyph family on screen at once.

        A terminal cannot be asked whether it owns a codepoint - a missing glyph
        still takes up its cell, so nothing distinguishes it from a real one
        until it is drawn. Showing them is the only honest test.
        """
        inner = width - 2
        current = art.glyph_mode()
        body = [[], [("  a row of boxes or question marks means your terminal "
                      "lacks that family", "grey")], []]
        for mode, label, shown in art.sample_rows(inner - 6):
            here = mode == current
            body.append([("  %s " % ("▸" if here else " "),
                          "brightcyan" if here else "grey"),
                         (label, "bold" if here else "white"), ("  ",), (shown,)])
            body.append([])
        body.append([("  choose with ", "grey"), (":art half", "brightcyan"),
                     (" · ", "grey"), (":art quad", "brightcyan"),
                     (" · ", "grey"), (":art sext", "brightcyan"),
                     (" · ", "grey"), (":art oct", "brightcyan")])
        body.append([("  then ", "grey"), ("a", "bold"),
                     (" shows the cover; LAZYMUSIC_ART_GLYPH makes it stick",
                      "grey")])
        return self.main_box(width, height, "Glyph test", body)

    def panel_art(self, width, height):
        """A small cover on the left, the track details laid out beside it.

        Side by side rather than stacked because the main panel is far wider
        than it is tall, and a cover big enough to fill that width vertically
        would be exactly the oversized-block problem the small size avoids.
        Underneath, when the song has a timed lyric, the line being sung.
        """
        if self._glyph_sample:
            return self.panel_glyphs(width, height)
        inner = width - 2
        track = self.state.track
        title = VIEW_TITLE[V_ART]
        if not track:
            return self.main_box(width, height, title,
                                 [[("  nothing playing", "grey")]])

        rows = art_rows_for(height, inner)
        cover = None
        if rows:
            cover = self._art.get((track.pid, rows, art.glyph_mode()))
            if cover is None:
                self.want_art(rows)
        cover_w = rows * 2
        text_x = 2 + cover_w + ART_GUTTER if rows else 2
        details = self.now_playing(max(8, inner - text_x - 1))

        # While the cover is loading or missing, its square is held open so the
        # details do not jump sideways the moment it lands.
        note = "" if cover else ("no art" if cover == [] else self.spinner())

        # The card is as tall as whichever of the two columns is taller, and is
        # centred in the panel rather than left hanging under the title.
        card = max(rows, len(details))
        room = height - 2 - card
        snippet = self.lyric_snippet(inner - 4) if room >= 5 else []
        total = card + (len(snippet) + 1 if snippet else 0)
        body = [[] for _ in range(max(0, (height - 2 - total) // 2))]
        for i in range(card):
            line = [("  ",)]
            if i < rows:
                if cover:
                    line.append((cover[i],) if i < len(cover) else (" " * cover_w,))
                elif i == rows // 2:
                    line.append((pad(note.center(cover_w), cover_w), "grey"))
            line.append((Tab(text_x),))
            if i < len(details):
                line += details[i]
            body.append(line)
        if snippet:
            body.append([])
            body += snippet
        return self.main_box(width, height, title, body)

    def lyric_snippet(self, width):
        """The line being sung, with the one before and the one after it."""
        pid = self.state.track.pid
        if lyrics.mode() == "off" or not pid:
            return []
        found = self._lyrics.get(pid)
        if found is None:
            self.want_lyrics()
            return []
        if not found.synced:
            return []
        current = found.line_at(self.position - self._lyric_delay)
        out = []
        for i, style in ((current - 1, ("grey",)), (current, (self.tone, "bold")),
                         (current + 1, ("white",))):
            text = found.lines[i][1] if 0 <= i < len(found.lines) else ""
            if i == current and not text:
                text = "♪"
            text = truncate(clean(text), width)
            out.append([(Tab(2 + max(0, (width - widest(text)) // 2)),),
                        (text,) + style])
        return out

    def lyric_layout(self, found, width):
        """The lyric wrapped to `width`: rows of (line index, text), and the
        first row of each line. Cached, since it only changes with the song."""
        key = (id(found), width)
        if self._lyric_layout[0] != key:
            rows, first = [], []
            for i, (_at, text) in enumerate(found.lines):
                first.append(len(rows))
                rows += [(i, part) for part in (wrap(text, width) or [""])]
            self._lyric_layout = (key, rows, first)
        return self._lyric_layout[1], self._lyric_layout[2]

    def panel_lyrics(self, width, height):
        lane = self.lanes["lyrics"]
        view = max(1, height - 2)
        self._views["lyrics"] = view
        pid = self.state.track.pid
        found = self._lyrics.get(pid) if pid else None

        # The lane holds one track's lines at a time; refill it when the fetch
        # for a different song lands.
        if found is not None and self._lyric_pid != pid:
            self._lyric_pid = pid
            lane.set_items(found.lines)
            lane.cursor = lane.scroll = 0

        title = VIEW_TITLE[V_LYRICS]
        if pid:
            title = "%s · %s" % (title, self.state.track.name)
        if not pid:
            return self.main_box(width, height, title,
                                 [[("  nothing playing", "grey")]])
        if found is None:
            self.want_lyrics()
            return self.main_box(width, height, title,
                                 [[("  %s fetching lyrics…" % self.spinner(), "grey")]])
        if not found.lines:
            how = lyrics.mode()
            note = {"off": "lyrics are switched off (LAZYMUSIC_LYRICS)",
                    "tags": "no lyrics in this file's tags"}.get(
                        how, "no lyrics found for this track")
            if found.instrumental:
                note = "♪  instrumental  ♪"
            return self.main_box(width, height, title, [[("  " + note, "grey")]])

        # A timed transcript scrolls itself, up until the moment you scroll it
        # by hand; `after_move` drops the follow, and `enter` or the next track
        # restores it.
        inner = width - 2
        avail = max(4, inner - 4)
        current = found.line_at(self.position - self._lyric_delay) if found.synced else -1
        if found.synced and self._follow and current >= 0:
            lane.goto(current, view)
        lane.clamp(view)

        # Long lines wrap rather than being cut off, so the view is laid out in
        # wrapped rows and kept centred on the line that matters.
        rows, first = self.lyric_layout(found, avail)
        anchor = max(0, min(lane.cursor, len(first) - 1))
        start = first[anchor]
        span = (first[anchor + 1] if anchor + 1 < len(first) else len(rows)) - start
        top = start + span // 2 - view // 2
        if not (found.synced and self._follow):
            top = max(0, min(top, len(rows) - view))
        # While following, the line being sung holds the middle of the panel
        # from the first line to the last, the way a karaoke screen does.

        focused = self.focus == TRACKS
        body = []
        for r in range(top, top + view):
            if not 0 <= r < len(rows):
                body.append([])
                continue
            i, text = rows[r]
            if found.synced:
                if i == current:
                    style = (self.tone, "bold")
                    text = text or "♪ ♪ ♪"
                elif current >= 0 and i < current:
                    style = ("grey",)
                else:
                    style = ("white",)
                if i == lane.cursor and not self._follow and focused and i != current:
                    style = ("brightcyan", "bold")
            elif i == lane.cursor and focused:
                style = ("brightcyan", "bold")
            else:
                style = ("white",)
            if not text:
                body.append([])
                continue
            body.append([(Tab(2 + max(0, (avail - widest(text)) // 2)),),
                         (text,) + style])

        if found.synced:
            title += "  ●" if self._follow else "  ○"
        parts = [found.source + (" · synced" if found.synced else "")]
        if self._lyric_delay:
            parts.append("%+.1fs" % self._lyric_delay)
        if len(lane.items) > view:
            parts.append("%d/%d" % (lane.cursor + 1, len(lane.items)))
        return self.main_box(width, height, title, body, footer="  ".join(parts))

    def panel_queue(self, width, height):
        lane = self.lanes["queue"]
        view = max(1, height - 2)
        self._views["queue"] = view
        lane.clamp(view)
        inner = width - 2
        if not lane.items:
            note = ("nothing playing from a playlist" if not self.state.playlist
                    else "nothing left in this playlist")
            body = [[("  " + note, "grey")], [],
                    [("  press ", "grey"), ("A", "bold"),
                     (" on any track to queue it", "grey")]]
        else:
            body = self.track_rows(lane, inner, view, self.focus == TRACKS)
        if self._queue_kind == "queue":
            title = "%s — queue (%d)" % (VIEW_TITLE[V_QUEUE], len(lane.all_items))
        elif self._queue_kind == "playlist":
            # Shuffle makes the real order unknowable, so the title stops
            # claiming to know it rather than showing a confidently wrong list.
            title = "%s — %s" % (VIEW_TITLE[V_QUEUE], self.state.playlist)
            if self.state.shuffle:
                title += "  ~shuffled"   # the real order is Music's to know
        else:
            title = VIEW_TITLE[V_QUEUE]
        return self.main_box(width, height, title, body,
                             footer=self.list_footer(lane, view))

    def help_rows(self, width):
        """Compose the whole key sheet for this width, however tall it comes out.

        The sheet outgrew a short terminal once the view and queue keys were
        added, so it is built in full here and scrolled by `panel_help` rather
        than being silently cut off at the bottom of the panel.
        """
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

        rows = [[]]
        for i in range(max(len(c) for c in columns)):
            segs = []
            for col, key_w in zip(columns, key_ws):
                segs += cell(col[i], key_w) if i < len(col) else [(" " * col_w,)]
            rows.append(segs)
        rows.append([])
        rows.append([("  press ", "grey"), ("?", "bold"), (" or ", "grey"),
                     ("esc", "bold"), (" to close", "grey")])
        return rows

    def panel_help(self, width, height):
        lane = self.lanes["help"]
        view = max(1, height - 2)
        self._views["help"] = view
        if width != self._help_width:
            self._help_width = width
            lane.set_items(self.help_rows(width))
            lane.cursor = lane.scroll = 0
        # There is no cursor to show on a key sheet, so the cursor *is* the top
        # of the view - otherwise the first dozen `j` presses would move an
        # invisible marker and look like nothing had happened.
        total = len(lane.items)
        top = max(0, min(lane.cursor, max(0, total - view)))
        lane.cursor = lane.scroll = top
        body = lane.items[top:top + view]
        footer = ""
        if total > view:
            footer = "j/k  %d-%d of %d" % (top + 1, min(top + view, total), total)
        return self.frame(TRACKS, width, height, "Keys", body, footer=footer,
                          focused=False)

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
            icon, tone = {"error": ("✗", "brightred"),
                          "ok": ("✓", "brightgreen")}.get(
                              self.message_kind, ("•", "yellow"))
            return row([(" ",), (icon + " ", tone, "bold"), (self.message, tone)],
                       cols)

        if self.view == V_HELP:
            pairs = [("?/esc", "close help"), ("j/k", "scroll")]
        elif self.view == V_LYRICS:
            pairs = [("space", "play/pause"), ("j/k", "scroll"),
                     ("enter", "sing from here"), ("a", "art"), ("u", "up next"),
                     ("esc", "tracks"), ("?", "help"), ("q", "quit")]
        elif self.view != V_TRACKS:
            pairs = [("space", "play/pause"), ("j/k", "move"), ("a", "art"),
                     ("y", "lyrics"), ("u", "up next"), ("esc", "tracks"),
                     ("?", "help"), ("q", "quit")]
        else:
            pairs = [("space", "play/pause"), ("j/k", "move"), ("h/l", "panel"),
                     ("enter", "play"), ("A", "queue"), ("a", "art"),
                     ("y", "lyrics"), ("?", "help"), ("q", "quit")]
        segs = [(" ",)]
        for key, what in pairs:
            segs.append((key, "brightcyan", "bold"))
            segs.append((" " + what + "   ", "grey"))

        # The right-hand corner: half-finished vim input, as vim shows it, and
        # a spinner while something you are waiting on is still loading.
        corner = []
        hint = self.count + self.pending.replace("ctrl-", "^")
        if hint:
            corner.append((hint + " ", "yellow", "bold"))
        if self.bus.loading():
            corner.append((self.spinner() + " ", "brightcyan"))
        if not corner:
            return row(segs, cols)
        corner_w = sum(width_of(seg[0]) for seg in corner)
        return row(segs, max(0, cols - corner_w)) + row(corner, corner_w)

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
        if self.view == V_HELP:
            if key in ("?", "esc", "q", "enter"):
                self.view = V_TRACKS
                return
            if key.isdigit():
                self.count += key
                return
            self.motion_key(key, self.take_count())
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
            elif key in ("1", "2", "3"):
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
        if moved and self.view == V_LYRICS:
            self._follow = False

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
            # Taking shuffle into your own hands cancels our obligation to put
            # it back the way it was before the queue started.
            self._shuffle_restore = False
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
            self.set_view(V_HELP)
        elif key == "a":
            self.set_view(V_ART)
        elif key == "y":
            self.set_view(V_LYRICS)
        elif key == "u":
            self.set_view(V_QUEUE)
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
        elif key == "ctrl-l":
            self._size = None           # vim's redraw: repaint every row
            self._help_width = -1
        elif key == "A":
            self.enqueue_selection()
        elif key == "D":
            self.dequeue()
        elif key == "esc":
            self.count, self.pending = "", ""
            lane, view = self.focused_lane()
            if lane is not None and lane.filter:
                lane.apply_filter("")
                lane.clamp(view)
            elif self.view != V_TRACKS:
                self.view = V_TRACKS

    # -- focus ---------------------------------------------------------------

    def focused_lane(self):
        """The lane the cursor is in, and how many of its rows are on screen.

        The main panel is not one list but whichever the current view shows, so
        focus on panel 4 resolves through `VIEW_LANE`; the cover and the help
        sheet have no lane at all, and motions simply find nothing to move.
        """
        if self.view == V_HELP:
            return self.lanes["help"], self._views.get("help", 10)
        key = self.focus
        if key == TRACKS:
            key = VIEW_LANE.get(self.view)
            if key is None:
                return None, 0
        return self.lanes.get(key), self._views.get(key, 10)

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
        stack = (PLAYER, PLAYLISTS)
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
            self.fail(e)
        except ValueError:
            # `:vol loud` used to take the whole UI down with a traceback.
            self.notify("could not read %r as a number" % rest, 3.0, "error")

    def cmd_quit(self, rest):
        self.running = False

    def cmd_search(self, rest):
        if not rest:
            self.notify("usage: :search <query>")
            return
        self.notify("searching…", 30)

        def done(hits, err):
            if err:
                self.fail(err)
                return
            self.message = ""
            self.push_view()
            # Results land in the main panel, which is the only one wide enough
            # to read a track and its artist side by side.
            self.show_main("Search: %s" % rest, hits)
            self.focus = TRACKS
            if not hits:
                self.notify("nothing in your library matches that")

        self.bus.submit("search", lambda: music.search(rest), done)

    def cmd_play(self, rest):
        if not rest:
            self.act(music.play)
            return

        def done(hits, err):
            if err:
                self.fail(err)
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
        self.view = V_HELP

    def cmd_art(self, rest):
        """`:art` shows the cover; `:art half` / `:art oct` picks the glyph.

        Octants pack four times the pixels into a cell but are Unicode 16, so a
        terminal that has not caught up draws them as empty boxes. Switching is
        a command rather than a setting so you can see the difference at once.
        """
        arg = rest.strip().lower()
        if arg == "test":
            self._glyph_sample = True
            self.view = V_ART
            return
        if arg:
            if not art.set_glyph(arg):
                self.notify("art glyph must be one of: %s  (try :art test)"
                            % ", ".join(art.GLYPH_ORDER))
                return
            self._glyph_sample = False
            self.notify("cover glyph: %s" % art.glyph_mode())
            self.view = V_ART
            return
        self.set_view(V_ART)

    def cmd_lyrics(self, rest):
        self.set_view(V_LYRICS)

    def cmd_upnext(self, rest):
        """`:upnext` shows the queue; `:upnext clear` empties it."""
        if rest.strip() == "clear":
            def done(_value, err):
                if err:
                    self.fail(err)
                else:
                    self.notify("queue cleared", kind="ok")
                self.after_queue_change()
            self.bus.submit("qclear", music.queue_clear, done)
            self.view = V_QUEUE
            return
        if rest.strip() == "play":
            self.start_queue()
            self.view = V_QUEUE
            return
        if rest:
            self.notify("usage: :upnext [play|clear]")
            return
        self.set_view(V_QUEUE)

    def cmd_enqueue(self, rest):
        self.enqueue_selection()

    def cmd_tracks(self, rest):
        self.view = V_TRACKS

    def cmd_sleep(self, rest):
        """`:sleep 30` pauses in thirty minutes; `:sleep off` cancels it."""
        arg = rest.strip().lower().rstrip("m")
        if not arg:
            left = self.sleep_left()
            self.notify("sleep timer: %s" % (left + " left" if left else
                                             "off  (:sleep <minutes>)"))
            return
        if arg in ("off", "0", "cancel"):
            self._sleep_at = 0.0
            self.notify("sleep timer off", kind="ok")
            return
        minutes = float(arg)
        if minutes <= 0:
            raise ValueError(arg)
        self._sleep_at = time.monotonic() + minutes * 60
        self.notify("pausing in %s ☾" % self.sleep_left(), kind="ok")

    def cmd_offset(self, rest):
        """`:offset +0.5` shows lyrics half a second later; bare resets it."""
        arg = rest.strip().rstrip("s")
        if not arg or arg == "0":
            self._lyric_delay = 0.0
        elif arg[0] in "+-":
            # Positive means the words come later, which is what you want when
            # the highlight runs ahead of the singer.
            self._lyric_delay += float(arg)
        else:
            self._lyric_delay = float(arg)
        self.notify("lyrics offset %+.1fs" % self._lyric_delay, kind="ok")

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
        "art": cmd_art, "cover": cmd_art, "lyrics": cmd_lyrics,
        "upnext": cmd_upnext, "queue": cmd_upnext, "tracks": cmd_tracks,
        "enqueue": cmd_enqueue, "add": cmd_enqueue,
        "sleep": cmd_sleep, "offset": cmd_offset,
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

    def enqueue_selection(self):
        """`A`: queue the track under the cursor, wherever that cursor is."""
        lane, _ = self.focused_lane()
        track = lane.selected if lane is not None else None
        if not isinstance(track, music.Track):
            self.notify("no track under the cursor")
            return
        self.enqueue(track)

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
            return
        if self.focus == TRACKS and self.view == V_LYRICS:
            self.sing_from_cursor()
            return
        lane, _ = self.focused_lane()
        track = lane.selected if lane is not None else None
        # The cover has no lane, and so nothing for enter to play.
        if not isinstance(track, music.Track):
            return
        # A track picked out of Up Next belongs to the playing playlist, so it
        # is started in that context and the rest of the list follows on.
        if self.focus == TRACKS and self.view == V_QUEUE:
            playlist = music.QUEUE if self._queue_kind == "queue" else self.state.playlist
        elif self.focus == TRACKS and self.view == V_TRACKS:
            playlist = self.main_playlist
        else:
            playlist = ""
        if playlist:
            self.act(music.play_track_in_playlist, track.pid, playlist)
        else:
            self.act(music.play_track, track.pid)
        self.notify("playing %s" % track.label)

    def sing_from_cursor(self):
        """`enter` on a timed lyric line: jump the song to it and follow again."""
        found = self._lyrics.get(self.state.track.pid)
        lane = self.lanes["lyrics"]
        at = found.time_of(lane.cursor) if found and found.synced else None
        self._follow = True
        if at is None:
            return
        self.act(music.seek, max(0.0, at + self._lyric_delay))
        # Move the playhead here too, so the highlight does not jump back to
        # the old line for the second it takes the new position to be read.
        self.state.position = max(0.0, at + self._lyric_delay)
        self._pos_at = time.monotonic()


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
    sys.stdout.write(ALT_ON + CUR_HIDE + WRAP_OFF + CLR_ALL)
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
                app.tick_queue()
                app.tick_sleep()
                app.refresh()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        sys.stdout.write(WRAP_ON + CUR_SHOW + ALT_OFF)
        sys.stdout.flush()
    return 0
