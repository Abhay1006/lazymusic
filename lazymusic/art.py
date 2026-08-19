"""Cover art as text: JPEG bytes in, coloured terminal rows out.

Alacritty, Terminal.app and most of what people run lazymusic under support
none of the inline-image protocols (sixel, kitty, iTerm2), and tmux gets in the
way of the ones that do. Truecolor, though, is universal, and so are the block
glyphs - so a cell is painted as a block whose foreground and background carry
two different colours, and the shape of the block says which part of the cell
gets which. A half block splits a cell in two; the quadrant, sextant and octant
families split it into 4, 6 and 8, which is four times the detail in the same
space by the time you reach octants.

Every family divides its cell to match the terminal's own 1:2 shape, so the
sampled pixels line up with the subcells exactly and the picture comes out
square without any stretching. What the newer families cost is colour: a glyph
paints only two, so each cell is cut at the midpoint of its luminance range.

Decoding is handed to `sips`, which ships with macOS. It downsamples the cover
to exactly the pixel grid wanted and re-encodes it as a 24-bit BMP - a format
with no compression to undo, so reading it back is a header and a byte loop
rather than a dependency on Pillow.
"""

import os
import struct
import subprocess

from .fmt import Raw, bg, color_enabled, fg

BLOCK = "▀"          # foreground is the top pixel, background the bottom one

# Each entry is (subcells wide, subcells tall). More subcells means more pixels
# in the same screen space, which on a large font is the only way a cover
# becomes readable at all - but every glyph past `half` paints just two colours
# per cell, and each family is newer Unicode than the last:
#
#   half  1x2   full colour, exact          U+2580        universal
#   quad  2x2   2x the pixels               U+2596-259F   Unicode 3.2, 2002
#   sext  2x3   3x the pixels               U+1FB00-1FB3B Unicode 13, 2020
#   oct   2x4   4x the pixels               U+1CD00-1CDE5 Unicode 16, 2024
#
# The default is deliberately the conservative one that every terminal can
# draw. A terminal without the newer glyphs shows tofu or question marks rather
# than failing, and there is no way to ask it in advance - `:art test` puts the
# families on screen so you can see which ones your terminal has.
GLYPH_CELLS = {"half": (1, 2), "quad": (2, 2), "sext": (2, 3), "oct": (2, 4)}
GLYPH_ORDER = ("half", "quad", "sext", "oct")
DEFAULT_GLYPH = "quad"


_override = None


def glyph_table(mode):
    """(subcells wide, subcells tall, glyphs, swap set) for `mode`."""
    wide, tall = GLYPH_CELLS[mode]
    table = {"quad": (QUAD_GLYPHS, QUAD_SWAP), "sext": (SEXT_GLYPHS, SEXT_SWAP),
             "oct": (OCT_GLYPHS, OCT_SWAP)}.get(mode)
    return (wide, tall) + (table or (None, None))


def set_glyph(mode):
    """Switch glyph for this session, or pass None to fall back to the
    environment again. False if `mode` is not one we draw with."""
    global _override
    if mode is None:
        _override = None
        return True
    if mode not in GLYPH_CELLS:
        return False
    _override = mode
    return True


def glyph_mode():
    """Which block glyph to draw with.

    `half` is the fallback for terminals that leave the Unicode 16 octants as
    empty boxes; `:art half` switches at runtime and LAZYMUSIC_ART_GLYPH sets
    it at startup.
    """
    if _override:
        return _override
    want = os.environ.get("LAZYMUSIC_ART_GLYPH", "").strip().lower()
    return want if want in GLYPH_CELLS else DEFAULT_GLYPH


# Glyphs indexed by a bit pattern; bit i is subcell i, counting left to right
# then top to bottom, which is the order the Unicode sextant and octant names
# use. Generated from Unicode 16 rather than typed out, and embedded rather than
# looked up at import so the tables do not depend on the Unicode version of
# whichever Python is running. Eight of the 256 octant patterns were never given
# a codepoint of their own; for those the glyph is the complement and the SWAP
# set says to exchange the two colours, which draws the very same picture.
QUAD_GLYPHS = ' ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█'
QUAD_SWAP = frozenset([])
SEXT_GLYPHS = ' 🬀🬁🬂🬃🬄🬅🬆🬇🬈🬉🬊🬋🬌🬍🬎🬏🬐🬑🬒🬓▌🬔🬕🬖🬗🬘🬙🬚🬛🬜🬝🬞🬟🬠🬡🬢🬣🬤🬥🬦🬧▐🬨🬩🬪🬫🬬🬭🬮🬯🬰🬱🬲🬳🬴🬵🬶🬷🬸🬹🬺🬻█'
SEXT_SWAP = frozenset([])
OCT_GLYPHS = ' 𜷥𜷤▆𜴀▘𜴁𜴂𜴃𜴄▝𜴅𜴆𜴇𜴈▀𜴉𜴊𜴋𜴌𜷖𜴍𜴎𜴏𜴐𜴑𜴒𜴓𜴔𜴕𜴖𜴗𜴘𜴙𜴚𜴛𜴜𜴝𜴞𜴟𜷂𜴠𜴡𜴢𜴣𜴤𜴥𜴦𜴧𜴨𜴩𜴪𜴫𜴬𜴭𜴮𜴯𜴰𜴱𜴲𜴳𜴴𜴵▂𜶫𜴶𜴷𜴸𜴹𜴺𜴻𜴼𜴽𜴾𜴿𜵀𜵁𜵂𜵃𜵄▖𜵅𜵆𜵇𜵈▌𜵉𜵊𜵋𜵌▞𜵍𜵎𜵏𜵐▛𜵑𜵒𜵓𜵔𜵕𜵖𜵗𜵘𜵙𜵚𜵛𜵜𜵝𜵞𜵟𜵠𜵡𜵢𜵣𜵤𜵥𜵦𜵧𜵨𜵩𜵪𜵫𜵬𜵭𜵮𜵯𜵰𜵰𜵱𜵲𜵳𜵴𜵵𜵶𜵷𜵸𜵹𜵺𜵻𜵼𜵽𜵾𜵿𜶀𜶁𜶂𜶃𜶄𜶅𜶆𜶇𜶈𜶉𜶊𜶋𜶌𜶍𜶎𜶏▗𜶐𜶑𜶒𜶓▚𜶔𜶕𜶖𜶗▐𜶘𜶙𜶚𜶛▜𜶜𜶝𜶞𜶟𜶠𜶡𜶢𜶣𜶤𜶥𜶦𜶧𜶨𜶩𜶪𜶫▂𜶬𜶭𜶮𜶯𜶰𜶱𜶲𜶳𜶴𜶵𜶶𜶷𜶸𜶹𜶺𜶻𜶼𜶽𜶾𜶿𜷀𜷁𜷂𜷃𜷄𜷅𜷆𜷇𜷈𜷉𜷊𜷋𜷌𜷍𜷎𜷏𜷐𜷑𜷒𜷓𜷔𜷕𜷖𜷗𜷘𜷙𜷚▄𜷛𜷜𜷝𜷞▙𜷟𜷠𜷡𜷢▟𜷣▆𜷤𜷥█'
OCT_SWAP = frozenset([1, 2, 3, 20, 40, 63, 64, 128])


def cell_size(rows, glyph=None):
    """Pixel grid and column count needed to draw `rows` text rows squarely.

    A terminal cell is about twice as tall as it is wide, so a square image
    wants twice as many columns as rows. Each glyph then subdivides its cell,
    and because the subdivisions are chosen to match that 1:2 cell shape the
    resulting pixels come out square either way - half blocks give 1x2 per
    cell, octants 2x4.
    """
    wide, tall = GLYPH_CELLS[glyph or glyph_mode()]
    cols = rows * 2
    return cols, (cols * wide, rows * tall)


def to_bmp(src, dst, pixel_w, pixel_h):
    """Resample `src` to a 24-bit BMP of the given pixel size. False on failure."""
    try:
        p = subprocess.run(
            ["sips", "-s", "format", "bmp", "-z", str(pixel_h), str(pixel_w),
             src, "--out", dst],
            capture_output=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def read_bmp(path):
    """Return (width, height, rows-of-(r,g,b)) for an uncompressed BMP.

    Only the shapes `sips` actually emits are handled: 24- and 32-bit pixel
    arrays, either bottom-up (a positive height, which is the usual case) or
    top-down. Anything else is refused rather than guessed at.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    depth = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if depth not in (24, 32) or compression != 0:
        raise ValueError("unsupported BMP: %d-bit, compression %d"
                         % (depth, compression))
    step = depth // 8
    top_down = height < 0
    height = abs(height)
    stride = (width * step + 3) // 4 * 4    # BMP rows are padded to 4 bytes
    if offset + stride * height > len(data):
        raise ValueError("truncated BMP")

    pixels = []
    for y in range(height):
        # A positive height means the first row in the file is the bottom one.
        src = y if top_down else height - 1 - y
        base = offset + src * stride
        pixels.append([(data[base + x * step + 2], data[base + x * step + 1],
                        data[base + x * step]) for x in range(width)])
    return width, height, pixels


def rows_from_pixels(pixels, width, height):
    """Fold a pixel grid into half-block rows, two pixel rows per text row.

    The escapes are written by hand rather than through `c()`, which would end
    every cell with a reset and so send twice the bytes. A cover is redrawn on
    every frame while the progress bar moves, so instead each row resets once at
    its end, and a colour pair identical to the one before it is not resent at
    all - which is most of a row, given album covers are largely flat areas.
    """
    out = []
    for y in range(0, height - height % 2, 2):
        top, bottom = pixels[y], pixels[y + 1]
        parts, last = [], None
        for x in range(width):
            pair = (top[x], bottom[x])
            if pair != last:
                parts.append("\033[%s;%sm" % (fg(*pair[0]), bg(*pair[1])))
                last = pair
            parts.append(BLOCK)
        parts.append("\033[0m")
        # Stored as a Raw so the panel code never tries to measure the escapes.
        out.append(Raw("".join(parts), width))
    return out


def two_tone(cell):
    """Reduce one cell's pixels to a foreground, a background and a bit pattern.

    A glyph paints exactly two colours, so the cell is cut at the midpoint of
    its own luminance range: brighter pixels become the foreground, darker ones
    the background, and each side is averaged. Splitting on brightness rather
    than on the mean colour is what keeps an edge sharp - a pale caption over a
    dark cover stays legible instead of smearing into the average of the two.
    """
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in cell]
    low, high = min(lum), max(lum)
    if high - low < 1.0:               # a flat cell has no edge to preserve
        return cell[0], cell[0], 0
    mid = (low + high) / 2.0
    fore = [c for c, l in zip(cell, lum) if l > mid]
    back = [c for c, l in zip(cell, lum) if l <= mid]
    bits = 0
    for i, l in enumerate(lum):
        if l > mid:
            bits |= 1 << i
    average = lambda g: tuple(sum(c[i] for c in g) // len(g) for i in range(3))
    return average(fore), average(back), bits


def rows_from_cells(pixels, width, height, wide, tall, glyphs, swap):
    """Fold a pixel grid into glyph rows of `wide` x `tall` subcells each."""
    out = []
    offsets = [(dy, dx) for dy in range(tall) for dx in range(wide)]
    for cy in range(height // tall):
        parts, last = [], None
        for cx in range(width // wide):
            cell = [pixels[cy * tall + dy][cx * wide + dx] for dy, dx in offsets]
            fore, back, bits = two_tone(cell)
            if bits in swap:
                # No codepoint for this pattern; its complement drawn with the
                # colours the other way round is the identical picture.
                fore, back = back, fore
            pair = (fore, back)
            if pair != last:
                parts.append("\033[%s;%sm" % (fg(*fore), bg(*back)))
                last = pair
            parts.append(glyphs[bits])
        parts.append("\033[0m")
        out.append(Raw("".join(parts), width // wide))
    return out


def sample_rows(width):
    """A look at every glyph family, so you can see which your terminal draws.

    There is no way to ask a terminal whether it has a glyph - a missing one
    still occupies its cell - so the only honest test is to put them on screen.
    """
    out = []
    for mode in GLYPH_ORDER:
        wide, tall, glyphs, _swap = glyph_table(mode)
        if glyphs is None:
            glyphs = " ▀█"          # `half` needs no table; show what it uses
        label = "%-5s %dx%d  %d px/cell" % (mode, wide, tall, wide * tall)
        step = max(1, len(glyphs) // max(1, width - len(label) - 4))
        shown = "".join(glyphs[i] for i in range(0, len(glyphs), step))
        out.append((mode, label, shown[:max(0, width - len(label) - 4)]))
    return out


def render(src, rows, scratch, glyph=None):
    """Cover art at `src` as `rows` coloured rows. Returns [] if it cannot.

    `scratch` is the path the intermediate BMP is written to; the caller owns it
    so the whole thing stays free of temporary-file bookkeeping.
    """
    if not color_enabled():
        return []                     # a cover in monochrome is just noise
    mode = glyph or glyph_mode()
    _cols, (pixel_w, pixel_h) = cell_size(rows, mode)
    if not to_bmp(src, scratch, pixel_w, pixel_h):
        return []
    try:
        width, height, pixels = read_bmp(scratch)
    except (OSError, ValueError):
        return []
    wide, tall, glyphs, swap = glyph_table(mode)
    if glyphs is None:                # half blocks keep every pixel's colour
        return rows_from_pixels(pixels, width, height)[:rows]
    return rows_from_cells(pixels, width, height, wide, tall, glyphs, swap)[:rows]
