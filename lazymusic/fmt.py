"""Terminal formatting: colour, time codes, progress bars, panel boxes.

Rows are built as lists of `(text, *styles)` segments rather than pre-coloured
strings. Panels sit side by side, so every row has to be padded to an exact
column count - and once ANSI escapes are baked into a string you can no longer
measure it. Colour is therefore applied last, by `row()`, after the widths are
already settled.
"""

import os
import sys
import unicodedata

_ENABLED = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

_CODES = {
    "dim": "2", "bold": "1", "reverse": "7", "red": "31", "green": "32",
    "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "white": "37",
    "grey": "90", "brightgreen": "92", "brightcyan": "96",
}

# Rounded box drawing, as lazygit draws them.
TL, TR, BL, BR, H, V = "╭", "╮", "╰", "╯", "─", "│"


def c(text, *styles):
    if not _ENABLED or not styles:
        return text
    seq = ";".join(_CODES[s] for s in styles if s in _CODES)
    return "\033[%sm%s\033[0m" % (seq, text) if seq else text


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


def char_width(ch):
    """Columns a character occupies: 0 for combining marks, 2 for wide, else 1.

    Combining marks matter here as much as CJK does - Devanagari track titles
    carry a vowel sign or virama on most syllables, and counting those as a
    column each makes every such row pad short and tear the panel edge.
    """
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def width_of(text):
    return sum(char_width(ch) for ch in text)


def truncate(text, width):
    """Trim to `width` columns, accounting for wide and zero-width glyphs."""
    if width <= 0:
        return ""
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


def pad(text, width):
    return text + " " * max(0, width - width_of(text))


def row(segments, width, styles=()):
    """Render `(text, *styles)` segments into exactly `width` visible columns.

    `styles` applies to the padding too, which is what lets a selected row show
    an unbroken highlight across the full panel width.
    """
    out, used = [], 0
    for seg in segments:
        if used >= width:
            break
        text = truncate(seg[0], width - used)
        if not text:
            continue
        used += width_of(text)
        out.append(c(text, *(tuple(seg[1:]) + tuple(styles))))
    if used < width:
        out.append(c(" " * (width - used), *styles) if styles else " " * (width - used))
    return "".join(out)


def box(width, height, title, body, focused=False, footer=""):
    """Draw a bordered panel and return exactly `height` rows of `width` columns.

    `title` and `footer` are plain strings; `body` is a list of segment lists.
    Rows beyond the panel height are dropped - callers do their own scrolling.
    """
    if width < 4 or height < 2:
        return [" " * width] * max(0, height)
    edge = ("brightcyan", "bold") if focused else ("grey",)
    inner = width - 2

    title = truncate(title, max(0, inner - 4))
    lead = TL + H + (" " + title + " " if title else H)
    top = lead + H * max(0, width - width_of(lead) - 1) + TR

    foot = truncate(footer, max(0, inner - 4))
    tail = (" " + foot + " " if foot else H) + H + BR
    bottom = BL + H * max(0, width - width_of(tail) - 1) + tail

    lines = [c(top, *edge)]
    for i in range(height - 2):
        content = body[i] if i < len(body) else []
        lines.append(c(V, *edge) + row(content, inner) + c(V, *edge))
    lines.append(c(bottom, *edge))
    return lines
