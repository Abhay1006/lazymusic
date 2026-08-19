"""Unit tests for the pieces that do not need Music.app running."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lazymusic import fmt, osa  # noqa: E402
from lazymusic.cli import _relative  # noqa: E402
from lazymusic.music import REPEAT_MODES, State, Track, _num  # noqa: E402
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
