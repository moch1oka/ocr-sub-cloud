from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import threading
from pathlib import Path
from typing import Callable, List

from PIL import Image, ImageOps, UnidentifiedImageError

from languages import get_language_config


# ==========================================
# 1. TEXT CLEANUP & PUNCTUATION FORMATTING
# ==========================================

DOUBLE_QUOTE_REGEX = re.compile(
    "|".join(["«", "‹", "»", "›", "„", "“", "‟", "”", "❝", "❞", "❮", "❯", "〝", "〞", "〟", "＂"])
)

SINGLE_QUOTE_REGEX = re.compile("|".join(["‘", "‛", "’", "❛", "❜", "`", "´"]))

CJK_CHAR_REGEX = r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"


def _space_after_sentence_dot(m: re.Match) -> str:
    next_char = m.string[m.end()]
    return ". " if next_char.isupper() else "."


def apply_punctuation_and_spacing(text: str) -> str:
    """Collapses multiple spaces and normalizes spacing around punctuation marks."""
    # Collapse multiple horizontal spaces
    text = re.sub(r"[ \t]{2,}", " ", text)

    # Remove extra spaces before punctuation (except dot followed by digit, e.g. .05)
    text = re.sub(r"\s+([,!?:;…]|\.(?!\d))", r"\1", text)

    # Ensure single space after punctuation, except for multiple punctuation marks, numbers/decimals, and quotes/brackets/braces
    text = re.sub(r"([,!?…;])(?!\s)(?![,.!?…;])(?!\d)(?![)\"\'’”›»\]\}\{])", r"\1 ", text)

    # A dot only gets a space when it ends a word and the next word is capitalized ("Hello.World"),
    # so URLs, abbreviations and initials (www.google.com, e.g., U.S.A) stay intact
    text = re.sub(r"(?<=[^\W\d_]{2})\.(?=[^\W\d_])", _space_after_sentence_dot, text)

    # Remove space between multiple punctuation marks
    text = re.sub(r"([,.!?…])\s+([,.!?…])", r"\1\2", text)

    # Clean whitespace around formatting tags
    text = re.sub(r"(\{\\i1\}|<i\s*>)\s+", r"\1", text)
    text = re.sub(r"\s+(\{\\i0\}|</i\s*>)", r"\1", text)

    return text.strip()


def fix_quotes(text: str) -> str:
    """Normalizes various Unicode quotes into standard ASCII quotes."""
    text = SINGLE_QUOTE_REGEX.sub("'", text)
    text = DOUBLE_QUOTE_REGEX.sub('"', text)
    return text


def text_cleanup(text: str, lang: str = "vi") -> str:
    """
    Cleans up raw OCR output according to the selected language preset.
    Preserves newlines as '\\N', normalizes quotes, applies language-specific whitelisting,
    and strips unwanted spaces for CJK scripts.
    """
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

        # Ellipses come back as '･･･', '...' or a mix (Lens sometimes returns '･･･ ...' for one '…')
        text = re.sub(r"[･.]{2,}…?", "…", text)
        text = re.sub(r"…(?:\s+…)+", "…", text)
        text = text.replace("･", "・")

        # Remove OCR-generated spaces between CJK characters and around CJK punctuation
        if cfg.strip_cjk_spaces:
            # Space before ASCII !/? or after a closing bracket, next to CJK text ("はは !", "(頭目) 待て")
            text = re.sub(rf"({CJK_CHAR_REGEX})\s+([!?])", r"\1\2", text)
            text = re.sub(rf"([)\]])\s+({CJK_CHAR_REGEX})", r"\1\2", text)
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
            r"!\[\]_\$ /\\:\'\" ,.\?;\-+=()*%@~…{}&#<>♪♫"
            r"]"
        )
        text = re.sub(pattern, "", text)

    else:
        # Latin / Vietnamese mode
        text = fix_quotes(text)

        # Preserve apostrophes/single quotes, semicolons, ellipses, braces, and common punctuation
        allowed_special_chars = r' ![]_{}$ /\\:\'" ,.?;\-+=()*%@~…&#<>♪♫'
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


# ==========================================
# 2. IMAGE PREPROCESSING & RESIZING
# ==========================================

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
                scale = 1.0
            # Never upscale past the max limits (very wide, very short strips)
            scale = min(scale, max_dim / w, max_dim / h, math.sqrt(max_pixels / (w * h)))

            if scale != 1.0:
                new_w = min(max_dim, max(1, int(round(w * scale))))
                new_h = min(max_dim, max(1, int(round(h * scale))))
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                w, h = img.size

            # Pad (instead of stretching) whatever is still below min_dim
            if w < min_dim or h < min_dim:
                pad_w, pad_h = max(0, min_dim - w), max(0, min_dim - h)
                border = (pad_w // 2, pad_h // 2, pad_w - pad_w // 2, pad_h - pad_h // 2)
                img = ImageOps.expand(img, border=border, fill=img.getpixel((0, 0)))
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


# ==========================================
# 3. TIMECODE & FILENAME PARSING
# ==========================================

def _timecode_to_ms(raw: str) -> int:
    """Converts a 'H_MM_SS_frac[_...]' timecode into milliseconds (frac: 2 digits = centiseconds)."""
    parts = raw.split("_")
    h, m, s, frac = int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
    if not frac.isdigit():
        raise ValueError(f"Invalid timecode fraction: {frac!r}")
    ms = int(frac) * 10 if len(frac) == 2 else int(frac.ljust(3, "0")[:3])
    return h * 3600000 + m * 60000 + s * 1000 + ms


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
        timecode_part = name_without_ext[4:]
    else:
        timecode_part = name_without_ext

    start_raw, end_raw = timecode_part.split("__", 1)
    return _timecode_to_ms(start_raw), _timecode_to_ms(end_raw), is_top


def timecode_key(item) -> tuple[int, str]:
    """Sorting key for images based on starting timecode extracted from filename."""
    filename = Path(str(item[0] if isinstance(item, tuple) else item)).name
    try:
        start_ms, _, _ = parse_filename_timecodes(filename)
        return (start_ms, filename)
    except (IndexError, ValueError):
        return (2**63, filename)


# ==========================================
# 4. SYSTEM & CLI UTILITIES
# ==========================================

def get_in_path(name: str) -> str | None:
    """Finds executable in system PATH or Scoop shims."""
    search_name = name
    if platform.system() == "Windows":
        search_name = name + ".exe"

    try:
        path = shutil.which(search_name)
        # Scoop support on Windows
        if platform.system() == "Windows":
            shim_path = shutil.which(f"{name}.shim")
            if shim_path:
                scoop_app_path = (
                    Path(shim_path).parents[1]
                    / "apps"
                    / "videosubfinder"
                    / "current"
                    / "VideoSubFinderWXW.exe"
                )
                if scoop_app_path.is_file():
                    path = str(scoop_app_path)
        if path and os.access(path, os.X_OK):
            return path
        return None
    except Exception:
        return None


class OcrCache:
    """
    OCR results stored on disk by image content hash, so an interrupted or repeated run doesn't send the
    same images again. Results are kept per settings signature (engine, language, line mode...), and only
    non-empty results are stored, so failed or empty images are retried next time.
    """

    def __init__(self, path: Path | str, signature: str):
        self.path = Path(path)
        self.signature = signature
        self._lock = threading.Lock()
        self._dirty = 0
        try:
            self._data: dict[str, dict[str, str]] = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self._data = {}
        self._entries = self._data.setdefault(signature, {})

    @staticmethod
    def image_key(image_path: Path | str) -> str:
        return hashlib.sha1(Path(image_path).read_bytes()).hexdigest()

    def get(self, key: str) -> str | None:
        return self._entries.get(key)

    def put(self, key: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._entries[key] = text
            self._dirty += 1
            if self._dirty >= 50:
                self._save_locked()

    def save(self) -> None:
        with self._lock:
            if self._dirty:
                self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = 0


def collect_images(images_dir: Path | str) -> List[Path]:
    """Collects and returns all supported image files from a directory."""
    images_dir = Path(images_dir)
    if not images_dir.is_dir():
        return []
    valid_suffixes = {".jpeg", ".jpg", ".png", ".bmp", ".gif", ".webp"}
    images = [
        p for p in images_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in valid_suffixes
    ]
    return sorted(images)


def parse_time_ms(value: str) -> int:
    """
    Parses a user-supplied time into milliseconds. Accepts seconds ('90', '90.5'),
    'M:SS', 'H:MM:SS', and a millisecond part after '.', ',' or a 4th ':' ('0:01:30:500', '0:01:30.500').
    """
    parts = value.strip().replace(",", ".").split(":")
    millis = 0
    if len(parts) == 4:  # H:MM:SS:mmm (VideoSubFinder format)
        if not re.fullmatch(r"\d{1,3}", parts[3]):
            raise ValueError(f"invalid time {value!r}")
        millis = int(parts[3])
        parts = parts[:3]
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"invalid time {value!r}")

    *whole, seconds = parts
    if not all(p.isdigit() for p in whole) or not re.fullmatch(r"\d+(?:\.\d+)?", seconds):
        raise ValueError(f"invalid time {value!r}")
    if millis and "." in seconds:
        raise ValueError(f"invalid time {value!r}")

    total_s = float(seconds)
    for i, p in enumerate(reversed(whole), 1):
        total_s += int(p) * 60**i
    return round(total_s * 1000) + millis


def time_arg(value: str) -> int:
    """ArgumentParser type for times (see parse_time_ms)."""
    try:
        return parse_time_ms(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{exc}; use seconds, M:SS, H:MM:SS, H:MM:SS.mmm or H:MM:SS:mmm"
        ) from exc


def format_vsf_time(ms: int) -> str:
    """Formats milliseconds the way VideoSubFinder's --start_time / --end_time expect (H:MM:SS:mmm)."""
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h}:{m:02d}:{s:02d}:{ms:03d}"


def float_range(mini: float, maxi: float) -> Callable[..., float]:
    """Returns an ArgumentParser type function checking that mini <= value <= maxi."""
    def float_range_checker(arg):
        try:
            f = float(arg)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a floating point number") from exc

        if f < mini or f > maxi:
            raise argparse.ArgumentTypeError(f"must be in range [{mini} .. {maxi}]")
        return f

    return float_range_checker