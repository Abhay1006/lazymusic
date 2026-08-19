"""Domain layer: everything `mus` can ask Music.app to do."""

import time

from .osa import SEP, MusicError, is_running, launch, lit
from .osa import tell as _raw_tell

REPEAT_MODES = ("off", "one", "all")

_up = False  # last known state of Music.app; kept fresh by `status`


def ensure_running():
    """Make sure Music.app is up before anything is asked of it.

    Doing it here rather than letting AppleScript do it means the app starts
    hidden instead of jumping in front of the terminal. Cheap on the hot path:
    once Music is known to be up this is a bool test, no osascript round trip.
    """
    global _up
    if _up:
        return
    if not is_running():
        launch()
    _up = True


def tell(body, timeout=25.0):
    """Every request to Music goes through here, so it is always awake first."""
    ensure_running()
    return _raw_tell(body, timeout=timeout)


class Track:
    def __init__(self, pid="", name="", artist="", album="", duration=0.0, loved=False):
        self.pid = pid
        self.name = name
        self.artist = artist
        self.album = album
        self.duration = duration
        self.loved = loved

    def __bool__(self):
        return bool(self.name or self.pid)

    @property
    def label(self):
        return "%s - %s" % (self.name, self.artist) if self.artist else self.name


class State:
    def __init__(self):
        self.running = False
        self.player = "stopped"
        self.volume = 0
        self.shuffle = False
        self.repeat = "off"
        self.position = 0.0
        self.playlist = ""
        self.track = Track()

    @property
    def playing(self):
        return self.player == "playing"

    @property
    def stopped(self):
        return self.player == "stopped" or not self.track


def _num(s, default=0.0):
    try:
        return float(s.strip().replace(",", "."))
    except (ValueError, AttributeError):
        return default


# Every optional property is wrapped in `try` - streaming and cloud tracks do not
# always expose the full set, and one missing field must not kill the whole read.
_STATUS = """
set D to character id 31
set o to (player state as text) & D
set o to o & ((sound volume) as text) & D
set o to o & ((shuffle enabled) as text) & D
set o to o & ((song repeat) as text) & D
set pos to "0"
try
    set pos to (player position as text)
end try
set o to o & pos & D
set pl to ""
try
    set pl to (name of current playlist)
end try
set o to o & pl & D
set pid to ""
set nm to ""
set ar to ""
set al to ""
set du to "0"
set lv to "false"
try
    -- One event fetches the whole record; the field reads below are then local.
    -- Asking for each property separately cost a round trip apiece and made
    -- this query take 379ms instead of ~150ms.
    set t to (properties of current track)
    set pid to (persistent ID of t)
    set nm to (name of t)
    set ar to (artist of t)
    set al to (album of t)
    set du to ((duration of t) as text)
    try
        set lv to ((favorited of t) as text)
    on error
        set lv to ((loved of t) as text)
    end try
end try
return o & pid & D & nm & D & ar & D & al & D & du & D & lv
"""


def status(autolaunch=False):
    global _up
    st = State()
    _up = is_running()    # cheap, and it notices Music being quit under us
    if not _up:
        if not autolaunch:
            return st
        launch()          # hidden; see osa.launch
        _up = True
    st.running = True
    parts = tell(_STATUS).split(SEP)
    parts += [""] * (12 - len(parts))
    st.player = parts[0].strip() or "stopped"
    st.volume = int(_num(parts[1]))
    st.shuffle = parts[2].strip() == "true"
    st.repeat = parts[3].strip() or "off"
    st.position = _num(parts[4])
    st.playlist = parts[5]
    st.track = Track(
        pid=parts[6],
        name=parts[7],
        artist=parts[8],
        album=parts[9],
        duration=_num(parts[10]),
        loved=parts[11].strip() == "true",
    )
    return st


# ---------------------------------------------------------------- playback ----

def play():
    tell("play")


def pause():
    tell("pause")


def toggle():
    tell("playpause")


def stop():
    tell("stop")


def next_track():
    tell("next track")


def prev_track():
    """Restart the current track, or jump back if we are near its start.

    This mirrors what every music player does with a "previous" button: the
    first press rewinds, a second press within a few seconds goes back a track.
    """
    tell(
        "if (player position) > 3 then\n"
        "    set player position to 0\n"
        "else\n"
        "    previous track\n"
        "end if"
    )


def seek(seconds, relative=False):
    """Move the playhead. Retries once: a track that has just been told to play
    is briefly not seekable yet, and Music.app answers with error -10006."""
    if relative:
        script = "set player position to ((player position) + %f)" % seconds
    else:
        script = "set player position to %f" % max(0.0, seconds)
    try:
        tell(script)
    except MusicError as e:
        if "-10006" not in str(e):
            raise
        time.sleep(0.4)
        tell(script)


def set_volume(level):
    tell("set sound volume to %d" % max(0, min(100, int(level))))


def nudge_volume(delta):
    tell(
        "set v to (sound volume) + (%d)\n"
        "if v > 100 then set v to 100\n"
        "if v < 0 then set v to 0\n"
        "set sound volume to v" % int(delta)
    )


def set_shuffle(on):
    tell("set shuffle enabled to %s" % ("true" if on else "false"))


def toggle_shuffle():
    tell("set shuffle enabled to not (shuffle enabled)")


def set_repeat(mode):
    if mode not in REPEAT_MODES:
        raise MusicError("repeat mode must be one of: %s" % ", ".join(REPEAT_MODES))
    # `song repeat` takes a keyword, not a string, so the mode is spliced in raw.
    tell("set song repeat to %s" % mode)


def cycle_repeat(current):
    nxt = REPEAT_MODES[(REPEAT_MODES.index(current) + 1) % len(REPEAT_MODES)]
    set_repeat(nxt)
    return nxt


def _favorite(new_expr, old_expr):
    """Set the favourite flag on the current track.

    macOS 15 renamed Love to Favorite and dropped the old `loved` property,
    which now raises -10001. Prefer `favorited`, fall back for older systems.
    """
    tell(
        "try\n"
        "    set favorited of current track to %s\n"
        "on error\n"
        "    set loved of current track to %s\n"
        "end try" % (new_expr, old_expr)
    )


def set_loved(on):
    value = "true" if on else "false"
    _favorite(value, value)


def toggle_loved():
    _favorite("not (favorited of current track)", "not (loved of current track)")


# --------------------------------------------------- bulk property fetching ----
#
# Reading properties one track at a time costs a round trip per property per
# track (~50ms), which makes a 1,200-track playlist take over a minute. Asking
# for `name of every track ...` instead returns the whole column in a single
# Apple Event: the same playlist comes back in about a tenth of a second.
#
# The catch is that the getter has to be applied to the `whose` clause itself -
# binding the matches to a variable first and asking for `name of res` fails
# with -1700. So the selector is spliced into each column query.

GROUP = "\x1e"  # record separator, between property columns

_COLUMNS = """
set AppleScript's text item delimiters to (character id 31)
%(setup)s
set c1 to (persistent ID of %(sel)s) as text
set c2 to (name of %(sel)s) as text
set c3 to (artist of %(sel)s) as text
set c4 to (album of %(sel)s) as text
return c1 & (character id 30) & c2 & (character id 30) & c3 & (character id 30) & c4
"""


def _columns(selector, setup="", timeout=90.0):
    """Fetch id/name/artist/album for a track selector and zip them into Tracks."""
    out = tell(_COLUMNS % {"sel": selector, "setup": setup}, timeout=timeout)
    cols = out.split(GROUP)
    if len(cols) < 4 or not cols[0]:
        return []
    ids, names, artists, albums = (col.split(SEP) for col in cols[:4])
    return [
        Track(pid=i, name=n, artist=a, album=al)
        for i, n, a, al in zip(ids, names, artists, albums)
    ]


# ------------------------------------------------------------------ search ----

_MATCH = ("(every track of library playlist 1 whose "
          "(name contains q) or (artist contains q) or (album contains q))")


def search(query, limit=200):
    """Search the local library by title, artist or album. Case-insensitive."""
    return _columns(_MATCH, setup="set q to %s" % lit(query))[:limit]


def play_track(pid):
    tell(
        "set t to (first track of library playlist 1 whose persistent ID is %s)\n"
        "play t" % lit(pid)
    )


# --------------------------------------------------------------- playlists ----

def playlists():
    """Every user playlist with its track count, in Music's own order.

    Names come back in one event, but the counts have to be looped: the obvious
    `count of tracks of every user playlist` quietly answers 0 rather than a
    list. Twenty-odd round trips still land well under half a second, and this
    runs once at startup.
    """
    out = tell(
        "set AppleScript's text item delimiters to (character id 31)\n"
        "set ns to (name of every user playlist) as text\n"
        "set cs to {}\n"
        "repeat with p in user playlists\n"
        "    set end of cs to ((count of tracks of p) as text)\n"
        "end repeat\n"
        "return ns & (character id 30) & (cs as text)"
    )
    cols = out.split(GROUP)
    if len(cols) < 2 or not cols[0]:
        return []
    return [(n, int(_num(k))) for n, k in zip(cols[0].split(SEP), cols[1].split(SEP))]


def play_playlist(name, shuffle=None):
    if shuffle is not None:
        set_shuffle(shuffle)
    tell("play playlist %s" % lit(name))


def playlist_tracks(name):
    return _columns("(every track of playlist pl)", setup="set pl to %s" % lit(name))


def play_track_in_playlist(pid, playlist):
    """Play a track in its playlist's context, so what follows is the rest of it."""
    tell(
        "set p to playlist %s\n"
        "set t to (first track of p whose persistent ID is %s)\n"
        "play t" % (lit(playlist), lit(pid))
    )


__all__ = [
    "MusicError", "REPEAT_MODES", "State", "Track", "cycle_repeat",
    "ensure_running", "is_running", "launch", "next_track", "nudge_volume", "pause", "play", "play_playlist",
    "play_track", "play_track_in_playlist", "playlist_tracks", "playlists",
    "prev_track", "search", "seek",
    "set_loved", "set_repeat", "set_shuffle", "set_volume", "status", "stop",
    "toggle", "toggle_loved", "toggle_shuffle",
]
