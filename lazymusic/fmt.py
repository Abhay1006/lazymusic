"""Terminal formatting: colour, time codes, progress bars, panel boxes.

Rows are built as lists of `(text, *styles)` segments rather than pre-coloured
strings. Panels sit side by side, so every row has to be padded to an exact
column count - and once ANSI escapes are baked into a string you can no longer
measure it. Colour is therefore applied last, by `row()`, after the widths are
already settled.
"""

import functools
import os
import re
import sys
import unicodedata

_ENABLED = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

_CODES = {
    "dim": "2", "bold": "1", "reverse": "7", "red": "31", "green": "32",
    "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    "grey": "90", "brightred": "91", "brightgreen": "92", "brightcyan": "96",
    "brightwhite": "97",
}

# Role name -> SGR parameters, filled in by `theme.apply`. Roles are what the
# interface asks for ("muted", "focus", ...); the theme decides what they look
# like. An empty value is a role that adds nothing in the current theme.
_ROLES = {}

# Rounded box drawing, as lazygit draws them.
TL, TR, BL, BR, H, V = "╭", "╮", "╰", "╯", "─", "│"

_SGR = re.compile(r"^[0-9;]+$")
_ESCAPE = re.compile(r"\033\[[0-9;]*m")


class Tab:
    """A column stop inside a row: whatever follows starts at column `col`.

    Within a padded row this is just spacing. In an anchored row (see `box`) it
    also moves the cursor there absolutely, so a title the terminal draws wider
    or narrower than we measured cannot push the next column out of line.
    """

    __slots__ = ("col",)

    def __init__(self, col):
        self.col = col


class Line(list):
    """A body row whose `styles` cover its full width, padding included.

    That is what lets a selected row show an unbroken highlight band.
    """

    def __init__(self, segments=(), styles=()):
        super().__init__(segments)
        self.styles = tuple(styles)


class Raw(str):
    """A string that already carries its own escapes, with its width recorded.

    Album art is one coloured segment per character cell, which is thousands of
    tiny segments a frame. Measuring each of those through `width_of` costs more
    than drawing them. Art rows are built once per track instead and handed over
    whole; `cols` is what `row` uses in place of measuring.
    """

    def __new__(cls, text, cols):
        self = super().__new__(cls, text)
        self.cols = cols
        return self


def fg(r, g, b):
    """A 24-bit foreground style token, usable anywhere a colour name is."""
    return "38;2;%d;%d;%d" % (r, g, b)


def bg(r, g, b):
    return "48;2;%d;%d;%d" % (r, g, b)


def cha(col):
    """Move the cursor to absolute column `col` (1-based) on the current row."""
    return "\033[%dG" % col


def ech(count):
    """Erase `count` cells from the cursor without moving it."""
    return "\033[%dX" % count if count > 0 else ""


def c(text, *styles):
    if not _ENABLED or not styles or not text:
        return text
    # A style is a theme role, a name from the table, or SGR parameters already
    # (what `fg` and `bg` produce), which are passed straight through.
    parts = []
    for s in styles:
        code = _ROLES.get(s)
        if code is None:
            code = _CODES.get(s) or (s if _SGR.match(s) else "")
        if code:
            parts.append(code)
    seq = ";".join(parts)
    return "\033[%sm%s\033[0m" % (seq, text) if seq else text


def strip_ansi(text):
    """Text with its colour escapes removed, so it can be measured or compared."""
    return _ESCAPE.sub("", text)


def set_roles(roles):
    """Replace the role table wholesale; see `theme.apply`."""
    global _ROLES
    _ROLES = dict(roles)


def set_color(enabled):
    global _ENABLED
    _ENABLED = bool(enabled)


def color_enabled():
    return _ENABLED


def mmss(seconds):
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def bar(fraction, width, filled="━", empty="─", head="●"):
    """A progress bar with a playhead, exactly `width` columns wide."""
    width = max(3, width)
    fraction = min(1.0, max(0.0, fraction))
    pos = int(round(fraction * (width - 1)))
    return filled * pos + head + empty * (width - pos - 1)


# Marks that occupy no column of their own. `Mc` is the subtle one: Unicode
# classes it as a *spacing* mark, but a terminal composes it onto the base letter
# and advances the cursor once for the pair - every Devanagari matra (ा ि ो),
# and the same marks in Bengali, Tamil, Gujarati and Kannada, behave this way.
#
# "A terminal" is doing a lot of work there, though: tmux on macOS measures a
# matra as zero columns and tmux on Linux as one, and nothing can ask which is
# in front of us. These widths are therefore only ever a best guess, and `box`
# is built so that a wrong guess costs a little spacing rather than the layout.
_ZERO_WIDTH = ("Mn", "Me", "Mc", "Cf")


@functools.lru_cache(maxsize=8192)
def char_width(ch):
    """Columns a character occupies: 0 for combining marks, 2 for wide, else 1."""
    if unicodedata.combining(ch) or unicodedata.category(ch) in _ZERO_WIDTH:
        return 0
    o = ord(ch)
    # Hangul vowel and final jamo join the syllable before them.
    if 0x1160 <= o <= 0x11FF or 0xD7B0 <= o <= 0xD7FF:
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def width_of(text):
    if text.isascii():
        return len(text)              # the overwhelmingly common case
    return sum(char_width(ch) for ch in text)


@functools.lru_cache(maxsize=8192)
def _wide_char(ch):
    return 1 if unicodedata.category(ch) == "Mc" else char_width(ch)


def widest(text):
    """The most columns any terminal might give `text`.

    Identical to `width_of` except that spacing marks (`Mc`) count as one
    column, as some terminals draw them. Wrapping by this bound means a line
    never comes out wider than its panel, whichever kind of terminal it is.
    """
    if text.isascii():
        return len(text)
    return sum(_wide_char(ch) for ch in text)


# Characters that must never reach the terminal from a song title or a lyric.
# Control codes move the cursor (a tab jumps to the next tab stop and drags the
# rest of the row with it); bidi overrides and isolates can make a terminal
# that does its own bidi reorder the whole row, borders included; a byte-order
# mark or a soft hyphen is drawn by some terminals and dropped by others.
_BLANKS = re.compile("[\t\n\r\v\f\u0085\u2028\u2029]")
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\u00ad\u061c\u200e\u200f"
                     "\u202a-\u202e\u2066-\u2069\ufeff\ufff9-\ufffb]")


def clean(text):
    """`text` made safe to print inside a panel: one line, no control codes."""
    if text.isascii() and text.isprintable():
        return text
    return _UNSAFE.sub("", _BLANKS.sub(" ", text))


def truncate(text, width):
    """Trim to `width` columns, accounting for wide and zero-width glyphs."""
    if width <= 0:
        return ""
    if text.isascii():
        return text if len(text) <= width else text[:width - 1] + "…"
    if width_of(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - 1:      # leave room for the ellipsis
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def wrap(text, width):
    """Split `text` into lines of at most `width` columns.

    Breaks at spaces where there are any; scripts written without them (Chinese,
    Japanese, Thai) are broken between characters instead, never inside one.
    Measured by `widest`, so a wrapped line fits whichever way the terminal
    counts it.
    """
    text = clean(text).strip()
    if width <= 0 or not text:
        return [text] if text else []
    if widest(text) <= width:
        return [text]
    lines, current, used = [], "", 0
    for word in text.split(" "):
        w = widest(word)
        if current and used + 1 + w <= width:
            current += " " + word
            used += 1 + w
            continue
        if current:
            lines.append(current)
        current, used = "", 0
        # A word longer than the line is cut between characters, keeping each
        # base letter together with the marks that follow it.
        for ch in word:
            cw = _wide_char(ch)
            if used + cw > width and current and char_width(ch):
                lines.append(current)
                current, used = "", 0
            current += ch
            used += cw
    if current:
        lines.append(current)
    return lines


def pad(text, width):
    return text + " " * max(0, width - width_of(text))


def row(segments, width, styles=(), origin=None):
    """Render `(text, *styles)` segments into exactly `width` visible columns.

    `styles` applies to the padding too, which is what lets a selected row show
    an unbroken highlight across the full panel width. `origin`, when given, is
    the screen column the row starts at, and makes every `Tab` an absolute
    cursor move rather than trusting the widths measured so far.
    """
    out, used = [], 0
    for seg in segments:
        text = seg[0]
        if isinstance(text, Tab):
            col = max(0, min(text.col, width))
            if used < col:
                gap = " " * (col - used)
                out.append(c(gap, *styles) if styles else gap)
            if origin is not None:
                out.append(cha(origin + col))
                used = col
            else:
                used = max(used, col)
            continue
        if used >= width:
            continue                  # a later Tab may still bring us back
        if isinstance(text, Raw):
            if used + text.cols > width:
                continue          # pre-rendered and unsplittable; drop it whole
            used += text.cols
            out.append(text)
            continue
        text = truncate(clean(text), width - used)
        if not text:
            continue
        used += width_of(text)
        out.append(c(text, *(tuple(seg[1:]) + tuple(styles))))
    if used < width:
        out.append(c(" " * (width - used), *styles) if styles else " " * (width - used))
    return "".join(out)


def box(width, height, title, body, focused=False, footer="", x=None,
        edge=None):
    """Draw a bordered panel and return exactly `height` rows of `width` columns.

    `title` and `footer` are plain strings; `body` is a list of segment lists
    (or `Line`s). Rows beyond the panel height are dropped - callers do their
    own scrolling.

    With `x` (the 0-based screen column of the panel's left edge) the panel is
    *anchored*: every border is placed with an absolute cursor move and the
    interior is erased before it is drawn. That is what keeps the layout intact
    when the terminal disagrees with us about how wide some text is - Hindi,
    Tamil, Urdu, emoji and combining marks are all measured differently by
    different terminals. A line drawn wider than measured is overwritten by its
    own border; one drawn narrower leaves blank cells, never stale ones.
    """
    if width < 4 or height < 2:
        blank = " " * width
        rows = [blank if x is None else cha(x + 1) + blank] * max(0, height)
        return rows
    if edge is None:
        edge = ("focus", "bold") if focused else ("border",)
    label = ("focus", "bold") if focused else ("text",)
    inner = width - 2

    title = truncate(clean(title), max(0, inner - 4))
    foot = truncate(clean(footer), max(0, inner - 4))
    tail = (" " + foot + " " if foot else H) + H + BR

    if x is None:
        named = " " + title + " " if title else ""
        rest = H * max(0, width - 3 - width_of(named)) + TR
        top = c(TL + H, *edge) + c(named, *label) + c(rest, *edge)
        bottom = c(BL + H * max(0, width - width_of(tail) - 1) + tail, *edge)
        lines = [top]
        for i in range(height - 2):
            content = body[i] if i < len(body) else []
            lines.append(c(V, *edge) + row(content, inner, getattr(content, "styles", ()))
                         + c(V, *edge))
        lines.append(bottom)
        return lines

    left, right = cha(x + 1), cha(x + width)
    rule = H * (width - 2)
    top = left + c(TL + rule + TR, *edge)
    if title:
        top += cha(x + 3) + " " + c(title, *label) + " " + right + c(TR, *edge)
    bottom = left + c(BL + rule + BR, *edge)
    if foot:
        bottom += cha(x + width - width_of(tail) + 1) + c(tail, *edge) \
            + right + c(BR, *edge)
    lines = [top]
    wall = c(V, *edge)
    erase = ech(inner)
    for i in range(height - 2):
        content = body[i] if i < len(body) else []
        lines.append(left + wall + erase
                     + row(content, inner, getattr(content, "styles", ()),
                           origin=x + 2)
                     + right + wall)
    lines.append(bottom)
    return lines
