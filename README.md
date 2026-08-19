# lazymusic

A lazygit-style terminal UI for Apple Music. Browse playlists, search your
library, queue up what plays next, read the lyrics, look at the cover and
control playback without ever bringing Music.app to the front.

No dependencies — just Python 3 and the Music.app that ships with macOS.

```
╭─ 1 Player ─────────────────────────╮╭─ 3 Evening ────────────────────────────────────────────────╮
│  ▶  Clair de Lune                  ││   1 Gymnopédie No. 1                   Erik Satie          │
│     Claude Debussy                 ││   2 Clair de Lune                      Claude Debussy      │
│   1:36 ━━━━━━●───────────── 5:05   ││   3 Prelude in E Minor                 Frédéric Chopin     │
│   shuffle  repeat one  ♥           ││   4 The Swan                           Camille Saint-Saëns │
│   vol ▪▪▪▪▪▪▪▪·· 80%               ││▸  5 Air on the G String                J. S. Bach          │
╰────────────────────────────────────╯│   6 Adagio for Strings                 Samuel Barber       │
╭─ 2 Playlists ──────────────────────╮│   7 Nocturne Op. 9 No. 2               Frédéric Chopin     │
│▸ Evening                         42││▶  8 Canon in D                         Johann Pachelbel    │
│  Focus                          118││   9 Moonlight Sonata                   Ludwig van Beethoven│
│  Road Trip                       64││  10 Ave Maria                          Franz Schubert      │
│  Rainy Day                       27││  11 Für Elise                          Ludwig van Beethoven│
│  Workout                         55││  12 Méditation                         Jules Massenet      │
│  Jazz                            90││  13 Vocalise                           Sergei Rachmaninoff │
│  Classical                      210││  14 Salut d'Amour                      Edward Elgar        │
│  Covers                          33││  15 Liebestraum No. 3                  Franz Liszt         │
│                                    ││  16 Berceuse                           Gabriel Fauré       │
│                                    ││  17 Élégie                             Jules Massenet      │
│                                    ││  18 Romance                            Robert Schumann     │
│                                    ││  19 Barcarolle                         Jacques Offenbach   │
│                                    ││  20 Chanson de Matin                   Edward Elgar        │
│                                    ││                                                            │
╰────────────────────────────────────╯╰────────────────────────────────────────────────────────────╯
 space play/pause   j/k move   h/l panel   enter play   a art   y lyrics   ? help   q quit          
```

The focused panel takes a bright border. `▸` is your cursor, green `▶` is the
track actually playing. Navigation is vim-style throughout — see below.

## Install

```sh
ln -s ~/Desktop/projects/lazymusic/bin/lazymusic ~/.local/bin/lazymusic
ln -s ~/Desktop/projects/lazymusic/bin/lazymusic ~/.local/bin/mus   # short alias
```

Any directory on your `PATH` works; the launcher resolves symlinks and finds its
own source tree. The first run makes macOS ask permission to control Music —
approve it once and you're done.

## Use

```sh
lazymusic             # open the UI
mus kun faya kun      # or skip the UI: find it in your library and play it
```

`mus` is just a shorter alias for the same program.

Music.app does have to be *running* — lazymusic is a remote control for it, not
a player of its own — but you should never see it. If it is down, lazymusic
starts it hidden and in the background, so no window comes over your terminal.

Inside tmux the window renames itself to `lazymusic` while the UI is up (it
would otherwise be called `python`, after the process running it) and goes back
to its old name on quit.

### In the UI — vim keys

Motions take a count, so `12j` moves down twelve rows and `40G` jumps to row 40.

| Motion | | Panels | |
| --- | --- | --- | --- |
| `j` / `k` | down / up | `h` / `l` | left stack / main panel |
| `gg` / `G` | first / last | `ctrl-w` `hjkl` | focus by direction |
| `<count>G` | go to that row | `ctrl-w` `1`–`3` | focus by number |
| `ctrl-d` / `ctrl-u` | half page | `tab` / `shift-tab` | cycle panels |
| `ctrl-f` / `ctrl-b` | full page | | |
| `{` / `}` | up / down 10 | **Search** | |
| `H` / `M` / `L` | top / middle / bottom | `/` | filter this panel |
| `zz` / `zt` / `zb` | centre / top / bottom | `esc` | clear the filter |
| | | `:search <q>` | search the library |
| **Playback** | | `S` | same, prefilled |
| `space` | play / pause | `*` | search the artist under the cursor |
| `n` / `p` | next / previous track | `ctrl-o` | back to the previous view |
| `R` | reload the library from Music |
| `[` / `]` | seek ∓10s | | |
| `+` / `-` | volume | `enter` | play the selection |
| `s` `r` `f` | shuffle, repeat, favourite | `?` | all keys |

| Views | | | |
| --- | --- | --- | --- |
| `a` | cover art | `u` | up next |
| `y` | lyrics | `esc` | back to the track list |
| `A` | queue the track under the cursor | `D` | remove it from the queue |

Each of these takes over the main panel and toggles off with the same key, the
way `?` always has. The left stack keeps playing, searching and browsing while
they are open.

Highlighting a playlist loads it into the main panel; `enter` there plays the
playlist from the start, or focus the main panel and pick a single track.

### The `:` command line

| Command | |
| --- | --- |
| `:q` | quit (`:quit`, `:qa`, `:x`) |
| `:42` | jump to row 42 |
| `:search <query>` | search the library (`:s`, `:find`) |
| `:filter <text>` | filter the focused panel; bare `:filter` clears it |
| `:play [query]` | resume, or find and play |
| `:pl <playlist>` | play a playlist |
| `:vol 60` · `:vol +10` | set or nudge volume |
| `:seek 90` · `:seek -15` | jump to, or by, seconds |
| `:shuffle [on\|off]` · `:repeat [off\|one\|all]` | |
| `:pause` · `:next` · `:prev` · `:fav` | |
| `:art` · `:lyrics` · `:upnext` | the main-panel views (`:tracks` goes back) |
| `:art test` | show every glyph family; see which your terminal draws |
| `:art half` · `quad` · `sext` · `oct` | cover glyph: 2, 4, 6 or 8 pixels per cell |
| `:upnext clear` | empty the queue (`:add` queues the selection) |
| `:reload` | re-read the library (same as `R`) |
| `:help` | the key list |

`tab` on the `:` line completes command names and lists the candidates. `?`
shows every key and every command in one panel.

**Deviations from vim, and why.** Horizontal motions (`w`, `b`, `e`, `f<char>`,
`0`, `$`) are absent — the panels are one-dimensional lists, so there is nothing
to move across. That frees `f` for favourite and `s` / `r` for shuffle / repeat.
`/` filters the list rather than jumping between matches, which is what lazygit
does and what is actually useful in a 1,200-track library; `n` / `p` therefore
keep their media meaning of next / previous track. `ctrl-i` (jumplist forward)
is unavailable because terminals send it as the same byte as `tab`.

### One-shot commands

| Command | What it does |
| --- | --- |
| `mus <query>` | search your library and play the best match |
| `mus status` / `--json` | now-playing summary, human or machine readable |
| `mus play [query]` | resume, or search and play |
| `mus pause` · `mus toggle` | pause; flip play/pause |
| `mus next` · `mus prev` | skip; back (or restart if past 3s) |
| `mus vol 60` · `+10` · `-10` | set or nudge volume |
| `mus seek 90` · `+15` · `-15` | jump to, or by, seconds |
| `mus shuffle [on\|off]` | toggles when given no argument |
| `mus repeat [off\|one\|all]` | cycles when given no argument |
| `mus love [on\|off]` | favourite the current track |
| `mus search <query>` | list matches without playing |
| `mus list [playlist]` | your playlists, or one playlist's tracks |
| `mus pl <playlist>` | play a playlist |

## How it works

Music.app exposes a scripting interface; lazymusic drives it through
`osascript`. That's why there's nothing to install and no API key to configure.

| File | Role |
| --- | --- |
| `lazymusic/osa.py` | runs AppleScript, escapes literals, normalises errors |
| `lazymusic/music.py` | the vocabulary: play, seek, search, playlists |
| `lazymusic/cli.py` | argument handling and one-shot output |
| `lazymusic/tui.py` | the panel UI |
| `lazymusic/fmt.py` | colour, time codes, progress bars, boxes, text widths |
| `lazymusic/art.py` | cover art as half-block cells |
| `lazymusic/lyrics.py` | lyrics from the file's tags, or from LRCLIB |

**Nothing touches Music.app on the draw loop.** Every osascript round trip
costs 70–150ms, and running those inline meant the UI froze for a large slice
of each second — which read as lag. `tui.Bus` runs them on a worker thread and
hands results back through a queue that the UI thread drains, so callbacks stay
single-threaded and no locks are needed.

**Start Music.app rather than letting AppleScript do it.** Any `tell
application "Music"` launches the app if it is not already up — loudly, with
its window landing on top of the terminal. `osa.launch` gets there first with
`open -g -j`, which starts it hidden and without focus, and `music.tell` routes
every request through that check. The check itself uses `pgrep` (≈5ms) instead
of asking System Events (≈80ms), because it sits on the polling path.

**Never write a newline on the bottom row.** Doing so scrolls the whole grid up
and silently eats the top border. The frame is joined with newlines *between*
rows only. `tests/` cannot catch this — reconstructing the escape stream is not
the same as knowing what the terminal grid holds — so verify with a real grid.

A few more things worth knowing if you edit this:

**Bulk property reads.** Asking Music for one property of one track costs a
round trip (~50ms), so looping over a 1,200-track playlist takes over a minute.
Asking for `name of every track of ...` returns the whole column in a single
Apple Event — the same playlist arrives in about 0.1s. The catch is the getter
must be applied to the `whose` clause itself; binding matches to a variable
first and asking for `name of res` fails with `-1700`.

**No search panel.** Search results fill the main panel, which is the only one
wide enough to read a track and its artist side by side — so a separate search
box showed the same hits again in a third of the width. It was removed and
playlists took the whole left column below the player. `/` still filters
whatever panel has focus; `S` and `:search` search the library.

**Widths before colour.** Panels are concatenated side by side, so every row
must be an exact number of columns — and once ANSI escapes are in a string you
can't measure it. Rows are therefore built as `(text, *styles)` segments and
coloured last, by `fmt.row()`. Combining marks count as zero columns and CJK as
two; getting that wrong tears the panel edge on non-Latin titles.

The subtle case is Unicode category **`Mc`**. Unicode classes it as a *spacing*
mark, so the obvious reading is one column — but a terminal composes it onto the
base letter and advances the cursor once for the pair. Every Devanagari matra
(ा ि ो) is `Mc`, as are the equivalent marks in Bengali, Tamil, Gujarati and
Kannada, so a Hindi lyric counted the naive way came out five or more columns
too wide, padded short, and walked the panel border left across the whole
layout. `Mc` is treated as zero-width alongside `Mn`, `Me` and `Cf`. This was
confirmed by measurement rather than by reading the standard: print a string in
a detached tmux session and read `#{cursor_x}` back for the width the emulator
actually used.

**Favourite, not loved.** macOS 15 renamed Love to Favorite and the old `loved`
property now raises `-10001`. `music.py` prefers `favorited` and falls back.

**Cover art without an image protocol.** Alacritty, Terminal.app and most of
what people run this under support none of sixel, the kitty graphics protocol
or iTerm2 inline images, and tmux gets in the way of the ones that do.
Truecolor is universal, so `art.py` draws each cell as an upper half block
`▀` — foreground paints the top pixel, background the bottom. That gives two
pixel rows per text row, which is also why a square cover needs twice as many
columns as rows. Decoding goes through `sips`, which ships with macOS: it
resamples straight to the pixel grid wanted and writes a 24-bit BMP, a format
with no compression to undo, so reading it back is a header and a byte loop
instead of a dependency on Pillow. A 600×600 cover costs ~190ms to fetch and
~18ms to render, both on the Bus, and the result is cached against the track's
persistent ID.

**A small cover looks better than a big one.** One block is one character cell,
so how large a cover appears is set by your font size, not by lazymusic — on a
large font a cover stretched to fill the panel is a few dozen enormous squares,
which reads as a broken photo. Kept small it reads as deliberate pixel art
instead. The cover is therefore capped (`ART_MAX`, 12 rows) rather than grown to
fit, and sits beside the track details rather than above them, since the main
panel is much wider than it is tall. `LAZYMUSIC_ART_ROWS=20` raises the cap for
anyone on a small font with cells to spare.

**More pixels per cell, not more cells.** Once the cover is small the cell count
is fixed, so the only way left to add detail is to fit more pixels into each
cell. Four families do this, each newer Unicode than the last:

| | subcells | pixels in a 24×12 cover | codepoints | since |
| --- | --- | --- | --- | --- |
| `half` | 1×2 | 24×24, full colour | `U+2580` | universal |
| `quad` | 2×2 | 48×24 | `U+2596`–`U+259F` | Unicode 3.2, 2002 |
| `sext` | 2×3 | 48×36 | `U+1FB00`–`U+1FB3B` | Unicode 13, 2020 |
| `oct` | 2×4 | 48×48 | `U+1CD00`–`U+1CDE5` | Unicode 16, 2024 |

All four cost the same bytes per frame and the same render time. The price past
`half` is that a glyph paints only two colours, so each cell is cut at the
midpoint of its own luminance range and each side averaged; on album art that
costs almost nothing, because covers are locally flat, and splitting on
brightness keeps a caption over a dark cover crisp instead of smearing it into
the mean.

Patterns are indexed by a bit mask, bit *i* being subcell *i* left to right then
top to bottom — the order the Unicode sextant and octant names use. Quadrants
and sextants are fully encoded; of the 256 octant patterns only 248 have a
codepoint, and the other eight draw their *complement* with the two colours
exchanged, which is the identical picture. The tables are generated from Unicode
16 and embedded in `art.py` rather than looked up at import, so they do not
depend on the Unicode version of whichever Python is running.

**A terminal cannot be asked which glyphs it has** — a missing one still
occupies its cell, so nothing distinguishes it from a real one until it is
drawn. (Do not infer support from codepoints appearing in a terminal's binary;
Alacritty 0.17 references all three ranges and still shows octants as question
marks.) The default is therefore `quad`, which every terminal can draw, and
`:art test` puts all four families on screen so you can see which yours has.
`:art sext` switches at runtime; `LAZYMUSIC_ART_GLYPH=sext` makes it stick.

**Music.app has no queue, so lazymusic keeps one as a playlist.** There is no
`up next` anywhere in its scripting dictionary, so nothing can read or write the
real play queue. `A` therefore appends to an ordinary user playlist named
`lazymusic queue`, made on first use so anyone who never queues anything is
never left with a stray playlist. Holding the queue in a playlist rather than in
a list in memory is what makes it work properly: Music plays a playlist natively
and in order, with its own shuffle and repeat, so nothing has to watch for the
end of a track and race to start the next one.

`duplicate` into a playlist preserves a track's persistent ID, so the playing
marker and removal still match it afterwards; removal is by **position** rather
than by ID, so queueing the same song twice and removing one copy does the
obvious thing. Note that `delete (track N of ...)` fails with `-1708` — the
parenthesised form resolves to an object with no delete handler — while the
plain command form works.

When the queue is empty, `u` falls back to showing the rest of the playlist the
current track is playing out of, which is exactly right with shuffle off and a
guess with it on, so the title says `~shuffled` rather than presenting a
confidently wrong list.

**Music.app lies about property writes.** `set shuffle enabled to true` returns
success and leaves the value at `false` whenever the playback engine has wedged
— the same state in which `play` and `next track` also silently do nothing,
which is why shuffle appeared to be broken rather than merely unavailable. Every
shuffle and repeat write is therefore read back, retried once (the refusal is
intermittent, and the same toggle usually lands a moment later) and only then
reported. A key that says why it did nothing beats one that just does nothing.

**Lyrics come from the tags first, the network second.** Music exposes a
`lyrics` property, but it only ever holds what is written into the file's own
tags; Apple Music's streaming lyrics are not scriptable, so on a mostly-streamed
library the property is empty for every track. When the tags are empty,
`lyrics.py` asks [LRCLIB](https://lrclib.net) — free, key-less, and often
holding an LRC transcript with a timestamp per line, which is what lets the
panel highlight the line currently being sung and scroll itself. Search results
are picked by closest duration, since relevance ordering happily returns a live
cut or a remix first. This is the only part of lazymusic that touches the
network; `LAZYMUSIC_LYRICS=tags` keeps everything local and `=off` disables
lyrics entirely.

**Records, not properties.** `properties of current track` fetches the whole
record in one event; reading each field separately cost a round trip apiece and
made the status query take 379ms instead of ~167ms. The floor for *any* query
to Music is ~77ms — process spawn plus Apple Event setup — so the goal is
always fewer calls, not cheaper ones.

## Performance

Measured on a 1,267-track library: roughly **2.6% of one core when paused,
4.5% when playing, 28MB resident**. State is polled once a second while playing
and every three seconds when nothing is moving; identical frames are not
repainted.

Music.app does not notify anyone when the library changes, so a song you add
while lazymusic is open cannot appear on its own. Cached track lists expire
after 60 seconds, and `R` (or `:reload`) re-reads everything immediately.

## Scope

lazymusic searches **your library** — everything you've added in Music,
including Apple Music tracks you've saved. It does not search the full Apple
Music catalogue; that needs a MusicKit developer token, which is a different
project. If `mus <query>` finds nothing, add the song in Music first.

Two limits are Music.app's rather than choices: there is **no queue** to read
(`u` infers one from the playlist), and **no streaming lyrics** to read (`y`
falls back to LRCLIB). Both are explained under *How it works*.

## Development

```sh
python3 -m unittest discover tests   # 161 tests, no Music.app needed
python3 -m lazymusic status          # run without installing
```
