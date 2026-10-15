from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re
import warnings

from ass import format_ass_dialogue, get_ass_header
from engine import OCREngine
from languages import get_language_config
from srt import format_srt_block
from utils import parse_filename_timecodes, text_cleanup, timecode_key


@dataclass
class SubtitleItem:
    start_ms: int
    end_ms: int
    text_content: str
    is_top: bool = False

    def to_ass(self) -> str:
        return format_ass_dialogue(self.start_ms, self.end_ms, self.text_content, self.is_top)

    def to_srt(self, index: int) -> str:
        return format_srt_block(self.start_ms, self.end_ms, self.text_content, index, self.is_top)


def _strip_tags(text: str) -> str:
    """Strips ASS and SRT formatting tags for pure text comparison."""
    return re.sub(r"\{\\[^}]*\}|<[^>]*>", "", text)


def _pick_best(prev: str, curr: str) -> str:
    """Prefers text that contains styling tags (e.g. italics) or is more complete."""
    p_has_fmt = r"{\i" in prev or "<i>" in prev
    c_has_fmt = r"{\i" in curr or "<i>" in curr
    if c_has_fmt and not p_has_fmt:
        return curr
    if p_has_fmt and not c_has_fmt:
        return prev
    return curr if len(curr) >= len(prev) else prev


def should_merge_subtitles(
    prev_text: str, curr_text: str, gap_ms: int, lang: str = "vi"
) -> tuple[bool, str]:
    """
    Determines if two consecutive subtitle entries should be merged into one.
    Handles identical matches, substring overlaps, and fuzzy similarity based on language preset.
    Returns (should_merge, best_text).
    """
    if gap_ms > 1000:
        return False, ""

    p = prev_text.strip()
    c = curr_text.strip()
    if not p or not c:
        return False, ""

    # Exact match (case-insensitive)
    if p.lower() == c.lower():
        return True, _pick_best(p, c)

    # Normalize line breaks and formatting tags for comparison
    p_norm = _strip_tags(p).replace(r"\N", " ").replace(r"\n", " ").strip()
    c_norm = _strip_tags(c).replace(r"\N", " ").replace(r"\n", " ").strip()
    p_lower = p_norm.lower()
    c_lower = c_norm.lower()

    if p_lower == c_lower:
        return True, _pick_best(p, c)

    cfg = get_language_config(lang)
    max_len_diff = 2 if cfg.cjk_mode else 3

    # Substring match with small length difference
    len_diff = abs(len(p_lower) - len(c_lower))
    if (p_lower in c_lower or c_lower in p_lower) and len_diff <= max_len_diff:
        return True, _pick_best(p, c)

    # Fuzzy similarity using difflib
    ratio = SequenceMatcher(None, p_lower, c_lower).ratio()
    min_len = min(len(p_lower), len(c_lower))
    threshold = cfg.fuzzy_threshold_short if min_len < 10 else cfg.fuzzy_threshold_long

    if ratio >= threshold:
        p_clean = _strip_tags(p).strip()
        c_clean = _strip_tags(c).strip()
        has_p_punct = p_clean[-1] in ".!?…\"'。！？" if p_clean else False
        has_c_punct = c_clean[-1] in ".!?…\"'。！？" if c_clean else False
        if has_c_punct and not has_p_punct:
            best = c
        elif has_p_punct and not has_c_punct:
            best = p
        else:
            best = _pick_best(p, c)
        return True, best

    return False, ""


class OCR_Subtitles:
    def __init__(
        self,
        output_subtitles_name: str | Path,
        output_directory: str | Path,
        images_dir_override: str | Path,
        ocr_engine: OCREngine,
        subtitle_format: str = "ass",
        lang: str = "vi",
        font: str | None = None,
        use_cache: bool = True,
    ) -> None:
        self.sub_items: dict[str, SubtitleItem] = {}
        self.empty_images: list[str] = []
        self.ocr_engine = ocr_engine
        self.subtitle_format = subtitle_format.lower()
        self.lang = lang
        self.lang_cfg = get_language_config(lang)
        self.font = font or self.lang_cfg.ass_font

        self.images_dir, self.output_files = self._process_file(
            output_subtitles_name, output_directory, images_dir_override
        )
        self.cache_path = Path(output_directory).resolve() / ".ocr_cache.json" if use_cache else None

    def __call__(self):
        results = self.ocr_engine(self.images_dir, cache_path=self.cache_path)

        if not results:
            warnings.warn("No images processed or no text extracted.")
            return

        for img_name, text in results.items():
            self._create_subtitle(img_name, text)

        self._write_subtitles()

        for fmt, path in self.output_files.items():
            print(f"Saved {fmt.upper()} subtitle to {path}")

        self._report_empty_images()

    def _report_empty_images(self, show: int = 10) -> None:
        """Lists images that produced no text, so they can be checked by hand (or OCRed again)."""
        if not self.empty_images:
            return
        subtitle_path = next(iter(self.output_files.values()))
        report_path = subtitle_path.with_name(f"{subtitle_path.stem}_no_text.txt")
        report_path.write_text(
            "".join(f"{self.images_dir / name}\n" for name in self.empty_images), encoding="utf-8"
        )
        print(f"{len(self.empty_images)} image(s) gave no text (not in the subtitles), listed in {report_path}:")
        for name in self.empty_images[:show]:
            print(f"  {name}")
        if len(self.empty_images) > show:
            print(f"  ... and {len(self.empty_images) - show} more")

    def _process_file(
        self,
        output_subtitles_name: str | Path,
        output_directory: str | Path,
        images_dir_override: str | Path | None = None,
    ) -> tuple[Path, dict[str, Path]]:
        try:
            output_path = Path(output_directory).resolve()
            if images_dir_override:
                images_dir_path = Path(images_dir_override).resolve()
            else:
                images_dir_path = (Path.cwd() / "images").resolve()

            output_path.mkdir(parents=True, exist_ok=True)

            # Sanitize output name to prevent extension doubling like name.ass.ass
            clean_name = Path(output_subtitles_name).stem

            counter = 0
            while True:
                suffix = f"_{counter}" if counter > 0 else ""
                test_ass = output_path / f"{clean_name}{suffix}.ass"
                test_srt = output_path / f"{clean_name}{suffix}.srt"

                conflict = False
                if self.subtitle_format in ("ass", "both") and test_ass.exists():
                    conflict = True
                if self.subtitle_format in ("srt", "both") and test_srt.exists():
                    conflict = True

                if conflict:
                    counter += 1
                else:
                    break

            output_files: dict[str, Path] = {}
            if self.subtitle_format in ("ass", "both"):
                output_files["ass"] = output_path / f"{clean_name}{suffix}.ass"
            if self.subtitle_format in ("srt", "both"):
                output_files["srt"] = output_path / f"{clean_name}{suffix}.srt"

            return (images_dir_path, output_files)
        except Exception as e:
            print(f"Error type: {type(e).__name__}")
            print(f"Error details: {e}")
            raise

    def _create_subtitle(self, img_name: str, text: str):
        if text is None:
            text = ""

        text = text_cleanup(text, lang=self.lang)
        if not text.strip():
            self.empty_images.append(img_name)

        try:
            start_ms, end_ms, is_top = parse_filename_timecodes(img_name)
        except Exception as e:
            print(f"Error parsing timecodes for {img_name}: {e}")
            return

        item = SubtitleItem(
            start_ms=start_ms,
            end_ms=end_ms,
            text_content=text,
            is_top=is_top,
        )
        self.sub_items[img_name] = item

    def _write_subtitles(self):
        cleaned_bot: list[SubtitleItem] = []
        cleaned_top: list[SubtitleItem] = []
        previous_bot: SubtitleItem | None = None
        previous_top: SubtitleItem | None = None

        for _, item in sorted(self.sub_items.items(), key=lambda pair: timecode_key(pair[0])):
            previous_sub = previous_top if item.is_top else previous_bot
            cleaned_list = cleaned_top if item.is_top else cleaned_bot

            if not item.text_content or item.text_content.isspace():
                continue

            if not previous_sub:
                cleaned_list.append(item)
                if item.is_top:
                    previous_top = item
                else:
                    previous_bot = item
                continue

            gap_ms = item.start_ms - previous_sub.end_ms
            should_merge, best_text = should_merge_subtitles(
                previous_sub.text_content, item.text_content, gap_ms, lang=self.lang
            )

            if should_merge:
                merged_end_ms = max(item.end_ms, previous_sub.end_ms)
                merged_item = SubtitleItem(
                    start_ms=previous_sub.start_ms,
                    end_ms=merged_end_ms,
                    text_content=best_text,
                    is_top=item.is_top,
                )
                cleaned_list.pop()
                cleaned_list.append(merged_item)
            else:
                cleaned_list.append(item)

            if item.is_top:
                previous_top = cleaned_list[-1]
            else:
                previous_bot = cleaned_list[-1]

        all_subtitles = sorted(
            cleaned_bot + cleaned_top,
            key=lambda s: s.start_ms,
        )

        try:
            # Write ASS format
            if "ass" in self.output_files:
                ass_header = get_ass_header(self.font)
                with self.output_files["ass"].open("w", encoding="utf-8") as ass_file:
                    _ = ass_file.write(ass_header)
                    for item in all_subtitles:
                        _ = ass_file.write(item.to_ass())

            # Write SRT format (with exact millisecond timestamps from filenames)
            if "srt" in self.output_files:
                with self.output_files["srt"].open("w", encoding="utf-8") as srt_file:
                    for idx, item in enumerate(all_subtitles, 1):
                        _ = srt_file.write(item.to_srt(idx))

        except IOError as e:
            print(f"Error writing subtitle output: {e}")
            raise

