# lazymusic

A lazygit-style terminal UI for Apple Music. Browse playlists, search your
library and control playback without ever bringing Music.app to the front.

No dependencies — just Python 3 and the Music.app that ships with macOS.

```
╭─ 1 Player ────────────────────────────╮╭─ 4 Music ─────────────────────────────────────────────────────────╮
│  ▶  Kun Faya Kun                      ││     1 Maahi Ve                              A.R. Rahman           │
│     A.R. Rahman, Javed Ali            ││     2 Jashn-E-Bahaaraa                      A.R. Rahman & Javed A…│
│   3:59 ━━━━━━━━━━━━●────────── 7:50   ││     3 Luka Chuppi                           A.R. Rahman & Lata Ma…│
│   shuffle  repeat one  ♥              ││ ▶   4 Nadaan Parinde                        A.R. Rahman & Mohit C…│
│   vol ▪▪▪▪▪▪▪▪·· 80%                  ││ ▸   5 Tere Bina                             A.R. Rahman, Chinmayi…│
╰───────────────────────────────────────╯│     6 Rang De Basanti                       A.R. Rahman, Daler Me…│
╭─ 2 Playlists ─────────────────────────╮│     7 Kun Faya Kun                          A.R. Rahman, Javed Al…│
│ ▸ Music                           1267││     8 Tum Tak                               A.R. Rahman, Javed Al…│
│   Favourite Songs                 1244││     9 The Winner Takes It All               ABBA                  │
│   Alone                             25││    10 Back In Black                         AC/DC                 │
╰──────────────────────────────── 1/22 ─╯│    11 Skyfall                               Adele                 │
╭─ 3 Search  tere ──────────────────────╮│    12 Faasle                                Aditya Rikhari        │
│ ▸ Tere Bina             A.R. Rahman,… ││    13 Sahiba                                Aditya Rikhari        │
│   Tere Liye             Atif Aslam &… ││    14 Samjho Na                             Aditya Rikhari        │
╰───────────────────────────────────────╯╰────────────────────────────────────────────────────────── 1/1267 ─╯
 space play/pause   j/k move   h/l panel   enter play   / filter   : command   ? help   q quit
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
| `<count>G` | go to that row | `ctrl-w` `1`–`4` | focus by number |
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

Three more things worth knowing if you edit this:

**Bulk property reads.** Asking Music for one property of one track costs a
round trip (~50ms), so looping over a 1,200-track playlist takes over a minute.
Asking for `name of every track of ...` returns the whole column in a single
Apple Event — the same playlist arrives in about 0.1s. The catch is the getter
must be applied to the `whose` clause itself; binding matches to a variable
first and asking for `name of res` fails with `-1700`.

**Widths before colour.** Panels are concatenated side by side, so every row
must be an exact number of columns — and once ANSI escapes are in a string you
can't measure it. Rows are therefore built as `(text, *styles)` segments and
coloured last, by `fmt.row()`. Combining marks count as zero columns and CJK as
two; getting that wrong tears the panel edge on non-Latin titles.

**Favourite, not loved.** macOS 15 renamed Love to Favorite and the old `loved`
property now raises `-10001`. `music.py` prefers `favorited` and falls back.

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

## Development

```sh
python3 -m unittest discover tests   # 54 tests, no Music.app needed
python3 -m lazymusic status          # run without installing
```
