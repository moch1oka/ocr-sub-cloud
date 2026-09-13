import base64
import concurrent.futures
import io
import json
import threading
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from curl_cffi import requests
from PIL import Image
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from engine import OCREngine
from progress import ImageSecondSpeedColumn
from utils import collect_images, timecode_key

_local_session = threading.local()


def _get_bing_session() -> requests.Session:
    if not hasattr(_local_session, "session"):
        _local_session.session = requests.Session(impersonate="chrome")
    return _local_session.session


class BingOCR(OCREngine):
    UPLOAD_URL: str = "https://www.bing.com/images/search?view=detailv2&iss=sbiupload&setlang=vi&mkt=vi-VN"

    def __init__(self, threads: int = 16, line_break_mode: str = "y_cluster"):
        self.threads = threads
        self.line_break_mode = line_break_mode
        self.scan_lock = Lock()
        self.console = Console()

    @property
    def engine_name(self) -> str:
        return "Bing OCR"

    def __call__(self, images_dir: Path) -> Dict[str, str]:
        """Process all images in a directory with multithreading."""
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

    def _prepare_image_b64(self, img_path: str) -> str:
        # Normalize via Pillow using PNG (lossless) to preserve sharp Vietnamese accents (e.g. ư, ơ, dấu hỏi/ngã)
        with Image.open(img_path) as img:
            # Resize if dimensions are extreme
            min_dim = 50
            max_dim = 3000
            w, h = img.size
            if w < min_dim or h < min_dim:
                scale = max(min_dim / w, min_dim / h)
                img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
            elif w > max_dim or h > max_dim:
                scale = min(max_dim / w, max_dim / h)
                img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

            # Try fast lossless PNG first
            buf = io.BytesIO()
            img.save(buf, format="PNG", compress_level=1)
            raw_bytes = buf.getvalue()

            # If PNG exceeds Bing's web limit (~750KB), compress with high-quality JPEG
            if len(raw_bytes) > 750000:
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=92, optimize=True)
                raw_bytes = buf.getvalue()

            return base64.b64encode(raw_bytes).decode("utf-8")


    def process_image(self, img_path: str) -> str:
        try:
            img_b64 = self._prepare_image_b64(img_path)
        except Exception as e:
            print(f"Error preparing image '{img_path}': {e}")
            return ""

        session = _get_bing_session()
        max_retries = 3
        token = None
        api_host = "www.bing.com"

        # Step 1: Upload image to obtain insightsToken
        for attempt in range(max_retries):
            try:
                mp = requests.CurlMime()
                mp.addpart(name="imgurl", data="")
                mp.addpart(name="cbir", data="sbi")
                mp.addpart(name="imageBin", data=img_b64)

                r1 = session.post(
                    self.UPLOAD_URL,
                    multipart=mp,
                    allow_redirects=False,
                    timeout=20,
                )

                if r1.status_code == 302:
                    loc = r1.headers.get("Location", "")
                    parsed = urlparse(loc)
                    if parsed.netloc:
                        api_host = parsed.netloc
                    token_list = parse_qs(parsed.query).get("insightsToken")
                    if token_list and token_list[0]:
                        token = token_list[0]
                        break

                if attempt == max_retries - 1:
                    print(f"Failed to get token for {img_path}, status: {r1.status_code}")
                    return ""

                time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Upload error for {img_path}: {e}")
                    return ""
                time.sleep(0.5 * (attempt + 1))

        if not token:
            return ""

        # Step 2: Request OCR knowledge action
        api_url = f"https://{api_host}/images/api/custom/knowledge?setlang=vi&mkt=vi-VN"
        headers = {
            "origin": "https://www.bing.com",
            "referer": f"https://www.bing.com/images/search?view=detailV2&insightstoken={token}",
            "accept-language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        req_data = {
            "imageInfo": {"imageInsightsToken": token, "source": "Url"},
            "knowledgeRequest": {"invokedSkills": ["OCR"], "index": 1},
        }

        data = None
        for attempt in range(max_retries):
            try:
                mp2 = requests.CurlMime()
                mp2.addpart(
                    name="knowledgeRequest",
                    content_type="application/json",
                    data=json.dumps(req_data),
                )

                r2 = session.post(
                    api_url,
                    multipart=mp2,
                    headers=headers,
                    timeout=20,
                )

                if r2.status_code == 200:
                    data = r2.json()
                    break

                if attempt == max_retries - 1:
                    print(f"Failed knowledge request for {img_path}, status: {r2.status_code}")
                    return ""

                time.sleep(0.5 * (attempt + 1))
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Knowledge API error for {img_path}: {e}")
                    return ""
                time.sleep(0.5 * (attempt + 1))

        if not data:
            return ""

        # Step 3: Parse text and bounding boxes from OCR action
        regions = []
        for tag in data.get("tags", []):
            if tag.get("displayName") == "##TextRecognition":
                for action in tag.get("actions", []):
                    if action.get("_type") == "ImageKnowledge/TextRecognitionAction":
                        regions = action.get("data", {}).get("regions", [])
                        break
                break

        if not regions:
            return ""

        if self.line_break_mode == "line":
            lines_text = []
            for r in regions:
                for l in r.get("lines", []):
                    txt = l.get("text", "").strip()
                    if txt:
                        lines_text.append(txt)
            return "\n".join(lines_text)

        elif self.line_break_mode == "paragraph":
            para_texts = []
            for r in regions:
                para_lines = [l.get("text", "").strip() for l in r.get("lines", []) if l.get("text", "").strip()]
                if para_lines:
                    para_texts.append(" ".join(para_lines))
            return "\n".join(para_texts)

        else:  # default: "y_cluster"
            all_lines = []
            for r in regions:
                for l in r.get("lines", []):
                    txt = l.get("text", "").strip()
                    if not txt:
                        continue

                    quad = l.get("boundingBox")
                    if quad and all(k in quad for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")):
                        cy = sum(quad[k]["y"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")) / 4.0
                        cx = sum(quad[k]["x"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")) / 4.0
                        h = max(quad["bottomRight"]["y"], quad["bottomLeft"]["y"]) - min(quad["topLeft"]["y"], quad["topRight"]["y"])
                    else:
                        cy = 0.5
                        cx = 0.5
                        h = 0.1

                    all_lines.append({"text": txt, "cy": cy, "cx": cx, "h": h})

            if not all_lines:
                return ""

            # Sort all lines from top to bottom
            all_lines.sort(key=lambda item: item["cy"])

            # Cluster lines that share approximately the same visual row
            clusters: list[list[dict]] = []
            for item in all_lines:
                matched = False
                for cluster in clusters:
                    avg_cy = sum(x["cy"] for x in cluster) / len(cluster)
                    avg_h = sum(x["h"] for x in cluster) / len(cluster)
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
                row_strings.append(" ".join(x["text"] for x in cluster))

            return "\n".join(row_strings)
