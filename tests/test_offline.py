"""Offline unit tests (no network). Run with: python -m unittest discover -s tests"""

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from dvdsub import DvdSubtitle, IdxParser, VobSubParser
from ass import format_ass_dialogue
from engine import OCREngine
from ocr import OCR_Subtitles, should_merge_subtitles
from pgs import PgsParser, save_subtitle_images
from run import create_arg_parser, filter_by_time
from utils import (
    format_vsf_time,
    get_image_raw_bytes_and_dims,
    parse_filename_timecodes,
    parse_time_ms,
    text_cleanup,
    timecode_key,
)
from vsf import VideoSubFinder


class TextCleanupTest(unittest.TestCase):
    def test_dot_spacing_keeps_urls_and_abbreviations(self):
        self.assertEqual(text_cleanup("Truy cập www.google.com nhé", "vi"), "Truy cập www.google.com nhé")
        self.assertEqual(text_cleanup("e.g. U.S.A 3.5", "en"), "e.g. U.S.A 3.5")

    def test_dot_spacing_still_splits_sentences(self):
        self.assertEqual(text_cleanup("Xin chào.Bạn khỏe không?", "vi"), "Xin chào. Bạn khỏe không?")
        self.assertEqual(text_cleanup("Mr.Smith", "en"), "Mr. Smith")
        self.assertEqual(text_cleanup("Wait,what", "en"), "Wait, what")

    def test_music_and_symbols_are_kept(self):
        self.assertEqual(text_cleanup("♪ La la la ♪", "vi"), "♪ La la la ♪")
        self.assertEqual(text_cleanup("Tom & Jerry #1", "en"), "Tom & Jerry #1")
        self.assertIn("♪", text_cleanup("♪ 歌 ♪", "ja"))

    def test_italic_tags_and_line_breaks(self):
        self.assertEqual(text_cleanup("{\\i1}Hello{\\i0}\nWorld", "en"), "{\\i1}Hello{\\i0}\\NWorld")

    def test_cjk_spaces_removed(self):
        self.assertEqual(text_cleanup("こ ん に ち は 。", "ja"), "こんにちは。")
        self.assertEqual(text_cleanup("はは ! わっははは!", "ja"), "はは! わっははは!")
        self.assertEqual(text_cleanup("(頭目) 待て待て", "ja"), "(頭目)待て待て")

    def test_cjk_ellipsis_normalized(self):
        # Real Google Lens output for 'あ…フェイ…だぁ…'
        self.assertEqual(text_cleanup("あ ･･･ ... フェイ ･･･ ... だぁ･･･ ...\nやほーい･･･", "ja"), "あ…フェイ…だぁ…\\Nやほーい…")
        self.assertEqual(text_cleanup("ひょっとして･･…", "ja"), "ひょっとして…")
        self.assertEqual(text_cleanup("ワイルド･ホーセス", "ja"), "ワイルド・ホーセス")
        self.assertEqual(text_cleanup("そうか……", "ja"), "そうか……")


class TimecodeTest(unittest.TestCase):
    def test_vsf_name(self):
        self.assertEqual(
            parse_filename_timecodes("0_00_02_169__0_00_05_004_0000003940856008608560480.jpeg"),
            (2169, 5004, False),
        )

    def test_top_prefix_and_dedupe_suffix(self):
        self.assertEqual(parse_filename_timecodes("top_0_01_19_120__0_01_20_038_100x50_1.png"), (79120, 80038, True))

    def test_sort_key_matches_parser(self):
        name = "0_00_02_1690__0_00_05_004_x.png"
        self.assertEqual(timecode_key(name)[0], parse_filename_timecodes(name)[0])

    def test_sort_order(self):
        names = ["0_00_10_000__0_00_11_000.png", "0_00_09_99__0_00_10_000.png", "bad.png"]
        self.assertEqual(sorted(names, key=timecode_key), [names[1], names[0], names[2]])


class TimeRangeTest(unittest.TestCase):
    def test_parse_formats(self):
        cases = {
            "90": 90000, "90.5": 90500, "1:30": 90000, "0:01:30": 90000, "0:01:30.5": 90500,
            "0:01:30,250": 90250, "0:01:30:500": 90500, "1:02:03:004": 3723004,
        }
        for value, ms in cases.items():
            self.assertEqual(parse_time_ms(value), ms, value)
        for bad in ("abc", "", "-5", "1:2:3:4:5", "0:01:30.5:100"):
            with self.assertRaises(ValueError, msg=bad):
                parse_time_ms(bad)

    def test_cli(self):
        args = create_arg_parser().parse_args(["-i", "a.sup", "--start_time", "1:30", "--end_time", "0:02:00.250"])
        self.assertEqual((args.start_time, args.end_time), (90000, 120250))
        with self.assertRaises(SystemExit):
            create_arg_parser().parse_args(["-i", "a.sup", "--start_time", "soon"])

    def test_vsf_gets_its_own_format(self):
        self.assertEqual(format_vsf_time(3723004), "1:02:03:004")

    def test_filter_keeps_subtitles_overlapping_the_range(self):
        im = Image.new("RGB", (10, 10))
        subs = [DvdSubtitle(s, e, im) for s, e in ((0, 1000), (900, 2100), (2000, 3000), (5000, 6000))]
        kept = filter_by_time(subs, 1000, 5000)
        self.assertEqual([(s.start_ms, s.end_ms) for s in kept], [(900, 2100), (2000, 3000)])
        self.assertEqual(len(filter_by_time(subs, None, 1000)), 2)
        self.assertIs(filter_by_time(subs), subs)


class MergeTest(unittest.TestCase):
    def test_merge_similar(self):
        self.assertEqual(should_merge_subtitles("Anh đi đâu?", "Anh đi đâu?", 100), (True, "Anh đi đâu?"))

    def test_no_merge_after_gap(self):
        self.assertFalse(should_merge_subtitles("Anh đi đâu?", "Anh đi đâu?", 1500)[0])


class _FakeEngine(OCREngine):
    engine_name = "fake"

    def process_image(self, img_path: str) -> str:
        return ""


class FormatTextTest(unittest.TestCase):
    def test_paragraph_mode_keeps_italic(self):
        engine = _FakeEngine(line_break_mode="paragraph")
        a = {"text": "Hello", "cy": 0.2, "cx": 0.5, "h": 0.1, "is_italic": True}
        b = {"text": "world", "cy": 0.3, "cx": 0.5, "h": 0.1, "is_italic": True}
        c = {"text": "Next", "cy": 0.8, "cx": 0.5, "h": 0.1, "is_italic": False}
        self.assertEqual(engine.format_recognized_text([a, b, c], [[a, b], [c]]), "{\\i1}Hello world{\\i0}\nNext")

    def test_y_cluster(self):
        engine = _FakeEngine()
        lines = [
            {"text": "B", "cy": 0.8, "cx": 0.5, "h": 0.1},
            {"text": "A2", "cy": 0.21, "cx": 0.7, "h": 0.1},
            {"text": "A1", "cy": 0.2, "cx": 0.3, "h": 0.1},
        ]
        self.assertEqual(engine.format_recognized_text(lines, None), "A1 A2\nB")


class _CountingEngine(OCREngine):
    """Returns text from a dict keyed by image name and counts the calls."""

    engine_name = "counting"

    def __init__(self, texts: dict[str, str]):
        super().__init__(threads=2)
        self.texts = texts
        self.calls: list[str] = []

    def process_image(self, img_path: str) -> str:
        name = Path(img_path).name
        self.calls.append(name)
        return self.texts.get(name, "")


class OcrRunTest(unittest.TestCase):
    NAMES = ["0_00_01_000__0_00_02_000_a.png", "0_00_03_000__0_00_04_000_b.png", "0_00_05_000__0_00_06_000_c.png"]

    def _make_images(self, folder: Path):
        folder.mkdir(parents=True)
        for i, name in enumerate(self.NAMES):
            Image.new("RGB", (60, 60), (i * 40, 0, 0)).save(folder / name)

    def test_cache_skips_done_images_and_retries_empty_ones(self):
        texts = {self.NAMES[0]: "One", self.NAMES[1]: "Two"}  # third image gives no text
        with tempfile.TemporaryDirectory() as d:
            images, out = Path(d) / "imgs", Path(d) / "out"
            self._make_images(images)

            first = _CountingEngine(texts)
            OCR_Subtitles("sub", out, images, first, subtitle_format="srt")()
            self.assertEqual(sorted(first.calls), sorted(self.NAMES))

            second = _CountingEngine(texts)
            OCR_Subtitles("sub", out, images, second, subtitle_format="srt")()
            self.assertEqual(second.calls, [self.NAMES[2]])  # only the empty one is sent again

            # Different settings don't reuse the results
            third = _CountingEngine(texts)
            third.line_break_mode = "line"
            OCR_Subtitles("sub", out, images, third, subtitle_format="srt")()
            self.assertEqual(len(third.calls), 3)

            no_cache = _CountingEngine(texts)
            OCR_Subtitles("sub", out, images, no_cache, subtitle_format="srt", use_cache=False)()
            self.assertEqual(len(no_cache.calls), 3)

    def test_images_without_text_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            images, out = Path(d) / "imgs", Path(d) / "out"
            self._make_images(images)
            ocr = OCR_Subtitles("sub", out, images, _CountingEngine({self.NAMES[0]: "One"}), subtitle_format="srt")
            ocr()
            report = (out / "sub_no_text.txt").read_text(encoding="utf-8").splitlines()
            srt = (out / "sub.srt").read_text(encoding="utf-8")
        self.assertEqual([Path(p).name for p in report], self.NAMES[1:])
        self.assertIn("One", srt)


class AssTest(unittest.TestCase):
    def test_stray_braces_escaped_but_italic_tags_kept(self):
        line = format_ass_dialogue(0, 1000, "{\\i1}Hi{\\i0} {sic} a}b")
        self.assertTrue(line.endswith(",{\\i1}Hi{\\i0} ｛sic｝ a｝b\n"), line)


class ImagePrepTest(unittest.TestCase):
    def _roundtrip(self, size):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "img.png"
            Image.new("RGB", size, (255, 255, 255)).save(p)
            return get_image_raw_bytes_and_dims(str(p))

    def test_wide_strip_stays_within_limits(self):
        _, w, h = self._roundtrip((2900, 40))
        self.assertLessEqual(w, 3000)
        self.assertGreaterEqual(h, 50)

    def test_small_image_upscaled(self):
        _, w, h = self._roundtrip((30, 20))
        self.assertGreaterEqual(min(w, h), 50)


class VsfArgsTest(unittest.TestCase):
    def _run_list(self, *argv):
        args = create_arg_parser().parse_args(["-i", "x.mp4", "-vsf", sys.executable, *argv])
        return VideoSubFinder(**vars(args)).run_list

    def test_default_opencv(self):
        run_list = self._run_list()
        self.assertIn("--open_video_opencv", run_list)
        self.assertNotIn("--open_video_ffmpeg", run_list)

    def test_ffmpeg_flag(self):
        run_list = self._run_list("--open_video_ffmpeg")
        self.assertIn("--open_video_ffmpeg", run_list)
        self.assertNotIn("--open_video_opencv", run_list)


class DvdTest(unittest.TestCase):
    def test_long_valid_duration_kept(self):
        im = Image.new("RGB", (10, 10))
        subs = [DvdSubtitle(0, 10000, im), DvdSubtitle(20000, 20000, im), DvdSubtitle(21000, 21000, im)]
        VobSubParser._fix_subtitle_durations(subs)
        self.assertEqual(subs[0].end_ms, 10000)
        self.assertEqual(subs[1].end_ms, 20950)
        self.assertEqual(subs[2].end_ms, 24000)

    def test_idx_delay(self):
        with tempfile.TemporaryDirectory() as d:
            idx = Path(d) / "a.idx"
            idx.write_text(
                "size: 720x576\n"
                "id: en, index: 0\n"
                "delay: 00:00:01:500\n"
                "timestamp: 00:00:10:000, filepos: 000000000\n"
                "id: vi, index: 1\n"
                "timestamp: 00:00:10:000, filepos: 000000800\n",
                encoding="utf-8",
            )
            parsed = IdxParser(idx)
        self.assertEqual(parsed.entries, [(11500, 0, 0), (10000, 0x800, 1)])
        self.assertEqual((parsed.screen_width, parsed.screen_height), (720, 576))

    def test_stream_selection_skips_empty_streams(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.idx").write_text(
                "time offset: 500\n"
                "langidx: 2\n"
                "id: ko, index: 0\n"
                "id: ja, index: 1\n"
                "timestamp: 00:00:01:000, filepos: 000000000\n"
                "id: en, index: 2\n"
                "timestamp: 00:00:02:000, filepos: 000000800\n",
                encoding="utf-8",
            )
            vp = VobSubParser(Path(d) / "a.sub")
        self.assertEqual(vp.idx.entries[0][0], 1500)
        self.assertEqual(vp.select_stream(lang_code="ja"), 1)
        self.assertEqual(vp.select_stream(lang_code="ko"), 2)  # ko has no subtitles -> langidx
        self.assertEqual(vp.select_stream(lang_code="vi"), 2)
        self.assertEqual(vp.select_stream(stream_id=0), 0)


def _pgs_segment(seg_type: int, payload: bytes, pts: int) -> bytes:
    return b"PG" + pts.to_bytes(4, "big") + (0).to_bytes(4, "big") + bytes([seg_type]) + len(payload).to_bytes(2, "big") + payload


def _pgs_rle(width: int, height: int, color: int) -> bytes:
    return (bytes([color]) * width + b"\x00\x00") * height


def _pgs_display(objects: list[tuple[int, int, int, int]], ms: int, comp_num: int, state: int) -> bytes:
    pts = ms * 90
    pcs = (1920).to_bytes(2, "big") + (1080).to_bytes(2, "big") + b"\x10" + comp_num.to_bytes(2, "big")
    pcs += bytes([state, 0, 0, len(objects)])
    for oid, x, y, _ in objects:
        pcs += oid.to_bytes(2, "big") + b"\x00\x00" + x.to_bytes(2, "big") + y.to_bytes(2, "big")
    data = _pgs_segment(0x16, pcs, pts)
    # Palette: index 1 = white, opaque (Y=235, Cr=Cb=128)
    data += _pgs_segment(0x14, b"\x00\x00" + bytes([1, 235, 128, 128, 255]), pts)
    for oid, _, _, w in objects:
        rle = _pgs_rle(w, 20, 1)
        ods = oid.to_bytes(2, "big") + b"\x00\xc0" + (len(rle) + 4).to_bytes(3, "big")
        ods += w.to_bytes(2, "big") + (20).to_bytes(2, "big") + rle
        data += _pgs_segment(0x15, ods, pts)
    return data + _pgs_segment(0x80, b"", pts)


def _build_pgs(objects: list[tuple[int, int, int, int]], start_ms: int, end_ms: int, repeats: int = 0) -> bytes:
    """
    objects: (object_id, x, y, width) with height 20. Returns a .sup with one display, `repeats`
    acquisition points of it one second apart, and a clear at end_ms.
    """
    data = _pgs_display(objects, start_ms, 0, 0x80)
    for r in range(1, repeats + 1):
        data += _pgs_display(objects, start_ms + r * 1000, 0, 0x40)
    return data + _pgs_display([], end_ms, 1, 0x00)


class PgsTest(unittest.TestCase):
    def _parse(self, objects, end_ms=3000, repeats=0):
        with tempfile.TemporaryDirectory() as d:
            sup = Path(d) / "a.sup"
            sup.write_bytes(_build_pgs(objects, 1000, end_ms, repeats))
            return PgsParser(sup).parse()

    def test_time_range_skips_other_subtitles(self):
        with tempfile.TemporaryDirectory() as d:
            sup = Path(d) / "a.sup"
            sup.write_bytes(_build_pgs([(0, 100, 900, 60)], 1000, 3000))
            self.assertEqual(len(PgsParser(sup).parse(range_start_ms=2000, range_end_ms=9000)), 1)
            self.assertEqual(len(PgsParser(sup).parse(range_start_ms=3000)), 0)
            self.assertEqual(len(PgsParser(sup).parse(range_end_ms=1000)), 0)

    def test_acquisition_points_are_one_subtitle(self):
        subs = self._parse([(0, 100, 900, 60)], end_ms=5500, repeats=3)
        self.assertEqual([(s.start_ms, s.end_ms) for s in subs], [(1000, 5500)])

    def test_single_bottom_object(self):
        subs = self._parse([(0, 100, 900, 60)])
        self.assertEqual(len(subs), 1)
        self.assertEqual((subs[0].start_ms, subs[0].end_ms, subs[0].is_top), (1000, 3000, False))
        # Decoded white pixels survive the crop + black flattening
        self.assertEqual(subs[0].image.getpixel((15, 15)), (255, 255, 255))

    def test_top_and_bottom_split(self):
        subs = self._parse([(0, 100, 50, 60), (1, 100, 900, 80)])
        self.assertEqual(sorted(s.is_top for s in subs), [False, True])
        bottom = next(s for s in subs if not s.is_top)
        self.assertEqual(bottom.origin_y, 900)

    def test_save_dedupes_names(self):
        im = Image.new("RGB", (10, 10))
        subs = [DvdSubtitle(1000, 2000, im), DvdSubtitle(1000, 2000, im)]
        with tempfile.TemporaryDirectory() as d:
            save_subtitle_images(subs, Path(d))
            names = sorted(p.name for p in Path(d).iterdir())
        self.assertEqual(names, ["0_00_01_000__0_00_02_000_10x10.png", "0_00_01_000__0_00_02_000_10x10_1.png"])
        for n in names:
            self.assertEqual(parse_filename_timecodes(n)[:2], (1000, 2000))


if __name__ == "__main__":
    unittest.main()
