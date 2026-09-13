import concurrent.futures
import random
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from curl_cffi import requests
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from engine import OCREngine
from languages import get_language_config
from lens import (AppliedFilter, LensOverlayFilterType, LensOverlayRoutingInfo, LensOverlayServerRequest,
                  LensOverlayServerResponse, Platform, Surface,)
from progress import ImageSecondSpeedColumn
from utils import collect_images, get_image_raw_bytes_and_dims, timecode_key


import threading

_local_session = threading.local()


def _get_lens_session() -> requests.Session:
    if not hasattr(_local_session, "session"):
        _local_session.session = requests.Session(impersonate="chrome")
    return _local_session.session


class GoogleLens(OCREngine):
    LENS_ENDPOINT: str = "https://lensfrontend-pa.googleapis.com/v1/crupload"

    HEADERS: dict[str, str] = {
        "Host": "lensfrontend-pa.googleapis.com",
        "Connection": "keep-alive",
        "Content-Type": "application/x-protobuf",
        "X-Goog-Api-Key": "AIzaSyDr2UxVnv_U85AbhhY8XSHSIavUW0DC-sY",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "empty",
    }

    def __init__(self, threads: int = 16, line_break_mode: str = "y_cluster", lang: str = "vi"):
        self.threads = threads
        self.line_break_mode = line_break_mode
        self.lang = lang
        self.lang_cfg = get_language_config(lang)
        self.scan_lock = Lock()
        self.console = Console()
    
    @property
    def engine_name(self) -> str:
        return "Google Lens"

    def __call__(self, images_dir: Path) -> Dict[str, str]:
        """Process all images in a directory with threading - batch functionality."""
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

    def process_image(self, img_path: str) -> str:
        image_data = get_image_raw_bytes_and_dims(img_path)
        if image_data is None:
            print(f"Error: Could not process image file '{img_path}'. Cannot populate image data in request.")
            return ""

        raw_bytes, width, height = image_data

        request = LensOverlayServerRequest()

        request.objects_request.request_context.request_id.uuid = random.randint(0, 2**64 - 1)
        request.objects_request.request_context.request_id.sequence_id = 0
        request.objects_request.request_context.request_id.image_sequence_id = 0
        request.objects_request.request_context.request_id.analytics_id = random.randbytes(n=16)
        request.objects_request.request_context.request_id.routing_info = LensOverlayRoutingInfo()

        request.objects_request.request_context.client_context.platform = Platform.WEB
        request.objects_request.request_context.client_context.surface = Surface.CHROMIUM

        request.objects_request.request_context.client_context.locale_context.language = self.lang_cfg.lens_lang
        request.objects_request.request_context.client_context.locale_context.region = self.lang_cfg.lens_region
        request.objects_request.request_context.client_context.locale_context.time_zone = ""  # not set by chromium

        request.objects_request.request_context.client_context.app_id = ""  # not set by chromium

        filter = AppliedFilter()
        filter.filter_type = LensOverlayFilterType.AUTO_FILTER
        request.objects_request.request_context.client_context.client_filters.filter.append(filter)

        request.objects_request.image_data.payload.image_bytes = raw_bytes
        request.objects_request.image_data.image_metadata.width = width
        request.objects_request.image_data.image_metadata.height = height

        payload = request.SerializeToString()

        session = _get_lens_session()
        res = None
        max_retries = 3
        last_exception = None
        for attempt in range(max_retries):
            try:
                res = session.post(
                    self.LENS_ENDPOINT,
                    data=payload,
                    headers=self.HEADERS,
                    timeout=30,
                )

                if res.status_code == 200:
                    break

                raise Exception(f"Request failed with status code: {res.status_code}")

            except Exception as e:
                last_exception = e
                print(f"Attempt {attempt + 1} failed. Retrying...")
                if attempt == max_retries - 1:
                    raise Exception(
                        f"Failed to upload image after {max_retries} attempts. Last error: {str(last_exception)}"
                    )
                continue

        if res is not None:
            response_proto = LensOverlayServerResponse().FromString(res.content)
            response_dict: dict[str, Any] = response_proto.to_dict()

            paragraphs = (
                response_dict.get("objectsResponse", {}).get("text", {}).get("textLayout", {}).get("paragraphs", [])
            )
            if not paragraphs:
                print(f"Empty OCR please check subtitle {img_path}")
                return ""

            if self.line_break_mode == "line":
                lines_text = []
                for p in paragraphs:
                    for l in p.get("lines", []):
                        line_str = "".join(
                            (w.get("plainText") or "") + (w.get("textSeparator") or "")
                            for w in l.get("words", [])
                        ).strip()
                        if line_str:
                            lines_text.append(line_str)
                return "\n".join(lines_text)

            elif self.line_break_mode == "paragraph":
                result = ""
                separator = "\n"
                for index, paragraph in enumerate(paragraphs):
                    if index > 0:
                        result += separator
                    for line in paragraph.get("lines", []):
                        for word in line.get("words", []):
                            plain_text = word.get("plainText") or ""
                            separator_text = word.get("textSeparator") or ""
                            result += plain_text + separator_text
                return result

            else:  # default: "y_cluster"
                all_lines = []
                for p in paragraphs:
                    for l in p.get("lines", []):
                        box = l.get("geometry", {}).get("boundingBox", {})
                        cy = box.get("centerY", 0.5)
                        cx = box.get("centerX", 0.5)
                        h = box.get("height", 0.1)
                        line_str = "".join(
                            (w.get("plainText") or "") + (w.get("textSeparator") or "")
                            for w in l.get("words", [])
                        ).strip()
                        if line_str:
                            all_lines.append({"text": line_str, "cy": cy, "cx": cx, "h": h})

                if not all_lines:
                    return ""

                # Sort all lines from top to bottom
                all_lines.sort(key=lambda item: item["cy"])

                # Cluster lines that share the same visual row
                clusters: list[list[dict]] = []
                for item in all_lines:
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

        return ""