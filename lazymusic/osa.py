"""Thin wrapper around `osascript` for talking to Music.app.

Scripts go to `lazymusic-helper` instead when it has been built (see
`helper/`). The helper is a small Swift program that stays running and runs
AppleScript in-process, so a call does not cost a new `osascript` process and a
fresh compile. Without it, or if it will not start, everything goes through
`osascript` exactly as before.
"""

import atexit
import json
import os
import re
import select
import subprocess
import threading
import time

SEP = "\x1f"  # unit separator, used to delimit fields coming back from AppleScript


class MusicError(Exception):
    pass


# `osascript` puts the character range and "execution error:" in front; the
# helper does not. Both lose the "Music got an error:" that every one carries.
_ERR_PREFIXES = re.compile(
    r'^(\d+:\d+:\s*)?(execution error:\s*)?(Music got an error:\s*)?', re.I
)


def run(script, timeout=25.0):
    """Run an AppleScript snippet, return its output with the trailing newline gone."""
    helper = _helper()
    if helper is not None:
        return helper.run(script, timeout)
    return run_osascript(script, timeout)


def run_osascript(script, timeout=25.0):
    """`run`, always through a new `osascript` process."""
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


# ------------------------------------------------------------- the helper ----

HELPER_PROTOCOL = 1
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER_BUILT = os.path.join(_ROOT, "helper", "build", "lazymusic-helper")

# A helper that dies or will not start this many times is given up on for the
# rest of the run, so a broken build costs a few failed starts, not one per call.
GIVE_UP_AFTER = 3


class HelperGone(MusicError):
    """The helper exited while a script was running.

    The script may already have done its work, so it is not sent again through
    `osascript`: queueing a track twice would be worse than one error message.
    """


def helper_path():
    """The helper binary to use, or None for `osascript`.

    `LAZYMUSIC_HELPER=0` turns it off. Any other value is taken as the path of
    the binary.
    """
    env = os.environ.get("LAZYMUSIC_HELPER", "").strip()
    if env.lower() in ("0", "off", "no", "false", "osascript"):
        return None
    path = env if env and env.lower() not in ("1", "on", "yes", "true") else HELPER_BUILT
    return path if os.access(path, os.X_OK) else None


class Helper:
    """One running `lazymusic-helper`, used by one thread at a time.

    Requests are answered strictly in order, so each thread gets its own
    helper (see `_helper`). A slow library fetch on one worker then never holds
    up the status poll on another, just as with separate `osascript` runs.
    """

    def __init__(self, path, start_timeout=5.0):
        self._buf = b""
        self.timed_out = False
        try:
            self.proc = subprocess.Popen(
                [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)
        except OSError as e:
            raise MusicError("lazymusic-helper would not start: %s" % e)
        try:
            hello = json.loads(self._readline(start_timeout))
        except (MusicError, ValueError):
            self.close()
            raise MusicError("lazymusic-helper did not say hello")
        if hello.get("protocol") != HELPER_PROTOCOL:
            self.close()
            raise MusicError("lazymusic-helper is out of date - run helper/build.sh")

    @property
    def alive(self):
        return self.proc.poll() is None

    def run(self, script, timeout=25.0):
        line = json.dumps({"script": script}).encode("ascii") + b"\n"
        try:
            self.proc.stdin.write(line)
        except (BrokenPipeError, OSError):
            self.close()
            raise HelperGone("lazymusic-helper stopped")
        try:
            reply = json.loads(self._readline(timeout))
        except ValueError:
            self.close()
            raise HelperGone("lazymusic-helper sent back something unreadable")
        if reply.get("ok"):
            return str(reply.get("out", "")).rstrip("\n")
        err = str(reply.get("error", "")).strip()
        raise MusicError(_ERR_PREFIXES.sub("", err) or "AppleScript failed")

    def _readline(self, timeout):
        deadline = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                # Stuck inside an Apple Event that will not come back. Killing
                # it is the only way out; the next call starts a fresh one.
                self.timed_out = True
                self.close()
                raise MusicError("Music.app did not respond in time")
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                self.close()
                raise HelperGone("lazymusic-helper stopped")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", "replace")

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except OSError:
                pass


_local = threading.local()
_lock = threading.Lock()
_helpers = []
_failures = 0


def _helper():
    """This thread's helper, started on first use; None means use `osascript`."""
    global _failures
    current = getattr(_local, "helper", None)
    if current is not None and current.alive:
        return current
    if _failures >= GIVE_UP_AFTER:
        return None
    if current is not None:
        _local.helper = None
        # A helper killed after a timeout was waiting on Music, which is no
        # fault of its own. One that died is counted, so a helper that crashes
        # on every call is eventually left alone.
        if not current.timed_out:
            with _lock:
                _failures += 1
            if _failures >= GIVE_UP_AFTER:
                return None
    path = helper_path()
    if path is None:
        return None
    try:
        made = Helper(path)
    except MusicError:
        with _lock:
            _failures += 1
        return None
    _local.helper = made
    with _lock:
        _helpers[:] = [h for h in _helpers if h.alive] + [made]
    return made


def backend():
    """"helper" or "osascript": what this thread's scripts go through."""
    return "helper" if _helper() is not None else "osascript"


@atexit.register
def _close_helpers():
    with _lock:
        for h in _helpers:
            h.close()
        del _helpers[:]


def tell(body, timeout=25.0):
    """Run `body` inside a `tell application "Music"` block."""
    return run(in_music(body), timeout=timeout)


def in_music(body):
    return 'tell application "Music"\n%s\nend tell' % body


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
