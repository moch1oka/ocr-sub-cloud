from abc import ABC, abstractmethod
import concurrent.futures
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Dict, List

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from languages import LanguageConfig, get_language_config
from progress import ImageSecondSpeedColumn
from utils import collect_images, timecode_key


def cluster_lines_by_y(lines: List[dict]) -> str:
    """
    Clusters a list of text lines (each having 'text', 'cy', 'cx', 'h')
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
        row_strings.append(" ".join(l["text"] for l in cluster))

    return "\n".join(row_strings)


class OCREngine(ABC):
    """Abstract base class for OCR engines implementing the Template Method pattern."""

    def __init__(
        self,
        threads: int = 16,
        line_break_mode: str = "y_cluster",
        lang: str = "vi",
    ):
        self.threads = threads
        self.line_break_mode = line_break_mode
        self.lang = lang
        self.lang_cfg: LanguageConfig = get_language_config(lang)
        self.scan_lock = Lock()
        self.console = Console()

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """User-friendly name of the OCR engine."""
        pass

    @abstractmethod
    def process_image(self, img_path: str) -> str:
        """Processes a single image file and returns the recognized text."""
        pass

    def __call__(self, images_dir: Path) -> Dict[str, str]:
        """
        Processes all images in images_dir using multithreading with a Rich progress bar.
        Returns a sorted dictionary mapping image filename to recognized text.
        """
        images = collect_images(images_dir)

        if not images:
            self.console.print(f"[yellow]No images found in {images_dir}[/yellow]")
            return {}

        results: Dict[str, str] = {}

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

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.threads) as executor:
                future_to_image = {
                    executor.submit(self.process_image, str(img_path)): img_path.name
                    for img_path in images
                }

                for future in concurrent.futures.as_completed(future_to_image):
                    img_name = future_to_image[future]
                    try:
                        text = future.result()
                        with self.scan_lock:
                            results[img_name] = text if text is not None else ""
                    except Exception as exc:
                        self.console.print(f"[red]{img_name} generated an exception: {exc}[/red]")
                        with self.scan_lock:
                            results[img_name] = ""

                    progress.update(task, advance=1)

        return dict(sorted(results.items(), key=timecode_key))


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