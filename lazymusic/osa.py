"""Thin wrapper around `osascript` for talking to Music.app."""

import re
import subprocess
import time

SEP = "\x1f"  # unit separator, used to delimit fields coming back from AppleScript


class MusicError(Exception):
    pass


_ERR_PREFIXES = re.compile(
    r'^\d+:\d+:\s*(execution error:\s*)?(Music got an error:\s*)?', re.I
)


def run(script, timeout=25.0):
    """Run an AppleScript snippet, return its stdout with the trailing newline gone."""
    try:
        p = subprocess.run(
            ["osascript", "-"],
            input=script.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise MusicError("osascript not found - this tool only runs on macOS")
    except subprocess.TimeoutExpired:
        raise MusicError("Music.app did not respond in time")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise MusicError(_ERR_PREFIXES.sub("", err) or "AppleScript failed")
    return p.stdout.decode("utf-8", "replace").rstrip("\n")


def tell(body, timeout=25.0):
    """Run `body` inside a `tell application "Music"` block."""
    return run('tell application "Music"\n%s\nend tell' % body, timeout=timeout)


def lit(value):
    """Quote a Python value as an AppleScript string literal."""
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    # AppleScript string literals cannot span lines; splice newlines back in.
    s = s.replace("\r", '" & (character id 13) & "').replace("\n", '" & (linefeed) & "')
    return '"%s"' % s


def is_running():
    """True if Music.app is already up. Avoids launching it just to read state.

    `pgrep` answers in a few milliseconds where the System Events round trip
    takes closer to eighty, which matters because this is checked on the
    polling path; System Events is kept as the fallback.
    """
    try:
        p = subprocess.run(["pgrep", "-x", "Music"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        out = run(
            'tell application "System Events" to return '
            '((name of processes) contains "Music") as text'
        )
        return out.strip() == "true"
    return p.returncode == 0


def launch(background=True, wait=10.0):
    """Start Music.app, by default without letting it come to the front.

    Any `tell application "Music"` starts the app on its own if it is not
    running - but AppleScript launches it the loud way, so the Music window
    lands on top of the terminal you were just working in. Starting it here
    with `open -g -j` (no foreground, launched hidden) keeps it out of sight;
    it still has to be running, since lazymusic is a remote control for it and
    nothing else. Returns True once the app answers.
    """
    if not background:
        run('tell application "Music" to activate')
    else:
        try:
            subprocess.run(["open", "-g", "-j", "-a", "Music"],
                           capture_output=True, timeout=wait)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise MusicError("could not start Music.app")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(0.2)
    return False
