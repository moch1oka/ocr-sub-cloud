import base64
import io
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

from curl_cffi import CurlHttpVersion, CurlMime, requests
from PIL import Image

from engine import OCREngine, cluster_lines_by_y

_local_session = threading.local()


def _get_bing_session() -> requests.Session:
    if not hasattr(_local_session, "session"):
        _local_session.session = requests.Session(
            impersonate="chrome",
            http_version=CurlHttpVersion.V1_1,
        )
    return _local_session.session


class BingOCR(OCREngine):
    def __init__(
        self,
        threads: int = 16,
        line_break_mode: str = "y_cluster",
        lang: str = "vi",
        detect_italic: bool = False,
    ):
        super().__init__(threads=threads, line_break_mode=line_break_mode, lang=lang, detect_italic=detect_italic)
        self.upload_url = (
            f"https://www.bing.com/images/search?view=detailv2&iss=sbiupload"
            f"&setlang={self.lang_cfg.bing_setlang}&mkt={self.lang_cfg.bing_mkt}"
        )

    @property
    def engine_name(self) -> str:
        return "Bing OCR"

    def _prepare_image_data(self, img_path: str) -> tuple[str, bytes]:
        min_dim = 50
        max_dim = 3000
        try:
            with Image.open(img_path) as img:
                w, h = img.size
                fmt = (img.format or "").upper()

                # Fast path: if already a valid PNG/JPEG <= 750KB with good dimensions, read raw bytes directly
                if min_dim <= w <= max_dim and min_dim <= h <= max_dim and fmt in ("PNG", "JPEG"):
                    with open(img_path, "rb") as f:
                        raw_bytes = f.read()
                    if len(raw_bytes) <= 750000:
                        return base64.b64encode(raw_bytes).decode("ascii"), raw_bytes

                # Otherwise resize or re-encode
                if w < min_dim or h < min_dim:
                    scale = max(min_dim / w, min_dim / h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                elif w > max_dim or h > max_dim:
                    scale = min(max_dim / w, max_dim / h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

                buf = io.BytesIO()
                img.save(buf, format="PNG", compress_level=1)
                raw_bytes = buf.getvalue()

                if len(raw_bytes) > 750000:
                    if img.mode in ("RGBA", "LA", "P"):
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=92, optimize=True)
                    raw_bytes = buf.getvalue()

                return base64.b64encode(raw_bytes).decode("ascii"), raw_bytes
        except Exception:
            with open(img_path, "rb") as f:
                raw_bytes = f.read()
            return base64.b64encode(raw_bytes).decode("ascii"), raw_bytes

    def process_image(self, img_path: str) -> str:
        try:
            img_b64, raw_bytes = self._prepare_image_data(img_path)
        except Exception as e:
            self.console.log(f"[red]Error preparing image '{img_path}': {e}[/red]")
            return ""

        session = _get_bing_session()
        max_retries = 3
        token = None
        api_host = "www.bing.com"

        # Step 1: Upload image to obtain insightsToken
        for attempt in range(max_retries):
            try:
                mp = CurlMime()
                mp.addpart(name="imgurl", data="")
                mp.addpart(name="cbir", data="sbi")
                mp.addpart(name="imageBin", data=img_b64)

                r1 = session.post(
                    self.upload_url,
                    multipart=mp,
                    allow_redirects=False,
                    timeout=20,
                )

                if r1.status_code == 302:
                    loc = r1.headers.get("Location", "")
                    if "insightsToken=" in loc:
                        token = loc.split("insightsToken=")[1].split("&")[0]
                        parsed = urlparse(loc)
                        if parsed.netloc:
                            api_host = parsed.netloc
                        break

                if attempt == max_retries - 1:
                    self.console.log(f"[yellow]Failed to get token for {img_path}, status: {r1.status_code}[/yellow]")
                    return ""

                delay1 = 1.0 * (attempt + 1) if r1.status_code == 429 else 0.5 * (attempt + 1)
                time.sleep(delay1)
            except Exception as e:
                if attempt == max_retries - 1:
                    self.console.log(f"[yellow]Upload error for {img_path}: {e}[/yellow]")
                    return ""
                time.sleep(0.5 * (attempt + 1))

        if not token:
            return ""

        # Step 2: Request OCR knowledge action
        api_url = (
            f"https://{api_host}/images/api/custom/knowledge"
            f"?setlang={self.lang_cfg.bing_setlang}&mkt={self.lang_cfg.bing_mkt}"
        )
        headers = {
            "origin": "https://www.bing.com",
            "referer": f"https://www.bing.com/images/search?view=detailV2&insightstoken={token}",
            "accept-language": self.lang_cfg.bing_accept_lang,
        }
        req_data = {
            "imageInfo": {"imageInsightsToken": token, "source": "Url"},
            "knowledgeRequest": {"invokedSkills": ["OCR"], "index": 1},
        }

        data = None
        for attempt in range(max_retries):
            try:
                mp2 = CurlMime()
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
                    self.console.log(f"[yellow]Failed knowledge request for {img_path}, status: {r2.status_code}[/yellow]")
                    return ""

                delay2 = 1.0 * (attempt + 1) if r2.status_code == 429 else 0.5 * (attempt + 1)
                time.sleep(delay2)
            except Exception as e:
                if attempt == max_retries - 1:
                    self.console.log(f"[yellow]Knowledge API error for {img_path}: {e}[/yellow]")
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

        img_gray = None
        if self.detect_italic:
            try:
                import cv2
                import numpy as np

                img_gray = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
            except Exception:
                img_gray = None

        def _is_line_italic(quad) -> bool:
            if img_gray is None or not quad:
                return False
            try:
                from utils import is_italic_patch

                if not all(k in quad for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")):
                    return False
                xs = [quad[k]["x"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")]
                ys = [quad[k]["y"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")]
                h_img, w_img = img_gray.shape[:2]
                if max(xs) <= 1.0 and max(ys) <= 1.0:
                    min_x, max_x = min(xs) * w_img, max(xs) * w_img
                    min_y, max_y = min(ys) * h_img, max(ys) * h_img
                else:
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)

                pad_x = (max_x - min_x) * 0.05
                pad_y = (max_y - min_y) * 0.05
                x1 = int(max(0, min_x - pad_x))
                x2 = int(min(w_img, max_x + pad_x))
                y1 = int(max(0, min_y - pad_y))
                y2 = int(min(h_img, max_y + pad_y))

                if x2 - x1 < 10 or y2 - y1 < 8:
                    return False

                return is_italic_patch(img_gray[y1:y2, x1:x2])
            except Exception:
                return False

        if self.line_break_mode == "line":
            lines_text = []
            for r in regions:
                for l in r.get("lines", []):
                    txt = l.get("text", "").strip()
                    if txt:
                        if self.detect_italic and _is_line_italic(l.get("boundingBox")):
                            txt = f"{{\\i1}}{txt}{{\\i0}}"
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

                    is_ita = _is_line_italic(quad) if self.detect_italic else False
                    all_lines.append({"text": txt, "cy": cy, "cx": cx, "h": h, "is_italic": is_ita})

            return cluster_lines_by_y(all_lines)
