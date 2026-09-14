from __future__ import annotations

import argparse
import io
import math
import os
import platform
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable, List

from PIL import Image, UnidentifiedImageError

if TYPE_CHECKING:
    from engine import OCREngine, OCREngineType

from languages import get_language_config

DOUBLE_QUOTE_REGEX = re.compile(
    "|".join(["«", "‹", "»", "›", "„", "“", "‟", "”", "❝", "❞", "❮", "❯", "〝", "〞", "〟", "＂"])
)

SINGLE_QUOTE_REGEX = re.compile("|".join(["‘", "‛", "’", "❛", "❜", "`", "´"]))

CJK_CHAR_REGEX = r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"


def apply_punctuation_and_spacing(text: str) -> str:
    # Collapse multiple horizontal spaces
    text = re.sub(r"[ \t]{2,}", " ", text)

    # Remove extra spaces before punctuation
    text = re.sub(r"\s+([,.!?…])", r"\1", text)

    # Ensure single space after punctuation, except for multiple punctuation marks and numbers/decimals
    text = re.sub(r"([,.!?…])(?!\s)(?![,.!?…])(?!\d)", r"\1 ", text)

    # Remove space between multiple punctuation marks
    text = re.sub(r"([,.!?…])\s+([,.!?…])", r"\1\2", text)

    return text.strip()


def fix_quotes(text: str) -> str:
    text = SINGLE_QUOTE_REGEX.sub("'", text)
    text = DOUBLE_QUOTE_REGEX.sub('"', text)
    return text


def text_cleanup(text: str, lang: str = "vi") -> str:
    if text is None:
        return ""

    cfg = get_language_config(lang)

    # Replace real newlines and literal \n with \N
    text = text.replace("\r\n", r"\N").replace("\n", r"\N")
    text = re.sub(r"\\n", r"\\N", text.strip())

    if cfg.cjk_mode:
        # CJK Mode (Japanese, Chinese)
        text = SINGLE_QUOTE_REGEX.sub("'", text)
        text = re.sub(r'[“”]', '"', text)

        # Remove OCR-generated spaces between CJK characters and around CJK punctuation
        if cfg.strip_cjk_spaces:
            # Remove space before CJK closing/middle punctuation
            text = re.sub(r"\s+([、。，！？：；）》」』】…〜])", r"\1", text)
            # Remove space after CJK opening/middle punctuation
            text = re.sub(r"([、。，！？：；（《「『【…〜])\s+", r"\1", text)
            # Remove spaces between CJK characters
            while re.search(rf"({CJK_CHAR_REGEX})\s+({CJK_CHAR_REGEX})", text):
                text = re.sub(rf"({CJK_CHAR_REGEX})\s+({CJK_CHAR_REGEX})", r"\1\2", text)

        # Whitelist for CJK text: Kana, Kanji/Hanzi, CJK punctuation, Latin, numbers
        pattern = re.compile(
            r"[^\sa-zA-Z0-9"
            r"\u3000-\u303F"  # CJK symbols & punctuation (、 。 「 」 『 』 etc.)
            r"\u3040-\u309F"  # Hiragana
            r"\u30A0-\u30FF"  # Katakana
            r"\u31F0-\u31FF"  # Katakana Phonetic Extensions
            r"\u3400-\u4DBF"  # CJK Extension A
            r"\u4E00-\u9FFF"  # CJK Unified Ideographs
            r"\uF900-\uFAFF"  # CJK Compatibility
            r"\uFF00-\uFFEF"  # Halfwidth & Fullwidth Forms
            r"!\[\]_\$ /\\:\'\" ,.\?;\-+=()*%@~…"
            r"]"
        )
        text = re.sub(pattern, "", text)

    else:
        # Latin / Vietnamese mode
        text = fix_quotes(text)

        # Preserve apostrophes/single quotes, semicolons, ellipses, and common punctuation
        allowed_special_chars = r' ![]_$ /\\:\'" ,.?;\-+=()*%@~…'
        pattern = re.compile(
            rf"[^\sa-zA-Z"
            r"\u00C0-\u017F"  # Latin Extended-A
            r"\u0180-\u024F"  # Latin Extended-B (ư, ơ)
            r"\u1E00-\u1EFF"  # Latin Extended Additional (accented letters)
            r"0-9"
            rf"{re.escape(allowed_special_chars)}]"
        )
        text = re.sub(pattern, "", text)

        # Normalize punctuation and spacing per line
        lines = text.split(r"\N")
        cleaned_lines = [apply_punctuation_and_spacing(l) for l in lines if l.strip()]
        text = r"\N".join(cleaned_lines)

    # Collapse multiple \N and trim at ends
    text = re.sub(r"(?:\s*\\N\s*){2,}", r"\\N", text)
    text = re.sub(r"^\\N\s*|\s*\\N$", "", text)

    return text.strip()



def get_image_raw_bytes_and_dims(image_path: str) -> tuple[bytes, int, int] | None:

    try:
        with Image.open(image_path) as img:
            width = img.width
            height = img.height
            max_pixels = 3_000_000
            if width * height > max_pixels:
                aspect_ratio = width / height
                new_w = int(math.sqrt(max_pixels * aspect_ratio))
                new_h = int(new_w / aspect_ratio)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                width, height = img.size
            image_bytes = io.BytesIO()
            img.save(image_bytes, format="PNG", compress_level=1)
            return (image_bytes.getvalue(), width, height)

    except FileNotFoundError:
        print(f"Error: Image file not found at '{image_path}'")
        return None
    except UnidentifiedImageError:
        print(f"Error: Pillow (PIL) cannot identify '{image_path}' as an image. Cannot get dimensions.")
        return None
    except IOError as e:
        print(f"Error reading raw file bytes from '{image_path}': {e}")
        return None
    except Exception as e:
        # Add type hints to help static analysis if possible, but Exception is broad
        print(f"An unexpected error occurred processing '{image_path}': {e}")
        return None


def float_range(mini: float, maxi: float) -> Callable[..., float]:
    """Return function handle of an argument type function for
    ArgumentParser checking a float range: mini <= arg <= maxi
      mini - minimum acceptable argument
      maxi - maximum acceptable argument"""

    # Define the function with default arguments
    def float_range_checker(arg):
        """New Type function for argparse - a float within predefined range."""

        try:
            f = float(arg)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a floating point number") from exc

        if f < mini or f > maxi:
            raise argparse.ArgumentTypeError("must be in range [" + str(mini) + " .. " + str(maxi) + "]")
        return f

    # Return function handle to checking function
    return float_range_checker


def ocr_engine_type(value: str):
    from engine import OCREngineType

    try:
        return OCREngineType.from_string(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))

def get_in_path(name: str) -> str | None:
    search_name = name
    if platform.system() == "Windows":
        search_name = name + ".exe"

    try:
        path = shutil.which(search_name)
        # Scoop
        if platform.system() == "Windows":
            shim_path = shutil.which(f"{name}.shim")
            if shim_path:
                scoop_app_path = Path(shim_path).parents[1] / "apps" / "videosubfinder" / "current" / "VideoSubFinderWXW.exe"
                if scoop_app_path.is_file():
                    path = str(scoop_app_path)
        if path and os.access(path, os.X_OK):
            return path
        else:
            return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def collect_images(images_dir: Path | str) -> List[Path]:
    """Collect all image files from directory."""
    images_dir = Path(images_dir)
    valid_suffixes = {".jpeg", ".jpg", ".png", ".bmp", ".gif", ".webp"}
    images = [
        p for p in images_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in valid_suffixes
    ]
    return sorted(images)
    
def parse_filename_timecodes(filename: str | Path) -> tuple[int, int, bool]:
    """
    Extracts start_ms, end_ms, and is_top flag directly from VideoSubFinder image filename.
    Returns (start_ms, end_ms, is_top).
    Example: '0_00_02_169__0_00_05_004_0000003940856008608560480.jpeg'
    """
    name = Path(filename).name
    name_without_ext = name.rsplit(".", 1)[0]
    is_top = name_without_ext.startswith("top_")

    if name_without_ext.startswith(("top_", "bot_")):
        timecode_part = "_".join(name_without_ext.split("_")[1:])
    else:
        timecode_part = name_without_ext

    start_raw, end_raw = timecode_part.split("__")

    s_parts = start_raw.split("_")
    s_h, s_m, s_s, s_frac = int(s_parts[0]), int(s_parts[1]), int(s_parts[2]), s_parts[3]
    s_ms = int(s_frac) * 10 if len(s_frac) == 2 else int(s_frac.ljust(3, "0")[:3])
    start_ms = s_h * 3600000 + s_m * 60000 + s_s * 1000 + s_ms

    e_parts = end_raw.split("_")
    e_h, e_m, e_s, e_frac = int(e_parts[0]), int(e_parts[1]), int(e_parts[2]), e_parts[3]
    e_ms = int(e_frac) * 10 if len(e_frac) == 2 else int(e_frac.ljust(3, "0")[:3])
    end_ms = e_h * 3600000 + e_m * 60000 + e_s * 1000 + e_ms

    return start_ms, end_ms, is_top


def timecode_key(item):
    if isinstance(item, tuple):
        filename = str(item[0])
    elif isinstance(item, (Path, str)):
        filename = str(item)
    else:
        filename = str(item)
    filename = Path(filename).name
    try:
        name_without_ext = filename.rsplit('.', 1)[0]
        
        if name_without_ext.startswith(('bot_', 'top_')):
            timecode_part = '_'.join(name_without_ext.split('_')[1:])
        else:
            timecode_part = name_without_ext
        
        start_timecode = timecode_part.split('__')[0]
        parts = start_timecode.split('_')
        
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2])
        frac = parts[3]
        ms = int(frac) * 10 if len(frac) == 2 else int(frac)
        
        return (hour, minute, second, ms)
    except (IndexError, ValueError):
        return (99, 99, 99, 999999)

def create_ocr_engine(ocr_engine_type: OCREngineType, args) -> OCREngine:
    from engine import OCREngineType
    line_break_mode = getattr(args, "line_break_mode", "y_cluster")
    lang = getattr(args, "lang", "vi")

    if ocr_engine_type == OCREngineType.GGLENS:
        from gglens import GoogleLens
        return GoogleLens(threads=args.gglens_thread, line_break_mode=line_break_mode, lang=lang)

    elif ocr_engine_type == OCREngineType.BING:
        from bing import BingOCR
        threads = getattr(args, "bing_thread", 16)
        return BingOCR(threads=threads, line_break_mode=line_break_mode, lang=lang)

    else:
        raise ValueError(f"Unknown OCR engine: {ocr_engine_type}")