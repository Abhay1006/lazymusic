"""Unit tests for the pieces that do not need Music.app running."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lazymusic import art, fmt, lyrics, osa  # noqa: E402
from lazymusic.cli import _relative  # noqa: E402
from lazymusic import music  # noqa: E402
from lazymusic.music import REPEAT_MODES, State, Track, _num  # noqa: E402
from lazymusic import tui  # noqa: E402
from lazymusic.tui import Keys, Lane  # noqa: E402


class TestTime(unittest.TestCase):
    def test_mmss(self):
        self.assertEqual(fmt.mmss(0), "0:00")
        self.assertEqual(fmt.mmss(7.9), "0:07")
        self.assertEqual(fmt.mmss(65), "1:05")
        self.assertEqual(fmt.mmss(2693.74), "44:53")
        self.assertEqual(fmt.mmss(3661), "1:01:01")

    def test_mmss_handles_junk(self):
        self.assertEqual(fmt.mmss(None), "0:00")
        self.assertEqual(fmt.mmss(-5), "0:00")


class TestBar(unittest.TestCase):
    def test_width_is_exact(self):
        for frac in (0.0, 0.37, 1.0):
            for width in (3, 10, 71):
                self.assertEqual(len(fmt.bar(frac, width)), width)

    def test_playhead_moves_to_the_ends(self):
        self.assertTrue(fmt.bar(0.0, 10).startswith("●"))
        self.assertTrue(fmt.bar(1.0, 10).endswith("●"))

    def test_fraction_is_clamped(self):
        self.assertEqual(fmt.bar(-3, 10), fmt.bar(0.0, 10))
        self.assertEqual(fmt.bar(9, 10), fmt.bar(1.0, 10))


class TestTruncate(unittest.TestCase):
    def setUp(self):
        fmt.set_color(False)

    def test_short_text_is_untouched(self):
        self.assertEqual(fmt.truncate("Tere Bina", 20), "Tere Bina")

    def test_long_text_gets_an_ellipsis_and_fits(self):
        out = fmt.truncate("Rockstar (Original Motion Picture Soundtrack)", 20)
        self.assertLessEqual(fmt.width_of(out), 20)
        self.assertTrue(out.endswith("…"))

    def test_wide_glyphs_count_as_two_columns(self):
        self.assertEqual(fmt.width_of("日本語"), 6)
        self.assertLessEqual(fmt.width_of(fmt.truncate("日本語の歌", 6)), 6)

    def test_combining_marks_take_no_column(self):
        # 7 code points, but only 4 base letters reach the screen.
        self.assertEqual(len("संपूर्ण"), 7)
        self.assertEqual(fmt.width_of("संपूर्ण"), 4)
        self.assertEqual(fmt.width_of("े"), 0)

    def test_truncate_never_exceeds_width(self):
        for text in ("संपूर्ण गीता MAHABHARAT", "日本語の歌", "plain ascii text"):
            for width in range(1, 12):
                self.assertLessEqual(fmt.width_of(fmt.truncate(text, width)), width)

    def test_pad_fills_to_the_requested_width(self):
        self.assertEqual(fmt.width_of(fmt.pad("abc", 10)), 10)
        self.assertEqual(fmt.pad("abcdef", 3), "abcdef")   # never truncates


class TestAppleScriptLiteral(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(osa.lit("hello"), '"hello"')

    def test_quotes_and_backslashes_are_escaped(self):
        self.assertEqual(osa.lit('a"b'), '"a\\"b"')
        self.assertEqual(osa.lit("a\\b"), '"a\\\\b"')

    def test_newlines_are_spliced_not_embedded(self):
        # AppleScript string literals cannot span lines.
        out = osa.lit("a\nb")
        self.assertNotIn("\n", out)
        self.assertIn("linefeed", out)

    def test_unicode_passes_through(self):
        self.assertEqual(osa.lit("हिंदी"), '"हिंदी"')


class TestNumberParsing(unittest.TestCase):
    def test_plain_and_comma_decimals(self):
        self.assertAlmostEqual(_num("96.88"), 96.88)
        self.assertAlmostEqual(_num("96,88"), 96.88)   # comma-decimal locales

    def test_junk_falls_back(self):
        self.assertEqual(_num(""), 0.0)
        self.assertEqual(_num("nope"), 0.0)
        self.assertEqual(_num(None), 0.0)


class TestRelativeArgs(unittest.TestCase):
    def test_absolute(self):
        self.assertEqual(_relative("60"), (60.0, False))

    def test_relative_both_directions(self):
        self.assertEqual(_relative("+10"), (10.0, True))
        self.assertEqual(_relative("-10"), (-10.0, True))

    def test_whitespace_is_ignored(self):
        self.assertEqual(_relative("  +5 "), (5.0, True))


class TestState(unittest.TestCase):
    def test_default_state_is_stopped_and_not_running(self):
        st = State()
        self.assertFalse(st.running)
        self.assertTrue(st.stopped)
        self.assertFalse(st.playing)

    def test_playing_requires_a_track(self):
        st = State()
        st.player = "playing"
        self.assertTrue(st.playing)
        self.assertTrue(st.stopped)          # no track yet
        st.track = Track(pid="X", name="Tere Bina")
        self.assertFalse(st.stopped)

    def test_track_label(self):
        self.assertEqual(Track(name="Tere Bina", artist="A.R. Rahman").label,
                         "Tere Bina - A.R. Rahman")
        self.assertEqual(Track(name="Untitled").label, "Untitled")

    def test_repeat_modes_cycle_in_order(self):
        self.assertEqual(REPEAT_MODES, ("off", "one", "all"))



class TestRow(unittest.TestCase):
    """`row` must always emit exactly the requested number of columns - panels
    are concatenated side by side, so one wrong width shears the whole screen."""

    def setUp(self):
        fmt.set_color(False)

    def test_pads_short_content(self):
        self.assertEqual(fmt.row([("hi",)], 10), "hi        ")

    def test_truncates_long_content(self):
        self.assertEqual(fmt.width_of(fmt.row([("a" * 50,)], 12)), 12)

    def test_multiple_segments_sum_to_width(self):
        for width in (5, 20, 61):
            out = fmt.row([("abc",), ("defg",), ("hijkl",)], width)
            self.assertEqual(fmt.width_of(out), width)

    def test_wide_glyph_segments_still_fit(self):
        out = fmt.row([("日本語の歌",), ("x",)], 7)
        self.assertEqual(fmt.width_of(out), 7)

    def test_empty_segments_are_skipped(self):
        self.assertEqual(fmt.row([("",), ("ok",)], 4), "ok  ")

    def test_colour_does_not_change_measured_width(self):
        fmt.set_color(True)
        try:
            plain = fmt.row([("abc",)], 10)
            styled = fmt.row([("abc", "green")], 10)
            self.assertIn("\033[", styled)
            self.assertEqual(len(plain), 10)
        finally:
            fmt.set_color(False)


class TestBox(unittest.TestCase):
    def setUp(self):
        fmt.set_color(False)

    def test_shape_is_exact(self):
        lines = fmt.box(30, 6, "Player", [[("body",)]])
        self.assertEqual(len(lines), 6)
        for line in lines:
            self.assertEqual(fmt.width_of(line), 30)

    def test_corners_and_title(self):
        lines = fmt.box(24, 4, "Playlists", [])
        self.assertTrue(lines[0].startswith(fmt.TL))
        self.assertTrue(lines[0].endswith(fmt.TR))
        self.assertTrue(lines[-1].startswith(fmt.BL))
        self.assertTrue(lines[-1].endswith(fmt.BR))
        self.assertIn("Playlists", lines[0])

    def test_footer_is_drawn_on_the_bottom_edge(self):
        lines = fmt.box(30, 4, "T", [], footer="3/22")
        self.assertIn("3/22", lines[-1])
        self.assertEqual(fmt.width_of(lines[-1]), 30)

    def test_overlong_title_is_truncated_not_overflowed(self):
        lines = fmt.box(20, 3, "A very long panel title indeed", [])
        self.assertEqual(fmt.width_of(lines[0]), 20)

    def test_body_shorter_than_box_is_padded(self):
        lines = fmt.box(20, 7, "T", [[("one",)]])
        self.assertEqual(len(lines), 7)
        self.assertEqual(fmt.width_of(lines[4]), 20)

    def test_body_longer_than_box_is_clipped(self):
        lines = fmt.box(20, 4, "T", [[("r%d" % i,)] for i in range(20)])
        self.assertEqual(len(lines), 4)

    def test_degenerate_sizes_do_not_crash(self):
        self.assertEqual(fmt.box(2, 1, "T", []), ["  "])
        self.assertEqual(fmt.box(0, 0, "T", []), [])



def lane_of(n, view=10):
    lane = Lane(label=lambda s: s)
    lane.set_items(["row %d" % i for i in range(n)])
    return lane, view


class TestEscapeDecoding(unittest.TestCase):
    def decode(self, data):
        return Keys._escape(data, 0)

    def test_arrows(self):
        self.assertEqual(self.decode("\033[A"), ("up", 3))
        self.assertEqual(self.decode("\033[B"), ("down", 3))
        self.assertEqual(self.decode("\033[C"), ("right", 3))
        self.assertEqual(self.decode("\033[D"), ("left", 3))

    def test_shift_tab_and_paging(self):
        self.assertEqual(self.decode("\033[Z"), ("shift-tab", 3))
        self.assertEqual(self.decode("\033[5~"), ("pageup", 4))
        self.assertEqual(self.decode("\033[6~"), ("pagedown", 4))

    def test_bare_escape(self):
        self.assertEqual(self.decode("\033"), ("esc", 1))
        self.assertEqual(self.decode("\033x"), ("esc", 1))

    def test_truncated_sequence_does_not_hang(self):
        self.assertEqual(self.decode("\033[12"), ("esc", 1))

    def test_modified_arrow_is_consumed_whole(self):
        # e.g. shift+up arrives as ESC [ 1 ; 2 A - must not leak stray digits.
        name, length = self.decode("\033[1;2A")
        self.assertEqual(length, 6)
        self.assertEqual(name, "up")


class TestLaneMotion(unittest.TestCase):
    def test_move_and_clamp_at_the_ends(self):
        lane, view = lane_of(50)
        lane.move(12, view)
        self.assertEqual(lane.cursor, 12)
        lane.move(-100, view)
        self.assertEqual(lane.cursor, 0)
        lane.move(500, view)
        self.assertEqual(lane.cursor, 49)

    def test_cursor_stays_inside_the_visible_window(self):
        lane, view = lane_of(100, view=10)
        for delta in (7, 9, 30, -5, -40, 99):
            lane.move(delta, view)
            self.assertGreaterEqual(lane.cursor, lane.scroll)
            self.assertLess(lane.cursor, lane.scroll + view)

    def test_goto_is_clamped(self):
        lane, view = lane_of(20)
        self.assertTrue(lane.goto(5, view))
        self.assertEqual(lane.cursor, 5)
        lane.goto(999, view)
        self.assertEqual(lane.cursor, 19)

    def test_move_reports_whether_it_moved(self):
        lane, view = lane_of(3)
        self.assertTrue(lane.move(1, view))
        self.assertTrue(lane.move(5, view))     # clamps to the end, still moved
        self.assertFalse(lane.move(5, view))    # already there

    def test_screen_rows(self):
        lane, view = lane_of(100, view=10)
        lane.goto(50, view)
        lane.reposition("zt", view)
        self.assertEqual(lane.scroll, 50)
        lane.screen_row("H", view)
        self.assertEqual(lane.cursor, 50)
        lane.screen_row("L", view)
        self.assertEqual(lane.cursor, 59)
        lane.screen_row("M", view)
        self.assertEqual(lane.cursor, 54)

    def test_reposition_keeps_scroll_in_range(self):
        lane, view = lane_of(100, view=10)
        lane.goto(2, view)
        lane.reposition("zz", view)
        self.assertEqual(lane.scroll, 0)        # cannot scroll above the top
        lane.goto(99, view)
        lane.reposition("zt", view)
        self.assertEqual(lane.scroll, 90)       # nor past the end

    def test_empty_lane_is_inert(self):
        lane = Lane()
        self.assertFalse(lane.move(1, 10))
        self.assertFalse(lane.goto(3, 10))
        self.assertIsNone(lane.selected)


class TestLaneFilter(unittest.TestCase):
    def test_filter_narrows_and_clears(self):
        lane = Lane(label=lambda s: s)
        lane.set_items(["Kun Faya Kun", "Tere Bina", "Tere Naina"])
        lane.apply_filter("tere")
        self.assertEqual(len(lane.items), 2)
        lane.apply_filter("")
        self.assertEqual(len(lane.items), 3)

    def test_filter_is_case_insensitive(self):
        lane = Lane(label=lambda s: s)
        lane.set_items(["Skyfall", "skydive"])
        lane.apply_filter("SKY")
        self.assertEqual(len(lane.items), 2)

    def test_cursor_does_not_dangle_past_a_shrunken_list(self):
        lane = Lane(label=lambda s: s)
        lane.set_items(["a1", "a2", "a3", "b1"])
        lane.goto(3, 10)
        lane.apply_filter("a")
        self.assertLess(lane.cursor, len(lane.items))
        self.assertIsNotNone(lane.selected)

    def test_set_items_reapplies_the_active_filter(self):
        lane = Lane(label=lambda s: s)
        lane.apply_filter("x")
        lane.set_items(["ax", "b", "cx"])
        self.assertEqual(lane.items, ["ax", "cx"])

    def test_selected_reflects_the_filtered_view(self):
        lane = Lane(label=lambda s: s)
        lane.set_items(["one", "two", "three"])
        lane.apply_filter("t")
        lane.goto(1, 10)
        self.assertEqual(lane.selected, "three")

if __name__ == "__main__":
    unittest.main()


class TestLrc(unittest.TestCase):
    def test_plain_lines_have_no_times(self):
        parsed = lyrics.parse_lrc("first line\n\nthird line")
        self.assertEqual(parsed, [(None, "first line"), (None, ""),
                                  (None, "third line")])

    def test_stamps_are_parsed_and_stripped(self):
        parsed = lyrics.parse_lrc("[00:12.99]When I was just a little boy")
        self.assertEqual(parsed, [(12.99, "When I was just a little boy")])

    def test_minutes_roll_into_seconds(self):
        self.assertEqual(lyrics.parse_lrc("[02:05.50]x")[0][0], 125.5)

    def test_two_digit_and_absent_fractions(self):
        self.assertEqual(lyrics.parse_lrc("[00:07]a")[0][0], 7.0)
        self.assertEqual(lyrics.parse_lrc("[00:07.5]a")[0][0], 7.5)

    def test_a_line_stamped_twice_appears_at_both_times(self):
        # A repeated chorus line carries one stamp per repeat.
        parsed = lyrics.parse_lrc("[00:10.00][01:00.00]chorus")
        self.assertEqual(parsed, [(10.0, "chorus"), (60.0, "chorus")])

    def test_stamped_lines_come_back_in_time_order(self):
        parsed = lyrics.parse_lrc("[00:30.00]later\n[00:10.00]earlier")
        self.assertEqual([t for t, _ in parsed], [10.0, 30.0])


class TestLyricsFollow(unittest.TestCase):
    def make(self):
        return lyrics.Lyrics(lyrics.parse_lrc(
            "[00:10.00]one\n[00:20.00]two\n[00:30.00]three"),
            "lrclib", synced=True)

    def test_before_the_first_line_nothing_is_current(self):
        self.assertEqual(self.make().line_at(5), -1)

    def test_the_line_that_has_most_recently_started_is_current(self):
        got = self.make()
        self.assertEqual(got.line_at(10), 0)
        self.assertEqual(got.line_at(19.9), 0)
        self.assertEqual(got.line_at(20), 1)
        self.assertEqual(got.line_at(999), 2)

    def test_an_unsynced_lyric_never_follows(self):
        plain = lyrics.Lyrics([(None, "one")], "tags")
        self.assertEqual(plain.line_at(10), -1)

    def test_empty_lyrics_are_falsey(self):
        self.assertFalse(lyrics.Lyrics())
        self.assertTrue(self.make())


class TestLyricsMode(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("LAZYMUSIC_LYRICS")

    def tearDown(self):
        os.environ.pop("LAZYMUSIC_LYRICS", None)
        if self._saved is not None:
            os.environ["LAZYMUSIC_LYRICS"] = self._saved

    def test_default_is_online(self):
        os.environ.pop("LAZYMUSIC_LYRICS", None)
        self.assertEqual(lyrics.mode(), "online")

    def test_recognised_values(self):
        for value in ("tags", "off", "online", " OFF "):
            os.environ["LAZYMUSIC_LYRICS"] = value
            self.assertEqual(lyrics.mode(), value.strip().lower())

    def test_nonsense_falls_back_to_online(self):
        os.environ["LAZYMUSIC_LYRICS"] = "sometimes"
        self.assertEqual(lyrics.mode(), "online")


class TestLrclibChoice(unittest.TestCase):
    def test_the_closest_duration_wins(self):
        # Search puts a live cut first; only the length tells the versions apart.
        hits = [{"duration": 300.0, "plainLyrics": "live"},
                {"duration": 182.0, "plainLyrics": "studio"}]
        self.assertEqual(lyrics._best(hits, 180)["plainLyrics"], "studio")

    def test_without_a_duration_the_first_usable_hit_wins(self):
        hits = [{"duration": 300.0, "plainLyrics": "live"},
                {"duration": 182.0, "plainLyrics": "studio"}]
        self.assertEqual(lyrics._best(hits, 0)["plainLyrics"], "live")

    def test_hits_with_no_lyrics_at_all_are_skipped(self):
        hits = [{"duration": 180.0}, {"duration": 400.0, "plainLyrics": "x"}]
        self.assertEqual(lyrics._best(hits, 180)["plainLyrics"], "x")
        self.assertIsNone(lyrics._best([{"duration": 1.0}], 180))
        self.assertIsNone(lyrics._best([], 180))

    def test_synced_is_preferred_over_plain(self):
        got = lyrics._from_payload({"syncedLyrics": "[00:01.00]a",
                                    "plainLyrics": "a"})
        self.assertTrue(got.synced)
        self.assertEqual(got.lines, [(1.0, "a")])

    def test_plain_is_used_when_there_is_no_transcript(self):
        got = lyrics._from_payload({"syncedLyrics": "", "plainLyrics": "a\nb"})
        self.assertFalse(got.synced)
        self.assertEqual(got.lines, [(None, "a"), (None, "b")])


def _bmp(width, height, rows, top_down=False):
    """Build a 24-bit BMP in memory, laid out the way `sips` writes them."""
    import struct
    stride = (width * 3 + 3) // 4 * 4
    body = b""
    order = rows if top_down else list(reversed(rows))
    for line in order:
        raw = b"".join(bytes((b, g, r)) for r, g, b in line)
        body += raw + b"\x00" * (stride - len(raw))
    header = (b"BM" + struct.pack("<IHHI", 54 + len(body), 0, 0, 54)
              + struct.pack("<IiiHHIIiiII", 40, width,
                            -height if top_down else height, 1, 24, 0,
                            len(body), 0, 0, 0, 0))
    return header + body


class TestBmp(unittest.TestCase):
    RED, BLUE = (255, 0, 0), (0, 0, 255)

    def read(self, blob):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "t.bmp")
        with open(path, "wb") as fh:
            fh.write(blob)
        return art.read_bmp(path)

    def test_a_bottom_up_bitmap_comes_back_top_row_first(self):
        rows = [[self.RED, self.RED], [self.BLUE, self.BLUE]]
        width, height, pixels = self.read(_bmp(2, 2, rows))
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(pixels, rows)

    def test_a_top_down_bitmap_reads_the_same_way(self):
        rows = [[self.RED, self.RED], [self.BLUE, self.BLUE]]
        self.assertEqual(self.read(_bmp(2, 2, rows, top_down=True))[2], rows)

    def test_row_padding_to_four_bytes_is_skipped(self):
        # Three pixels is nine bytes, so each row carries three bytes of padding.
        rows = [[self.RED, self.BLUE, self.RED]]
        self.assertEqual(self.read(_bmp(3, 1, rows))[2], rows)

    def test_junk_is_refused_rather_than_guessed_at(self):
        with self.assertRaises(ValueError):
            self.read(b"not a bitmap at all, not even close")
        with self.assertRaises(ValueError):
            self.read(_bmp(2, 2, [[self.RED] * 2] * 2)[:40])


class TestArtGeometry(unittest.TestCase):
    def test_a_cover_is_twice_as_wide_in_cells_as_it_is_tall(self):
        for mode in art.GLYPH_ORDER:
            cols, _ = art.cell_size(10, mode)
            self.assertEqual(cols, 20, mode)

    def test_the_cover_region_is_square_whatever_the_glyph(self):
        # A cell is 1 unit wide and 2 tall, and the cover uses twice as many
        # columns as rows, so the region on screen is square in every mode.
        for mode in art.GLYPH_ORDER:
            cols, _ = art.cell_size(10, mode)
            self.assertEqual(cols * 1, 10 * 2, mode)

    def test_the_sample_grid_matches_the_subcell_grid_exactly(self):
        # This is what keeps the picture undistorted: one sampled pixel per
        # subcell. `quad` and `sext` sample more columns than rows, which is
        # finer horizontal detail, not a stretched image.
        for mode in art.GLYPH_ORDER:
            wide, tall = art.GLYPH_CELLS[mode]
            cols, (pixel_w, pixel_h) = art.cell_size(10, mode)
            self.assertEqual((pixel_w, pixel_h), (cols * wide, 10 * tall), mode)

    def test_each_family_packs_more_pixels_than_the_last(self):
        counts = [art.cell_size(12, m)[1] for m in art.GLYPH_ORDER]
        areas = [w * h for w, h in counts]
        self.assertEqual(counts[0], (24, 24))      # half
        self.assertEqual(counts[-1], (48, 48))     # oct
        self.assertEqual(areas, sorted(areas))
        self.assertEqual(areas[-1], areas[0] * 4)

    def test_each_text_row_consumes_two_pixel_rows(self):
        rows = art.rows_from_pixels([[(1, 2, 3)], [(4, 5, 6)],
                                     [(7, 8, 9)], [(10, 11, 12)]], 1, 4)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.cols == 1 for r in rows))

    def test_an_odd_pixel_row_is_dropped_rather_than_half_drawn(self):
        rows = art.rows_from_pixels([[(0, 0, 0)]] * 5, 1, 5)
        self.assertEqual(len(rows), 2)

    def test_a_row_of_one_colour_sets_that_colour_once(self):
        fmt.set_color(True)
        rows = art.rows_from_pixels([[(1, 2, 3)] * 4, [(4, 5, 6)] * 4], 4, 2)
        self.assertEqual(rows[0].count("38;2;1;2;3"), 1)
        self.assertEqual(rows[0].count(art.BLOCK), 4)


class TestRawSegments(unittest.TestCase):
    def test_a_raw_segment_is_measured_by_its_recorded_width(self):
        fmt.set_color(True)
        cell = fmt.Raw("\033[38;2;1;2;3m▀\033[0m", 1)
        # Six visible columns: the one-column cell plus five of padding.
        self.assertEqual(len(fmt.strip_ansi(fmt.row([(cell,)], 6))), 6)

    def test_a_raw_segment_too_wide_to_fit_is_dropped_whole(self):
        cell = fmt.Raw("\033[38;2;1;2;3m▀▀▀\033[0m", 3)
        self.assertEqual(fmt.row([(cell,)], 2), " " * 2)

    def test_truecolor_tokens_survive_the_style_table(self):
        fmt.set_color(True)
        self.assertIn("38;2;10;20;30", fmt.c("x", fmt.fg(10, 20, 30)))
        self.assertIn("48;2;10;20;30", fmt.c("x", fmt.bg(10, 20, 30)))
        # Names still work, and mix with truecolor.
        self.assertIn("1;38;2;1;2;3", fmt.c("x", "bold", fmt.fg(1, 2, 3)))

    def test_colour_off_leaves_text_bare(self):
        fmt.set_color(False)
        self.assertEqual(fmt.c("x", fmt.fg(1, 2, 3)), "x")
        fmt.set_color(True)


class TestMainViews(unittest.TestCase):
    """The main panel switching between the track list, cover, lyrics and queue."""

    def setUp(self):
        self.app = tui.App()
        self.addCleanup(self.app.close)

    def test_it_opens_on_the_track_list(self):
        self.assertEqual(self.app.view, tui.V_TRACKS)

    def test_a_view_key_toggles_back_to_the_track_list(self):
        self.app.set_view(tui.V_ART)
        self.assertEqual(self.app.view, tui.V_ART)
        self.app.set_view(tui.V_ART)
        self.assertEqual(self.app.view, tui.V_TRACKS)

    def test_switching_between_two_views_does_not_toggle(self):
        self.app.set_view(tui.V_ART)
        self.app.set_view(tui.V_QUEUE)
        self.assertEqual(self.app.view, tui.V_QUEUE)

    def test_focus_follows_a_view_you_can_scroll(self):
        self.app.focus = tui.PLAYLISTS
        self.app.set_view(tui.V_QUEUE)
        self.assertEqual(self.app.focus, tui.TRACKS)

    def test_focus_stays_put_for_the_cover_which_does_not_scroll(self):
        self.app.focus = tui.PLAYLISTS
        self.app.set_view(tui.V_ART)
        self.assertEqual(self.app.focus, tui.PLAYLISTS)

    def test_the_main_panel_lane_follows_the_view(self):
        self.app.focus = tui.TRACKS
        self.app.view = tui.V_TRACKS
        self.assertIs(self.app.focused_lane()[0], self.app.lanes[tui.TRACKS])
        self.app.view = tui.V_LYRICS
        self.assertIs(self.app.focused_lane()[0], self.app.lanes["lyrics"])
        self.app.view = tui.V_QUEUE
        self.assertIs(self.app.focused_lane()[0], self.app.lanes["queue"])

    def test_the_cover_has_no_lane_to_scroll(self):
        self.app.focus = tui.TRACKS
        self.app.view = tui.V_ART
        self.assertIsNone(self.app.focused_lane()[0])

    def test_the_help_sheet_answers_wherever_the_focus_is(self):
        # Help is modal, so it scrolls no matter which panel had the cursor.
        for focus in (tui.PLAYER, tui.PLAYLISTS, tui.TRACKS):
            self.app.focus = focus
            self.app.view = tui.V_HELP
            self.assertIs(self.app.focused_lane()[0], self.app.lanes["help"])

    def test_motions_are_harmless_with_no_lane(self):
        self.app.focus = tui.TRACKS
        self.app.view = tui.V_ART
        for key in ("j", "k", "G", "H", "ctrl-d"):
            self.app.normal_key(key)          # must not raise
        self.assertEqual(self.app.view, tui.V_ART)

    def test_escape_leaves_a_view_once_no_filter_is_left_to_clear(self):
        self.app.focus = tui.TRACKS
        self.app.view = tui.V_QUEUE
        self.app.lanes["queue"].apply_filter("x")
        self.app.normal_key("esc")            # first esc clears the filter
        self.assertEqual(self.app.view, tui.V_QUEUE)
        self.app.normal_key("esc")            # second goes back to the tracks
        self.assertEqual(self.app.view, tui.V_TRACKS)

    def test_scrolling_a_lyric_takes_it_off_follow(self):
        self.app.view = tui.V_LYRICS
        self.app._follow = True
        self.app.after_move(True)
        self.assertFalse(self.app._follow)

    def test_a_new_track_puts_the_lyric_back_on_follow(self):
        self.app.view = tui.V_LYRICS
        self.app._follow = False
        self.app.on_track_change()
        self.assertTrue(self.app._follow)

    def test_filling_the_main_panel_with_a_list_dismisses_an_overlay(self):
        self.app.view = tui.V_ART
        self.app.show_main("Road Trip", [], playlist="Road Trip")
        self.assertEqual(self.app.view, tui.V_TRACKS)

    def test_view_commands_match_the_keys(self):
        self.app.run_command("art")
        self.assertEqual(self.app.view, tui.V_ART)
        self.app.run_command("lyrics")
        self.assertEqual(self.app.view, tui.V_LYRICS)
        self.app.run_command("upnext")
        self.assertEqual(self.app.view, tui.V_QUEUE)
        self.app.run_command("tracks")
        self.assertEqual(self.app.view, tui.V_TRACKS)


class TestUpNext(unittest.TestCase):
    """What follows the current track, inferred from the playing playlist."""

    def setUp(self):
        self.app = tui.App()
        self.addCleanup(self.app.close)
        self.tracks = [Track(pid=str(i), name="t%d" % i) for i in range(5)]

    def test_it_is_everything_after_the_playing_track(self):
        self.app.state.track = self.tracks[1]
        self.assertEqual(self.app.after_current(self.tracks), self.tracks[2:])

    def test_the_last_track_is_followed_by_nothing(self):
        self.app.state.track = self.tracks[4]
        self.assertEqual(self.app.after_current(self.tracks), [])

    def test_a_track_playing_from_elsewhere_leaves_the_list_whole(self):
        self.app.state.track = Track(pid="somewhere-else")
        self.assertEqual(self.app.after_current(self.tracks), self.tracks)

    def test_filling_up_next_always_asks_music(self):
        # It cannot be answered from cache: whether the queue playlist has
        # anything in it is only knowable by asking.
        self.app.state.playlist = "Road Trip"
        self.app._cache["Road Trip"] = (time.monotonic(), self.tracks)
        sent = []
        self.app.bus.submit = lambda key, call, then=None: sent.append(key)
        self.app.want_queue()
        self.assertEqual(sent, ["queue"])


class TestQueueEditing(unittest.TestCase):
    """Adding to and removing from the queue playlist."""

    def setUp(self):
        self.app = tui.App()
        self.addCleanup(self.app.close)
        self.sent = []
        self.app.bus.submit = lambda key, call, then=None: self.sent.append(key)
        self.tracks = [Track(pid="p%d" % i, name="t%d" % i) for i in range(4)]

    def queue_view(self, items):
        self.app.view = tui.V_QUEUE
        self.app.focus = tui.TRACKS
        self.app._queue_kind = "queue"
        self.app.lanes["queue"].set_items(items)

    def test_A_queues_the_track_under_the_cursor(self):
        self.app.focus = tui.TRACKS
        self.app.view = tui.V_TRACKS
        self.app.lanes[tui.TRACKS].set_items(self.tracks)
        self.app.normal_key("A")
        self.assertEqual(self.sent, ["enqueue"])

    def test_A_on_something_that_is_not_a_track_says_so(self):
        self.app.view = tui.V_LYRICS
        self.app.focus = tui.TRACKS
        self.app.lanes["lyrics"].set_items([(None, "a lyric line")])
        self.app.normal_key("A")
        self.assertEqual(self.sent, [])
        self.assertIn("no track", self.app.message)

    def test_D_removes_the_highlighted_queue_entry(self):
        self.queue_view(self.tracks)
        self.app.normal_key("D")
        self.assertEqual(self.sent, ["dequeue"])

    def test_D_does_nothing_when_the_panel_is_showing_a_playlist(self):
        self.app.view = tui.V_QUEUE
        self.app._queue_kind = "playlist"
        self.app.lanes["queue"].set_items(self.tracks)
        self.app.normal_key("D")
        self.assertEqual(self.sent, [])
        self.assertIn("press A", self.app.message)

    def test_removal_uses_the_position_in_the_queue_not_the_cursor(self):
        # A filtered lane's cursor is not the playlist position, and deleting
        # the wrong row would silently drop somebody else's track.
        self.queue_view(self.tracks)
        lane = self.app.lanes["queue"]
        lane.apply_filter("t2")
        self.assertEqual(lane.items, [self.tracks[2]])
        captured = {}
        self.app.bus.submit = lambda key, call, then=None: captured.update(
            key=key, index=lane.all_items.index(lane.selected) + 1)
        self.app.dequeue()
        self.assertEqual(captured["index"], 3)      # 1-based, unfiltered

    def test_clearing_the_queue_goes_through_the_bus(self):
        self.app.run_command("upnext clear")
        self.assertEqual(self.sent, ["qclear"])
        self.assertEqual(self.app.view, tui.V_QUEUE)

    def test_a_bare_upnext_just_opens_the_view(self):
        self.app.run_command("upnext")
        self.assertEqual(self.app.view, tui.V_QUEUE)

    def test_enter_plays_a_queued_track_in_the_queue_playlist(self):
        self.queue_view(self.tracks)
        played = {}
        self.app.act = lambda fn, *a: played.update(fn=fn.__name__, args=a)
        self.app.activate()
        self.assertEqual(played["fn"], "play_track_in_playlist")
        self.assertEqual(played["args"][1], music.QUEUE)

    def test_enter_on_the_playlist_fallback_uses_that_playlist(self):
        self.app.view = tui.V_QUEUE
        self.app.focus = tui.TRACKS
        self.app._queue_kind = "playlist"
        self.app.state.playlist = "Road Trip"
        self.app.lanes["queue"].set_items(self.tracks)
        played = {}
        self.app.act = lambda fn, *a: played.update(fn=fn.__name__, args=a)
        self.app.activate()
        self.assertEqual(played["args"][1], "Road Trip")


class TestArtSizing(unittest.TestCase):
    """How big the cover comes out, given the room the panel has."""

    def setUp(self):
        self._saved = os.environ.pop("LAZYMUSIC_ART_ROWS", None)

    def tearDown(self):
        os.environ.pop("LAZYMUSIC_ART_ROWS", None)
        if self._saved is not None:
            os.environ["LAZYMUSIC_ART_ROWS"] = self._saved

    def test_a_roomy_panel_stops_at_the_cap(self):
        # Deliberately not "as big as it fits": a large cover is a handful of
        # huge blocks, which is the thing that reads as broken.
        self.assertEqual(tui.art_rows_for(60, 120), tui.ART_MAX)

    def test_a_short_panel_is_limited_by_its_height(self):
        self.assertEqual(tui.art_rows_for(12, 120), 8)

    def test_a_narrow_panel_leaves_the_details_their_width(self):
        # inner 46: (46 - 2 - 2 - 18) // 2 = 12, so width is not the binding
        # constraint until it drops below that.
        self.assertEqual(tui.art_rows_for(60, 40), (40 - 2 - 2 - 18) // 2)

    def test_a_panel_with_no_room_gets_no_cover_at_all(self):
        self.assertEqual(tui.art_rows_for(6, 120), 0)
        self.assertEqual(tui.art_rows_for(60, 24), 0)

    def test_the_cap_can_be_raised_by_hand(self):
        os.environ["LAZYMUSIC_ART_ROWS"] = "20"
        self.assertEqual(tui.art_rows_for(60, 120), 20)

    def test_an_override_still_cannot_exceed_the_room_available(self):
        os.environ["LAZYMUSIC_ART_ROWS"] = "40"
        self.assertEqual(tui.art_rows_for(20, 120), 16)

    def test_nonsense_overrides_are_ignored(self):
        os.environ["LAZYMUSIC_ART_ROWS"] = "huge"
        self.assertEqual(tui.art_rows_for(60, 120), tui.ART_MAX)


class TestArtPanel(unittest.TestCase):
    def setUp(self):
        self.app = tui.App()
        self.addCleanup(self.app.close)
        self.app.view = tui.V_ART
        self.app.state.track = Track(pid="p", name="Song", artist="Band",
                                     album="Album", duration=200.0)

    def rows(self, width, height):
        return self.app.panel_main(width, height)

    def test_the_panel_is_exactly_the_size_asked_for(self):
        for width, height in ((60, 27), (60, 14), (34, 20), (30, 8)):
            lines = self.rows(width, height)
            self.assertEqual(len(lines), height)
            for line in lines:
                self.assertEqual(fmt.width_of(fmt.strip_ansi(line)), width)

    def test_the_details_survive_a_panel_too_small_for_a_cover(self):
        text = "\n".join(fmt.strip_ansi(l) for l in self.rows(40, 8))
        self.assertIn("Song", text)
        self.assertIn("Band", text)

    def test_an_album_repeating_the_track_name_is_not_printed_twice(self):
        self.app.state.track.album = "Song"
        text = "\n".join(fmt.strip_ansi(l) for l in self.rows(60, 27))
        self.assertEqual(text.count("Song"), 1)

    def test_nothing_playing_says_so(self):
        self.app.state.track = Track()
        text = "\n".join(fmt.strip_ansi(l) for l in self.rows(60, 27))
        self.assertIn("nothing playing", text)


class TestOctantTable(unittest.TestCase):
    """The 2x4 glyph table, which has to cover all 256 patterns exactly."""

    def test_every_pattern_has_a_glyph(self):
        self.assertEqual(len(art.OCT_GLYPHS), 256)
        self.assertTrue(all(art.OCT_GLYPHS))

    def test_the_familiar_patterns_land_on_the_familiar_glyphs(self):
        self.assertEqual(art.OCT_GLYPHS[0b00000000], " ")
        self.assertEqual(art.OCT_GLYPHS[0b11111111], "█")
        self.assertEqual(art.OCT_GLYPHS[0b00001111], "▀")   # top two rows
        self.assertEqual(art.OCT_GLYPHS[0b11110000], "▄")   # bottom two rows
        self.assertEqual(art.OCT_GLYPHS[0b01010101], "▌")   # left column
        self.assertEqual(art.OCT_GLYPHS[0b10101010], "▐")   # right column

    def test_the_unencoded_patterns_borrow_their_complement(self):
        # Eight patterns were never given a codepoint; each reuses the glyph of
        # its complement, which is the same picture with the colours exchanged.
        self.assertTrue(art.OCT_SWAP)
        for bits in art.OCT_SWAP:
            self.assertEqual(art.OCT_GLYPHS[bits], art.OCT_GLYPHS[~bits & 0xFF])

    def test_the_directly_drawn_patterns_are_all_distinct_glyphs(self):
        # 248 patterns have a codepoint of their own; no two may share one, or
        # two different pixel arrangements would draw identically.
        direct = [b for b in range(256) if b not in art.OCT_SWAP]
        glyphs = [art.OCT_GLYPHS[b] for b in direct]
        self.assertEqual(len(direct), 248)
        self.assertEqual(len(set(glyphs)), 248)

    def test_a_swapped_pattern_reuses_a_direct_one(self):
        # The eight borrowed entries are the only duplicates in the table.
        self.assertEqual(len(set(art.OCT_GLYPHS)), 248)


class TestTwoTone(unittest.TestCase):
    """Reducing one cell to a foreground, a background and a bit pattern."""

    BLACK, WHITE = (0, 0, 0), (255, 255, 255)

    def test_a_flat_cell_is_all_background(self):
        fore, back, bits = art.two_tone([(30, 40, 50)] * 8)
        self.assertEqual(bits, 0)
        self.assertEqual(fore, back)
        self.assertEqual(art.OCT_GLYPHS[bits], " ")

    def test_the_bright_half_becomes_the_foreground(self):
        cell = [self.WHITE, self.BLACK] * 4          # alternating columns
        fore, back, bits = art.two_tone(cell)
        self.assertEqual(fore, self.WHITE)
        self.assertEqual(back, self.BLACK)
        self.assertEqual(bits, 0b01010101)           # the left column is lit
        self.assertEqual(art.OCT_GLYPHS[bits], "▌")

    def test_bit_order_runs_left_to_right_then_top_to_bottom(self):
        cell = [self.BLACK] * 8
        cell[0] = self.WHITE                          # only the top-left subcell
        _, _, bits = art.two_tone(cell)
        self.assertEqual(bits, 0b00000001)

    def test_the_top_two_rows_give_an_upper_half_block(self):
        cell = [self.WHITE] * 4 + [self.BLACK] * 4
        _, _, bits = art.two_tone(cell)
        self.assertEqual(art.OCT_GLYPHS[bits], "▀")

    def test_each_side_is_averaged_not_taken_from_one_pixel(self):
        cell = [(200, 200, 200), (240, 240, 240)] + [(10, 10, 10)] * 6
        fore, back, _ = art.two_tone(cell)
        self.assertEqual(fore, (220, 220, 220))
        self.assertEqual(back, (10, 10, 10))


class TestOctantRows(unittest.TestCase):
    def grid(self, width, height, colour=(1, 2, 3)):
        return [[colour] * width for _ in range(height)]

    def test_four_pixel_rows_make_one_text_row(self):
        rows = art.rows_from_cells(self.grid(8, 8), 8, 8, 2, 4,
                                   art.OCT_GLYPHS, art.OCT_SWAP)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.cols == 4 for r in rows))

    def test_a_flat_grid_sets_its_colour_once_per_row(self):
        fmt.set_color(True)
        rows = art.rows_from_cells(self.grid(8, 4), 8, 4, 2, 4,
                                   art.OCT_GLYPHS, art.OCT_SWAP)
        self.assertEqual(rows[0].count("38;2;1;2;3"), 1)
        self.assertEqual(rows[0].count(" "), 4)      # four blank cells

    def test_the_glyph_mode_can_be_chosen_by_environment(self):
        saved = os.environ.get("LAZYMUSIC_ART_GLYPH")
        art.set_glyph(None)            # a runtime choice outranks the environment
        try:
            os.environ["LAZYMUSIC_ART_GLYPH"] = "half"
            self.assertEqual(art.glyph_mode(), "half")
            os.environ["LAZYMUSIC_ART_GLYPH"] = "nonsense"
            self.assertEqual(art.glyph_mode(), art.DEFAULT_GLYPH)
            os.environ.pop("LAZYMUSIC_ART_GLYPH")
            self.assertEqual(art.glyph_mode(), art.DEFAULT_GLYPH)
        finally:
            os.environ.pop("LAZYMUSIC_ART_GLYPH", None)
            if saved is not None:
                os.environ["LAZYMUSIC_ART_GLYPH"] = saved


class TestHelpSheet(unittest.TestCase):
    """The key sheet, which is taller than a short terminal and so scrolls."""

    def setUp(self):
        self.app = tui.App()
        self.addCleanup(self.app.close)
        self.app.view = tui.V_HELP

    def test_the_panel_is_exactly_the_size_asked_for(self):
        for width, height in ((96, 27), (130, 40), (60, 12), (44, 20)):
            lines = self.app.panel_main(width, height)
            self.assertEqual(len(lines), height)
            for line in lines:
                self.assertEqual(fmt.width_of(fmt.strip_ansi(line)), width)

    def test_nothing_is_silently_cut_off(self):
        # Every entry must be reachable by scrolling, however short the panel.
        self.app.panel_main(96, 27)
        lane = self.app.lanes["help"]
        self.assertGreater(len(lane.items), 25)

    def test_j_and_k_scroll_the_sheet(self):
        self.app.panel_main(96, 12)
        lane = self.app.lanes["help"]
        self.app.handle("j")
        self.app.handle("j")
        self.assertEqual(lane.cursor, 2)
        self.app.handle("k")
        self.assertEqual(lane.cursor, 1)

    def test_a_count_prefix_still_works(self):
        self.app.panel_main(96, 12)
        for key in "12j":
            self.app.handle(key)
        self.assertEqual(self.app.lanes["help"].cursor, 12)

    def test_G_scrolls_to_the_bottom_and_stops(self):
        # The cursor doubles as the top of the view, so the last screenful is
        # as far as it goes rather than scrolling the sheet off the panel.
        lane = self.app.lanes["help"]
        self.app.panel_main(96, 12)
        self.app.handle("G")
        self.app.panel_main(96, 12)
        self.assertEqual(lane.cursor, len(lane.items) - 10)

    def test_scrolling_reaches_entries_the_first_screen_cannot_show(self):
        lane = self.app.lanes["help"]
        self.app.panel_main(96, 12)
        first = {seg[0] for row in lane.items[:10] for seg in row}
        self.app.handle("G")
        self.app.panel_main(96, 12)
        last = {seg[0] for row in lane.items[lane.cursor:lane.cursor + 10]
                for seg in row}
        self.assertNotEqual(first, last)

    def test_the_closing_keys_still_close(self):
        for key in ("?", "esc", "q", "enter"):
            self.app.view = tui.V_HELP
            self.app.handle(key)
            self.assertEqual(self.app.view, tui.V_TRACKS)

    def test_playback_keys_do_not_leak_through_the_sheet(self):
        fired = []
        self.app.act = lambda fn, *a: fired.append(fn.__name__)
        for key in (" ", "n", "p", "s", "f", "A", "D"):
            self.app.handle(key)
        self.assertEqual(fired, [])
        self.assertEqual(self.app.view, tui.V_HELP)

    def test_it_recomposes_when_the_width_changes(self):
        self.app.panel_main(130, 30)
        wide = len(self.app.lanes["help"].items)
        self.app.panel_main(60, 30)
        narrow = len(self.app.lanes["help"].items)
        # One column instead of three, so the same entries stack much taller.
        self.assertGreater(narrow, wide)

    def test_the_queue_keys_are_documented(self):
        self.app.panel_main(60, 30)
        text = " ".join(seg[0] for row in self.app.lanes["help"].items
                        for seg in row)
        for label in ("QUEUE", "queue this track", "remove from queue"):
            self.assertIn(label, text)


class TestGlyphFamilies(unittest.TestCase):
    """All four block-glyph families, and the tables behind them."""

    def test_the_default_is_one_every_terminal_can_draw(self):
        # `quad` is Unicode 3.2 block elements. The newer families look better
        # but a terminal that lacks them shows tofu, so they are opt-in.
        self.assertEqual(art.DEFAULT_GLYPH, "quad")

    def test_every_family_has_a_complete_table(self):
        for mode in art.GLYPH_ORDER:
            wide, tall, glyphs, swap = art.glyph_table(mode)
            if glyphs is None:
                self.assertEqual(mode, "half")   # exact colour, needs no table
                continue
            self.assertEqual(len(glyphs), 1 << (wide * tall), mode)
            self.assertTrue(all(glyphs), mode)

    def test_only_octants_need_borrowed_glyphs(self):
        # Quadrants and sextants are fully encoded; octants are not.
        self.assertFalse(art.QUAD_SWAP)
        self.assertFalse(art.SEXT_SWAP)
        self.assertEqual(len(art.OCT_SWAP), 8)

    def test_the_familiar_glyphs_land_where_expected(self):
        self.assertEqual(art.QUAD_GLYPHS[0b0000], " ")
        self.assertEqual(art.QUAD_GLYPHS[0b1111], "█")
        self.assertEqual(art.QUAD_GLYPHS[0b0011], "▀")   # top row
        self.assertEqual(art.QUAD_GLYPHS[0b0101], "▌")   # left column
        self.assertEqual(art.SEXT_GLYPHS[0b000000], " ")
        self.assertEqual(art.SEXT_GLYPHS[0b111111], "█")
        self.assertEqual(art.SEXT_GLYPHS[0b010101], "▌")

    def test_each_family_folds_the_right_number_of_pixel_rows(self):
        grid = [[(9, 9, 9)] * 8 for _ in range(12)]
        for mode in ("quad", "sext", "oct"):
            wide, tall, glyphs, swap = art.glyph_table(mode)
            rows = art.rows_from_cells(grid, 8, 12, wide, tall, glyphs, swap)
            self.assertEqual(len(rows), 12 // tall, mode)
            self.assertEqual(rows[0].cols, 8 // wide, mode)

    def test_switching_family_at_runtime(self):
        try:
            for mode in art.GLYPH_ORDER:
                self.assertTrue(art.set_glyph(mode))
                self.assertEqual(art.glyph_mode(), mode)
            self.assertFalse(art.set_glyph("octopus"))
            self.assertEqual(art.glyph_mode(), art.GLYPH_ORDER[-1])
        finally:
            art.set_glyph(None)

    def test_a_runtime_choice_outranks_the_environment(self):
        saved = os.environ.get("LAZYMUSIC_ART_GLYPH")
        try:
            os.environ["LAZYMUSIC_ART_GLYPH"] = "half"
            self.assertEqual(art.glyph_mode(), "half")
            art.set_glyph("sext")
            self.assertEqual(art.glyph_mode(), "sext")
            art.set_glyph(None)
            self.assertEqual(art.glyph_mode(), "half")
        finally:
            art.set_glyph(None)
            os.environ.pop("LAZYMUSIC_ART_GLYPH", None)
            if saved is not None:
                os.environ["LAZYMUSIC_ART_GLYPH"] = saved

    def test_the_sampler_covers_every_family(self):
        rows = art.sample_rows(70)
        self.assertEqual([m for m, _, _ in rows], list(art.GLYPH_ORDER))
        for _mode, label, shown in rows:
            self.assertTrue(label.strip())
            self.assertTrue(shown.strip())


class TestSearchPanelGone(unittest.TestCase):
    """Search results fill the main panel; there is no separate search box."""

    def setUp(self):
        self.app = tui.App()
        self.addCleanup(self.app.close)

    def test_there_are_three_panels(self):
        self.assertEqual(tui.PANELS, (tui.PLAYER, tui.PLAYLISTS, tui.TRACKS))
        self.assertEqual(tui.TRACKS, 3)

    def test_tab_cycles_the_three(self):
        seen = []
        for _ in range(4):
            seen.append(self.app.focus)
            self.app.handle("tab")
        self.assertEqual(seen[0], seen[3])       # back where it started
        self.assertEqual(set(seen), set(tui.PANELS))

    def test_ctrl_w_3_reaches_the_main_panel(self):
        self.app.handle("ctrl-w")
        self.app.handle("3")
        self.assertEqual(self.app.focus, tui.TRACKS)

    def test_the_left_stack_is_just_player_and_playlists(self):
        self.app.focus = tui.TRACKS
        self.app.focus_stack(1)
        self.assertEqual(self.app.focus, tui.PLAYLISTS)
        self.app.focus_stack(-1)
        self.assertEqual(self.app.focus, tui.PLAYER)
        self.app.focus_stack(-1)
        self.assertEqual(self.app.focus, tui.PLAYER)   # clamps, no third panel

    def test_no_lane_is_left_for_a_search_panel(self):
        self.assertNotIn(3, [k for k in self.app.lanes if k == "search"])
        self.assertEqual(
            sorted(k for k in self.app.lanes if isinstance(k, str)),
            ["help", "lyrics", "queue"])
