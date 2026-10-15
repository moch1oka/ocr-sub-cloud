from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
import concurrent.futures
from enum import Enum
from pathlib import Path
from threading import Lock
import threading
import time
from typing import Any, Dict, List

from curl_cffi import CurlHttpVersion, requests
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from languages import LanguageConfig, get_language_config
from progress import ImageSecondSpeedColumn
from utils import OcrCache, collect_images, timecode_key


# ==========================================
# 1. COMPUTER VISION & ITALIC DETECTION
# ==========================================

def decode_grayscale(raw_bytes: bytes):
    """Decodes raw image bytes into a grayscale numpy array for computer vision processing."""
    if not raw_bytes:
        return None
    try:
        import cv2
        import numpy as np

        return cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None


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


def check_italic_patch(img_gray, x1: int, y1: int, x2: int, y2: int) -> bool:
    """Checks if a bounding box crop inside a grayscale image contains italic text."""
    if img_gray is None:
        return False
    try:
        h_img, w_img = img_gray.shape[:2]
        pad_x = (x2 - x1) * 0.05
        pad_y = (y2 - y1) * 0.05
        px1 = int(max(0, x1 - pad_x))
        px2 = int(min(w_img, x2 + pad_x))
        py1 = int(max(0, y1 - pad_y))
        py2 = int(min(h_img, y2 + pad_y))

        if px2 - px1 < 10 or py2 - py1 < 8:
            return False

        return is_italic_patch(img_gray[py1:py2, px1:px2])
    except Exception:
        return False


# ==========================================
# 2. SPATIAL Y-COORDINATE LINE CLUSTERING
# ==========================================

def format_cluster_row(cluster: list[dict]) -> str:
    """Formats a visual row, wrapping italic text in {\\i1}...{\\i0} tags if present."""
    if not cluster:
        return ""

    has_italic = any(l.get("is_italic") for l in cluster)
    if not has_italic:
        return " ".join(l["text"] for l in cluster)

    # If every item in the row is italic, wrap the entire row
    if all(l.get("is_italic") for l in cluster):
        return f"{{\\i1}}{' '.join(l['text'] for l in cluster)}{{\\i0}}"

    # Mixed: group adjacent items with the same italic state
    parts = []
    curr_italic = None
    curr_words = []
    for l in cluster:
        is_ita = bool(l.get("is_italic"))
        if is_ita == curr_italic:
            curr_words.append(l["text"])
        else:
            if curr_words:
                text_chunk = " ".join(curr_words)
                parts.append(f"{{\\i1}}{text_chunk}{{\\i0}}" if curr_italic else text_chunk)
            curr_italic = is_ita
            curr_words = [l["text"]]
    if curr_words:
        text_chunk = " ".join(curr_words)
        parts.append(f"{{\\i1}}{text_chunk}{{\\i0}}" if curr_italic else text_chunk)

    return " ".join(parts)


def cluster_lines_by_y(lines: List[dict]) -> str:
    """
    Clusters a list of text lines (each having 'text', 'cy', 'cx', 'h', and optional 'is_italic')
    by their normalized vertical center Y coordinate into visual rows.
    Rows are sorted top-to-bottom, and words within each row are sorted left-to-right.
    """
    if not lines:
        return ""

    # Sort all lines from top to bottom
    lines.sort(key=lambda item: item["cy"])

    # Cluster lines that share the same visual row
    clusters: list[list[dict]] = []
    for item in lines:
        matched = False
        for cluster in clusters:
            avg_cy = sum(l["cy"] for l in cluster) / len(cluster)
            avg_h = sum(l["h"] for l in cluster) / len(cluster)
            threshold = max(avg_h * 0.5, 0.06)
            if abs(item["cy"] - avg_cy) <= threshold:
                cluster.append(item)
                matched = True
                break
        if not matched:
            clusters.append([item])

    # Within each cluster (row), sort left to right and join with space
    row_strings = []
    for cluster in clusters:
        cluster.sort(key=lambda item: item["cx"])
        row_strings.append(format_cluster_row(cluster))

    return "\n".join(row_strings)


# ==========================================
# 3. BASE OCR ENGINE CLASS
# ==========================================

_local_session = threading.local()


class OCREngine(ABC):
    """Abstract base class for OCR engines implementing the Template Method pattern."""

    def __init__(
        self,
        threads: int = 32,
        line_break_mode: str = "y_cluster",
        lang: str = "vi",
        detect_italic: bool = False,
    ):
        self.threads = threads
        self.line_break_mode = line_break_mode
        self.lang = lang
        self.detect_italic = detect_italic
        self.lang_cfg: LanguageConfig = get_language_config(lang)
        self.scan_lock = Lock()
        self.console = Console()

    def get_session(self) -> requests.Session:
        """Returns a thread-local curl_cffi session impersonating Chrome with HTTP/1.1."""
        if not hasattr(_local_session, "session"):
            _local_session.session = requests.Session(
                impersonate="chrome",
                http_version=CurlHttpVersion.V1_1,
            )
        return _local_session.session

    def post_with_retry(
        self,
        url: str,
        session: requests.Session | None = None,
        max_retries: int = 3,
        success_codes: tuple[int, ...] = (200,),
        error_context: str = "",
        **kwargs,
    ) -> requests.Response | None:
        """Executes a POST request with exponential backoff on errors and HTTP 429 rate limits."""
        sess = session or self.get_session()
        last_exception = None
        for attempt in range(max_retries):
            try:
                res = sess.post(url, **kwargs)
                if res.status_code in success_codes:
                    return res

                last_exception = Exception(f"Request failed with status code: {res.status_code}")
                # Client errors (except timeout / rate limit) will not succeed on retry
                retryable = res.status_code >= 500 or res.status_code in (408, 429) or res.status_code < 400
                if attempt == max_retries - 1 or not retryable:
                    ctx = f" for {error_context}" if error_context else ""
                    self.console.log(f"[yellow]Failed request{ctx} after {attempt + 1} attempt(s): {last_exception}[/yellow]")
                    return None

                delay = 1.0 * (attempt + 1) if res.status_code == 429 else 0.5 * (attempt + 1)
                time.sleep(delay)
            except Exception as e:
                last_exception = e
                if attempt == max_retries - 1:
                    ctx = f" for {error_context}" if error_context else ""
                    self.console.log(f"[yellow]Request error{ctx} after {max_retries} attempts: {last_exception}[/yellow]")
                    return None
                time.sleep(0.5 * (attempt + 1))
        return None

    def decode_grayscale(self, raw_bytes: bytes):
        """Decodes raw image bytes into a grayscale numpy array if italic detection is active."""
        if not self.detect_italic or not raw_bytes:
            return None
        return decode_grayscale(raw_bytes)

    def format_recognized_text(
        self,
        lines: list[dict],
        paragraphs: list[list[dict]] | None = None,
    ) -> str:
        """
        Formats recognized lines according to self.line_break_mode:
        - 'line': one line per recognized line (wrapped in {\\i1} if italic)
        - 'paragraph': one output line per paragraph (each paragraph is a list of line dicts)
        - 'y_cluster': clustered into visual rows based on normalized Y coordinates
        """
        if not lines:
            return ""

        if self.line_break_mode == "line":
            formatted = []
            for l in lines:
                txt = l.get("text", "").strip()
                if txt:
                    if l.get("is_italic"):
                        txt = f"{{\\i1}}{txt}{{\\i0}}"
                    formatted.append(txt)
            return "\n".join(formatted)

        elif self.line_break_mode == "paragraph" and paragraphs:
            rows = (format_cluster_row([l for l in p if l.get("text", "").strip()]) for p in paragraphs)
            return "\n".join(r for r in rows if r.strip())

        else:  # default: "y_cluster"
            return cluster_lines_by_y(lines)

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """User-friendly name of the OCR engine."""
        pass

    @abstractmethod
    def process_image(self, img_path: str) -> str:
        """Processes a single image file and returns the recognized text."""
        pass

    @property
    def cache_signature(self) -> str:
        """Settings that change OCR output; cached results are only reused when these match."""
        return f"{self.engine_name}|{self.lang}|{self.line_break_mode}|italic={int(self.detect_italic)}"

    def __call__(self, images_dir: Path, cache_path: Path | None = None) -> Dict[str, str]:
        """
        Processes all images in images_dir using multithreading with a Rich progress bar.
        With cache_path, results of earlier runs are reused and new ones are stored there.
        Returns a sorted dictionary mapping image filename to recognized text.
        """
        images = collect_images(images_dir)

        if not images:
            self.console.print(f"[yellow]No images found in {images_dir}[/yellow]")
            return {}

        results: Dict[str, str] = {}
        cache = OcrCache(cache_path, self.cache_signature) if cache_path else None
        keys: Dict[str, str] = {}
        todo = images
        if cache:
            todo = []
            for img_path in images:
                keys[img_path.name] = OcrCache.image_key(img_path)
                cached = cache.get(keys[img_path.name])
                if cached is not None:
                    results[img_path.name] = cached
                else:
                    todo.append(img_path)
            if results:
                self.console.print(f"Reusing {len(results)} cached OCR results, {len(todo)} images left")

        try:
            if todo:
                self._run_images(todo, results, cache, keys)
        finally:
            if cache:
                cache.save()

        return dict(sorted(results.items(), key=timecode_key))

    def _run_images(
        self,
        images: List[Path],
        results: Dict[str, str],
        cache: OcrCache | None,
        keys: Dict[str, str],
    ) -> None:
        with Progress(
            TextColumn(f"[progress.description]{{task.description}} ({self.engine_name})"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TextColumn("{task.percentage:>3.0f}%"),
            ImageSecondSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task("Processing images", total=len(images))

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.threads)
            try:
                future_to_image = {
                    executor.submit(self.process_image, str(img_path)): img_path.name
                    for img_path in images
                }

                for future in concurrent.futures.as_completed(future_to_image):
                    img_name = future_to_image[future]
                    try:
                        text = future.result() or ""
                    except Exception as exc:
                        self.console.print(f"[red]{img_name} generated an exception: {exc}[/red]")
                        text = ""
                    with self.scan_lock:
                        results[img_name] = text
                    if cache:
                        cache.put(keys[img_name], text)

                    progress.update(task, advance=1)
            finally:
                # On Ctrl+C don't wait for every queued image; cached results are saved by the caller
                executor.shutdown(wait=True, cancel_futures=True)


# ==========================================
# 4. ENGINE ENUMS & FACTORY PATTERN
# ==========================================

class OCREngineType(Enum):
    GGLENS = "gglens"
    BING = "bing"

    @classmethod
    def from_string(cls, value: str) -> "OCREngineType":
        clean_value = value.lower().replace("_", "").replace("-", "")
        for engine in cls:
            if engine.value.lower().replace("_", "").replace("-", "") == clean_value:
                return engine
        raise ValueError(f"Unknown OCR engine: {value}. Available: {[e.value for e in cls]}")


def ocr_engine_type(value: str) -> OCREngineType:
    """ArgumentParser type converter for OCREngineType."""
    try:
        return OCREngineType.from_string(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def create_ocr_engine(ocr_engine_type: OCREngineType, args: Any) -> OCREngine:
    """Factory function to instantiate the requested OCR engine with CLI parameters."""
    line_break_mode = getattr(args, "line_break_mode", "y_cluster")
    lang = getattr(args, "lang", "vi")
    detect_italic = getattr(args, "detect_italic", False)

    if ocr_engine_type == OCREngineType.GGLENS:
        from gglens import GoogleLens

        threads = getattr(args, "gglens_thread", 32)
        return GoogleLens(
            threads=threads,
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