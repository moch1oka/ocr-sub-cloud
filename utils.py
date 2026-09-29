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

    # Remove extra spaces before punctuation (except dot followed by digit, e.g. .05)
    text = re.sub(r"\s+([,!?:;…]|\.(?!\d))", r"\1", text)

    # Ensure single space after punctuation, except for multiple punctuation marks, numbers/decimals, and quotes/brackets/braces
    text = re.sub(r"([,.!?…;])(?!\s)(?![,.!?…;])(?!\d)(?![)\"\'’”›»\]\}\{])", r"\1 ", text)

    # Remove space between multiple punctuation marks
    text = re.sub(r"([,.!?…])\s+([,.!?…])", r"\1\2", text)

    # Clean whitespace around formatting tags
    text = re.sub(r"(\{\\i1\}|<i\s*>)\s+", r"\1", text)
    text = re.sub(r"\s+(\{\\i0\}|</i\s*>)", r"\1", text)

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
            r"!\[\]_\$ /\\:\'\" ,.\?;\-+=()*%@~…{}"
            r"]"
        )
        text = re.sub(pattern, "", text)

    else:
        # Latin / Vietnamese mode
        text = fix_quotes(text)

        # Preserve apostrophes/single quotes, semicolons, ellipses, braces, and common punctuation
        allowed_special_chars = r' ![]_{}$ /\\:\'" ,.?;\-+=()*%@~…'
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



def get_image_raw_bytes_and_dims(
    image_path: str,
    min_dim: int = 50,
    max_dim: int = 3000,
    max_pixels: int = 3_000_000,
    max_bytes: int = 750_000,
) -> tuple[bytes, int, int] | None:
    """
    Loads, validates, and optionally resizes/re-encodes an image file.
    Ensures dimensions and payload size satisfy both Google Lens and Bing OCR API constraints:
    - min_dim >= 50px (prevents Bing 400 Bad Request on small text crops, improves OCR accuracy)
    - max_dim <= 3000px and total pixels <= 3,000,000 (prevents oversized payload rejections)
    - payload size <= 750KB (re-encodes as optimized JPEG if PNG is too large for Bing)
    - fast path: reads directly from disk if already a valid PNG/JPEG within bounds
    """
    try:
        file_size = os.path.getsize(image_path)
        with Image.open(image_path) as img:
            w, h = img.size
            fmt = (img.format or "").upper()

            # Fast path: already valid format, within dimensions, and file size <= max_bytes
            if (
                min_dim <= w <= max_dim
                and min_dim <= h <= max_dim
                and w * h <= max_pixels
                and file_size <= max_bytes
                and fmt in ("JPEG", "PNG")
            ):
                with open(image_path, "rb") as f:
                    return (f.read(), w, h)

            # Calculate scale if dimensions or pixel count exceed constraints
            if w < min_dim or h < min_dim:
                scale = max(min_dim / w, min_dim / h)
            else:
                scale = min(1.0, max_dim / w, max_dim / h, math.sqrt(max_pixels / (w * h)))

            if scale != 1.0:
                new_w = max(min_dim, int(round(w * scale)))
                new_h = max(min_dim, int(round(h * scale)))
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                w, h = img.size

            buf = io.BytesIO()
            if img.mode in ("RGBA", "LA", "P"):
                img.save(buf, format="PNG", compress_level=1)
            else:
                img.save(buf, format="JPEG", quality=90)
            raw_bytes = buf.getvalue()

            # If payload still exceeds max_bytes, convert to RGB JPEG
            if len(raw_bytes) > max_bytes:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90, optimize=True)
                raw_bytes = buf.getvalue()

            return (raw_bytes, w, h)

    except (FileNotFoundError, UnidentifiedImageError, IOError, Exception):
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

def is_italic_patch(gray_patch, threshold_angle: float = 8.0) -> bool:
    """
    Detects whether a cropped grayscale image patch containing text is italic
    by measuring vertical projection profile variance under affine shear angles.
    """
    if gray_patch is None or not hasattr(gray_patch, "shape"):
        return False
    h, w = gray_patch.shape[:2]
    if h < 8 or w < 12:
        return False

    try:
        import cv2
        import numpy as np

        _, thresh = cv2.threshold(gray_patch, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        border_mean = (thresh[0, :].mean() + thresh[-1, :].mean() + thresh[:, 0].mean() + thresh[:, -1].mean()) / 4.0
        if border_mean > 127:
            thresh = cv2.bitwise_not(thresh)

        if np.count_nonzero(thresh) < 20:
            return False

        # Test shear angles in degrees (-4 to 20)
        angles = [-4, 0, 4, 8, 11, 14, 17, 20]
        variances = []
        for angle in angles:
            rad = np.radians(angle)
            tan_a = float(np.tan(rad))
            new_w = w + int(h * abs(tan_a))
            offset_x = 0.0 if tan_a >= 0 else float(int(h * abs(tan_a)))
            M = np.float32([[1, tan_a, offset_x], [0, 1, 0]])
            sheared = cv2.warpAffine(thresh, M, (new_w, h), flags=cv2.INTER_NEAREST)
            proj = np.sum(sheared, axis=0)
            variances.append(float(np.var(proj)))

        best_idx = int(np.argmax(variances))
        best_angle = angles[best_idx]
        var_0 = variances[1]  # angle 0
        var_best = variances[best_idx]

        return bool((best_angle >= threshold_angle) and (var_best > var_0 * 1.05))
    except Exception:
        return False


def create_ocr_engine(ocr_engine_type: OCREngineType, args) -> OCREngine:
    from engine import OCREngineType
    line_break_mode = getattr(args, "line_break_mode", "y_cluster")
    lang = getattr(args, "lang", "vi")
    detect_italic = getattr(args, "detect_italic", False)

    if ocr_engine_type == OCREngineType.GGLENS:
        from gglens import GoogleLens
        return GoogleLens(
            threads=args.gglens_thread,
            line_break_mode=line_break_mode,
            lang=lang,
            detect_italic=detect_italic,
        )

    elif ocr_engine_type == OCREngineType.BING:
        from bing import BingOCR
        threads = getattr(args, "bing_thread", 32)
        return BingOCR(
            threads=threads,
            line_break_mode=line_break_mode,
            lang=lang,
            detect_italic=detect_italic,
        )

    else:
        raise ValueError(f"Unknown OCR engine: {ocr_engine_type}")