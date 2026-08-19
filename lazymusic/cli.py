"""Command line interface for `lazymusic`."""

import json
import sys

from . import music
from .fmt import c, mmss, truncate
from .osa import MusicError

USAGE = """lazymusic - control Apple Music from your terminal

  mus                        open the interactive player
  mus <query>                find a song in your library and play it
  mus status [--json]        what is playing right now
  mus play [query]           resume, or search and play
  mus pause | toggle
  mus next | prev
  mus vol [0-100|+10|-10]
  mus seek [30|+15|-15]
  mus shuffle [on|off]
  mus repeat [off|one|all]
  mus love [on|off]
  mus search <query>
  mus list [playlist]        list playlists, or one playlist's tracks
  mus pl <playlist>          play a playlist

`mus` is the short alias; `lazymusic` does the same thing.
"""


def _icon(state):
    return {"playing": "▶", "paused": "⏸"}.get(state.player, "■")


def print_status(state, as_json=False):
    if as_json:
        t = state.track
        json.dump(
            {
                "running": state.running, "state": state.player,
                "title": t.name, "artist": t.artist, "album": t.album,
                "position": round(state.position, 2), "duration": round(t.duration, 2),
                "volume": state.volume, "shuffle": state.shuffle,
                "repeat": state.repeat, "loved": t.loved, "playlist": state.playlist,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not state.running:
        print(c("Music is not running.", "grey"))
        return
    if state.stopped:
        print(c("■ nothing playing", "grey"))
        return
    t = state.track
    print("%s  %s" % (c(_icon(state), "green"), c(t.name, "bold")))
    if t.artist:
        print("   %s%s" % (t.artist, c("  ·  " + t.album, "grey") if t.album else ""))
    meta = "%s / %s" % (mmss(state.position), mmss(t.duration))
    flags = []
    if state.shuffle:
        flags.append("shuffle")
    if state.repeat != "off":
        flags.append("repeat:" + state.repeat)
    if t.loved:
        flags.append("♥")
    flags.append("vol %d" % state.volume)
    print(c("   %s  ·  %s" % (meta, "  ·  ".join(flags)), "grey"))


def print_tracks(tracks):
    if not tracks:
        print(c("No matches in your library.", "grey"))
        return
    width = max(20, min(46, max((len(t.name) for t in tracks), default=20)))
    for i, t in enumerate(tracks, 1):
        line = "%s  %s  %s" % (
            c("%2d" % i, "grey"),
            truncate(t.name, width).ljust(width),
            c(truncate(t.artist, 30), "grey"),
        )
        print(line.rstrip())


def _relative(raw):
    """Parse '+10' / '-10' / '40' into (value, is_relative)."""
    raw = raw.strip()
    if raw.startswith(("+", "-")):
        return float(raw), True
    return float(raw), False


def _ensure_running():
    # Starts Music hidden if it is down: a terminal command should never pull
    # a window over the terminal you ran it from.
    music.ensure_running()


def _resolve_and_play(query):
    _ensure_running()
    hits = music.search(query, limit=25)
    if not hits:
        print(c("Nothing in your library matches %r." % query, "yellow"))
        print(c("Add it to your library in Music, then try again.", "grey"))
        return 1
    music.play_track(hits[0].pid)
    print("%s  %s %s" % (c("▶", "green"), hits[0].name, c(hits[0].artist, "grey")))
    if len(hits) > 1:
        print(c("   (%d other matches - run `mus search %s` to see them)"
                % (len(hits) - 1, query), "grey"))
    return 0


def dispatch(argv):
    if not argv:
        from .tui import run_tui
        return run_tui()

    cmd, args = argv[0], argv[1:]

    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    if cmd == "status" or cmd == "now":
        print_status(music.status(), as_json="--json" in args)
        return 0

    if cmd == "play":
        if args:
            return _resolve_and_play(" ".join(args))
        _ensure_running()
        music.play()
    elif cmd == "pause":
        music.pause()
    elif cmd in ("toggle", "pp"):
        _ensure_running()
        music.toggle()
    elif cmd in ("next", "n"):
        music.next_track()
    elif cmd in ("prev", "previous", "p"):
        music.prev_track()
    elif cmd == "stop":
        music.stop()
    elif cmd in ("vol", "volume"):
        if not args:
            print(music.status().volume)
            return 0
        value, rel = _relative(args[0])
        music.nudge_volume(value) if rel else music.set_volume(value)
    elif cmd == "seek":
        if not args:
            print(mmss(music.status().position))
            return 0
        value, rel = _relative(args[0])
        music.seek(value, relative=rel)
    elif cmd == "shuffle":
        arg = args[0].lower() if args else "toggle"
        if arg in ("on", "true", "yes"):
            music.set_shuffle(True)
        elif arg in ("off", "false", "no"):
            music.set_shuffle(False)
        else:
            music.toggle_shuffle()
    elif cmd == "repeat":
        if not args:
            print(music.cycle_repeat(music.status().repeat))
            return 0
        music.set_repeat(args[0].lower())
    elif cmd in ("love", "like"):
        arg = args[0].lower() if args else "toggle"
        if arg in ("on", "yes"):
            music.set_loved(True)
        elif arg in ("off", "no"):
            music.set_loved(False)
        else:
            music.toggle_loved()
    elif cmd in ("search", "s", "find"):
        if not args:
            print(c("usage: lazymusic search <query>", "yellow"))
            return 2
        print_tracks(music.search(" ".join(args)))
        return 0
    elif cmd in ("list", "ls", "playlists"):
        if args:
            print_tracks(music.playlist_tracks(" ".join(args)))
        else:
            for name, count in music.playlists():
                print("%s  %s" % (truncate(name, 34).ljust(34), c("%d" % count, "grey")))
        return 0
    elif cmd in ("pl", "playlist"):
        if not args:
            print(c("usage: lazymusic pl <playlist name>", "yellow"))
            return 2
        _ensure_running()
        music.play_playlist(" ".join(args))
    else:
        # Bare words are treated as a song query: `lazymusic kun faya kun`.
        return _resolve_and_play(" ".join(argv))

    print_status(music.status())
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return dispatch(argv)
    except MusicError as e:
        print(c("error: %s" % e, "red"), file=sys.stderr)
        return 1
    except (ValueError, IndexError):
        print(c("Could not parse those arguments. Try `mus help`.", "yellow"),
              file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
