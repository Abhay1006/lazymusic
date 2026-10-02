"""Lyrics for the current track, from the file's own tags or from LRCLIB.

Music.app exposes a `lyrics` property, but it only ever holds what is written
into the file's tags - Apple Music's streaming lyrics are not scriptable, so on
a library that is mostly streamed the property is empty for every track. The
tags are still tried first, since they cost one local Apple Event and are
authoritative when present; only when they come back empty is LRCLIB asked.

LRCLIB is a free, key-less community lyrics database. It often has an LRC
transcript with a timestamp per line, which is what lets the panel underline
the line currently being sung.

The lookup is the one piece of lazymusic that touches the network. Set
LAZYMUSIC_LYRICS=tags to keep everything local, or =off to disable lyrics.
"""

import bisect
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import music

API = "https://lrclib.net/api"
AGENT = "lazymusic (https://github.com/Abhay1006/lazymusic)"
TIMEOUT = 8.0

# `[mm:ss.xx]` at the head of a line, as LRC transcripts are written. A line may
# carry several when the same words recur, so they are matched one at a time.
_STAMP = re.compile(r"\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]")

# ID tags such as `[ar:Artist]` or `[offset:+250]`, which sit on lines of their
# own at the top of an LRC file and are not lyrics.
_TAG = re.compile(r"^\[([a-z#]+):([^\]]*)\]$", re.I)

# Enhanced LRC stamps a time on every word, `<00:12.34>`; only the line-level
# stamp is used here, so the word stamps are dropped rather than shown.
_WORD_STAMP = re.compile(r"<\d+:\d{2}(?:[.:]\d{1,3})?>")


def mode():
    """How lyrics may be sourced: "online" (the default), "tags" or "off"."""
    value = os.environ.get("LAZYMUSIC_LYRICS", "online").strip().lower()
    return value if value in ("online", "tags", "off") else "online"


class Lyrics:
    """Lines of a lyric, each optionally stamped with the second it starts at."""

    def __init__(self, lines=(), source="", synced=False, instrumental=False):
        self.lines = list(lines)          # [(seconds or None, text), ...]
        self.source = source              # "tags" or "lrclib"
        self.synced = synced
        self.instrumental = instrumental  # known to have no words at all
        self._times = [at for at, _ in self.lines if at is not None]

    def __bool__(self):
        return bool(self.lines)

    def line_at(self, position):
        """Index of the line being sung at `position` seconds, or -1.

        Stamped lines come first and in time order (`parse_lrc` sorts them), so
        this is a binary search for the last one that has already started.
        """
        if not self.synced or not self._times:
            return -1
        return bisect.bisect_right(self._times, position) - 1

    def time_of(self, index):
        """When line `index` starts, or None for an unstamped line."""
        if 0 <= index < len(self.lines):
            return self.lines[index][0]
        return None


def parse_lrc(text):
    """Split an LRC or plain transcript into `(seconds or None, text)` lines.

    A line stamped more than once belongs at each of those times, so it is
    emitted once per stamp and the whole lot re-sorted. Unstamped input falls
    through as plain lines with no times at all. ID tags are dropped, except
    `[offset:]`, which LRC defines as milliseconds to shift every stamp by.
    """
    out, offset = [], 0.0
    for raw in text.splitlines():
        raw = _WORD_STAMP.sub("", raw).strip()
        tag = _TAG.match(raw)
        if tag and not _STAMP.match(raw):
            if tag.group(1).lower() == "offset":
                try:
                    offset = float(tag.group(2).strip()) / 1000.0
                except ValueError:
                    pass
            continue
        stamps = list(_STAMP.finditer(raw))
        body = _STAMP.sub("", raw).strip()
        if not stamps:
            out.append((None, raw))
            continue
        for m in stamps:
            minutes, seconds, frac = m.group(1), m.group(2), m.group(3) or "0"
            at = int(minutes) * 60 + int(seconds) + float("0." + frac)
            out.append((at, body))
    if any(at is not None for at, _ in out):
        # A positive offset means the lyrics come *earlier*, per the LRC spec.
        out = [(None if at is None else max(0.0, at - offset), body)
               for at, body in out]
        out.sort(key=lambda pair: (pair[0] is None, pair[0] or 0.0))
    # Leading and trailing blank lines are just padding in the file.
    while out and not out[-1][1] and out[-1][0] is None:
        out.pop()
    while out and not out[0][1] and out[0][0] is None:
        out.pop(0)
    return out


class LyricsError(Exception):
    """A lookup that failed for a reason worth putting on the key bar."""


def _get(path, params):
    url = "%s/%s?%s" % (API, path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError:
        raise                       # 404 is a miss, and the caller handles it
    except (urllib.error.URLError, OSError):
        # Being offline is the ordinary case here, not a bug worth a traceback
        # of socket internals on the key bar.
        raise LyricsError("no connection to lrclib")
    except ValueError:
        raise LyricsError("lrclib sent something unreadable")


def _best(hits, duration):
    """Pick the hit whose length is closest to the track we are actually playing.

    Search results are ordered by text relevance, which happily returns a live
    cut or a remix first. Duration is the one field that reliably tells those
    apart, so when it is known it decides; the timestamps of a version that runs
    forty seconds long would drift away from the audio.
    """
    usable = [h for h in hits if h.get("syncedLyrics") or h.get("plainLyrics")]
    if not usable:
        return None
    if not duration:
        return usable[0]
    return min(usable, key=lambda h: abs((h.get("duration") or 0) - duration))


def _from_payload(hit):
    synced = hit.get("syncedLyrics")
    if synced:
        return Lyrics(parse_lrc(synced), "lrclib", synced=True)
    plain = hit.get("plainLyrics")
    if plain:
        return Lyrics(parse_lrc(plain), "lrclib")
    return Lyrics(source="lrclib", instrumental=bool(hit.get("instrumental")))


# Decorations streaming services add to a title that the lyrics databases
# almost never carry: `(From "Rockstar")`, `(feat. X)`, `- Remastered 2011`,
# `[Live]`. Matching on the bare title is what finds most Bollywood and
# remastered tracks, whose catalogue names are rarely the ones LRCLIB uses.
_DECORATION = re.compile(
    r"\s*[(\[][^)\]]*[)\]]"
    r"|\s+-\s+.*\b(remaster(ed)?|version|edit|live|mono|stereo|from|mix|"
    r"single|acoustic|unplugged|lofi|lo-fi|slowed|reverb|sped up)\b.*$",
    re.I)
_FEATURING = re.compile(r"\s+(feat\.?|ft\.?|featuring|with)\s+.*$", re.I)
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\band\b|\bx\b|;)\s*", re.I)


def simple_title(title):
    """`title` without the bracketed and dashed decorations catalogues add."""
    bare = _FEATURING.sub("", _DECORATION.sub("", title)).strip()
    return bare or title.strip()


def lead_artist(artist):
    """The first-credited artist: `A.R. Rahman, Javed Ali & Mohit` -> `A.R. Rahman`."""
    first = _ARTIST_SPLIT.split(_FEATURING.sub("", artist).strip())[0].strip()
    return first or artist.strip()


def from_lrclib(artist, title, album="", duration=0):
    """Look `title` up on LRCLIB. Returns an empty Lyrics if nothing matches.

    Tries the exact record first, then a search, then a search on the title and
    artist with their catalogue decorations stripped, then a free-text search.
    Each step is only taken when the one before found nothing usable.
    """
    if not (artist and title):
        return Lyrics()
    # The exact endpoint wants all four fields and answers 404 when its copy is
    # tagged even slightly differently, so a miss falls through to the search.
    try:
        found = _from_payload(_get("get", {
            "artist_name": artist, "track_name": title,
            "album_name": album, "duration": int(duration or 0),
        }))
        if found or found.instrumental:
            return found
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    bare_title, bare_artist = simple_title(title), lead_artist(artist)
    queries = [{"artist_name": artist, "track_name": title}]
    if (bare_title, bare_artist) != (title, artist):
        queries.append({"artist_name": bare_artist, "track_name": bare_title})
    queries.append({"q": "%s %s" % (bare_title, bare_artist)})
    for params in queries:
        try:
            best = _best(_get("search", params), duration)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            continue
        if best:
            return _from_payload(best)
    return Lyrics()


def for_track(track):
    """Lyrics for `track`: its own tags if it has any, otherwise LRCLIB.

    Raises whatever the network raised, so the caller can report it; an ordinary
    "nobody has this song" is an empty Lyrics rather than an error.
    """
    how = mode()
    if how == "off":
        return Lyrics()
    tagged = music.track_lyrics(track.pid).strip()
    if tagged:
        return Lyrics(parse_lrc(tagged), "tags",
                      synced=bool(_STAMP.search(tagged)))
    if how == "tags":
        return Lyrics()
    return from_lrclib(track.artist, track.name, track.album, track.duration)
