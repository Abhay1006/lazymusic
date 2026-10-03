"""Colour themes: every colour in the interface, by the job it does.

Nothing outside this module names a colour. Rows ask for a *role* - `muted`
for secondary text, `focus` for whatever has the keyboard, `selection` for the
band behind the cursor row - and `fmt.c` looks the role up in the active theme.
Swapping the theme therefore recolours the whole interface at once, the panels,
the key bar and the command-line output alike.

Pick one with LAZYMUSIC_THEME, or `:theme <name>` while the UI is up.

Each theme maps every role to either `#rrggbb` (24-bit, like the cover art
already needs) or raw SGR parameters, which is how `classic` keeps to the
sixteen standard colours and `mono` uses no colour at all.
"""

import os

from . import fmt

# What each role is for. The keys are the contract every theme must fill.
ROLES = {
    "border": "the frame of a panel that does not have focus",
    "focus": "the focused panel's frame and title, the cursor mark, key hints",
    "muted": "secondary text: artists, durations, hints, empty-panel notes",
    "text": "ordinary text",
    "strong": "the selected row's text in the focused panel",
    "cursor": "the selected row in a panel without focus",
    "accent": "progress, the playing track, the lyric being sung",
    "selection": "the band behind the selected row (a background)",
    "note": "paused, the sleep timer, info messages, section headings",
    "error": "error messages",
    "ok": "confirmations",
    "love": "the heart on a loved track",
}

THEMES = {
    # The original look, on the terminal's own sixteen colours, so it follows
    # whatever palette the terminal is set to.
    "classic": {
        "border": "90", "focus": "96", "muted": "90", "text": "37",
        "strong": "97", "cursor": "36", "accent": "92", "selection": "48;5;236",
        "note": "33", "error": "91", "ok": "92", "love": "91",
    },
    # Catppuccin Mocha.
    "catppuccin": {
        "border": "#585b70", "focus": "#cba6f7", "muted": "#7f849c",
        "text": "#cdd6f4", "strong": "#f5e0dc", "cursor": "#89b4fa",
        "accent": "#a6e3a1", "selection": "#313244", "note": "#f9e2af",
        "error": "#f38ba8", "ok": "#a6e3a1", "love": "#f38ba8",
    },
    # Gruvbox dark.
    "gruvbox": {
        "border": "#665c54", "focus": "#fe8019", "muted": "#928374",
        "text": "#ebdbb2", "strong": "#fbf1c7", "cursor": "#83a598",
        "accent": "#b8bb26", "selection": "#3c3836", "note": "#fabd2f",
        "error": "#fb4934", "ok": "#b8bb26", "love": "#fb4934",
    },
    "nord": {
        "border": "#4c566a", "focus": "#88c0d0", "muted": "#7b88a1",
        "text": "#d8dee9", "strong": "#eceff4", "cursor": "#81a1c1",
        "accent": "#a3be8c", "selection": "#3b4252", "note": "#ebcb8b",
        "error": "#bf616a", "ok": "#a3be8c", "love": "#bf616a",
    },
    # No colour at all: weight, dimming, underline and reverse video carry it.
    # "" means the role adds nothing and the text takes the terminal default.
    "mono": {
        "border": "2", "focus": "1", "muted": "2", "text": "",
        "strong": "1", "cursor": "4", "accent": "1", "selection": "7",
        "note": "1", "error": "1;4", "ok": "1", "love": "1",
    },
}

# A theme that is one colour by design would be undone by tinting it with the
# cover, so these keep their own accent unless it is asked for explicitly.
NO_COVER = ("mono",)

DEFAULT = "classic"

_current = DEFAULT
_cover = None           # None: the theme decides. True/False: overridden.


def names():
    return sorted(THEMES)


def current():
    return _current


def _sgr(role, value):
    if not value.startswith("#"):
        return value
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return fmt.bg(r, g, b) if role == "selection" else fmt.fg(r, g, b)


def apply(name):
    """Make `name` the active theme. Raises KeyError for an unknown one."""
    global _current
    name = name.strip().lower()
    spec = THEMES[name]
    fmt.set_roles({role: _sgr(role, value) for role, value in spec.items()})
    _current = name


def cover_accent():
    """Whether the accent is taken from the playing track's cover."""
    if _cover is not None:
        return _cover
    return _current not in NO_COVER


def set_cover_accent(on):
    """True or False overrides the theme; None hands the choice back to it."""
    global _cover
    _cover = on


def from_env():
    """Apply LAZYMUSIC_THEME and LAZYMUSIC_ACCENT. Returns a problem, or "".

    An unknown theme name falls back to the default rather than refusing to
    start: a typo in a shell profile should not cost the whole interface.
    """
    problem = ""
    want = os.environ.get("LAZYMUSIC_THEME", "").strip().lower() or DEFAULT
    try:
        apply(want)
    except KeyError:
        apply(DEFAULT)
        problem = "unknown theme %r (LAZYMUSIC_THEME) - try: %s" % (
            want, ", ".join(names()))
    accent = os.environ.get("LAZYMUSIC_ACCENT", "").strip().lower()
    if accent in ("cover", "on", "1", "yes"):
        set_cover_accent(True)
    elif accent in ("theme", "off", "0", "no"):
        set_cover_accent(False)
    return problem


apply(DEFAULT)          # so anything printed before `from_env` still has colour
