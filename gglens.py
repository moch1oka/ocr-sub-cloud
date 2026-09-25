import random
import threading
import time
from typing import Any

from curl_cffi import requests

from engine import OCREngine, cluster_lines_by_y
from lens import (
    AUTO_FILTER,
    PLATFORM_WEB,
    SURFACE_CHROMIUM,
    LensOverlayServerRequest,
    LensOverlayServerResponse,
)
from utils import get_image_raw_bytes_and_dims

from curl_cffi.requests import CurlHttpVersion

_local_session = threading.local()


def _get_lens_session() -> requests.Session:
    if not hasattr(_local_session, "session"):
        _local_session.session = requests.Session(
            impersonate="chrome",
            http_version=CurlHttpVersion.V1_1,
        )
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

    def __init__(
        self,
        threads: int = 16,
        line_break_mode: str = "y_cluster",
        lang: str = "vi",
        detect_italic: bool = False,
    ):
        super().__init__(threads=threads, line_break_mode=line_break_mode, lang=lang, detect_italic=detect_italic)

    @property
    def engine_name(self) -> str:
        return "Google Lens"

    def process_image(self, img_path: str) -> str:
        image_data = get_image_raw_bytes_and_dims(img_path)
        if image_data is None:
            self.console.log(f"[red]Error: Could not process image file '{img_path}'.[/red]")
            return ""

        raw_bytes, width, height = image_data

        request = LensOverlayServerRequest()

        request.objects_request.request_context.request_id.uuid = random.randint(0, 2**64 - 1)
        request.objects_request.request_context.request_id.sequence_id = 0
        request.objects_request.request_context.request_id.image_sequence_id = 0
        request.objects_request.request_context.request_id.analytics_id = random.randbytes(16)
        request.objects_request.request_context.request_id.routing_info.SetInParent()

        request.objects_request.request_context.client_context.platform = PLATFORM_WEB
        request.objects_request.request_context.client_context.surface = SURFACE_CHROMIUM

        request.objects_request.request_context.client_context.locale_context.language = self.lang_cfg.lens_lang
        request.objects_request.request_context.client_context.locale_context.region = self.lang_cfg.lens_region
        request.objects_request.request_context.client_context.locale_context.time_zone = ""

        request.objects_request.request_context.client_context.app_id = ""

        filter_item = request.objects_request.request_context.client_context.client_filters.filter.add()
        filter_item.filter_type = AUTO_FILTER

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

                last_exception = Exception(f"Request failed with status code: {res.status_code}")
                if attempt == max_retries - 1:
                    self.console.log(f"[yellow]Failed to upload image {img_path} after {max_retries} attempts: {last_exception}[/yellow]")
                    return ""
                time.sleep(0.5 * (attempt + 1))

            except Exception as e:
                last_exception = e
                if attempt == max_retries - 1:
                    self.console.log(f"[yellow]Failed to upload image {img_path} after {max_retries} attempts: {last_exception}[/yellow]")
                    return ""
                time.sleep(0.5 * (attempt + 1))

        if res is None or res.status_code != 200:
            return ""

        response_proto = LensOverlayServerResponse()
        response_proto.ParseFromString(res.content)
        paragraphs = response_proto.objects_response.text.text_layout.paragraphs
        if not paragraphs:
            return ""

        img_gray = None
        if self.detect_italic:
            try:
                import cv2
                import numpy as np

                img_gray = cv2.imdecode(np.frombuffer(raw_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
            except Exception:
                img_gray = None

        def _is_line_italic(box) -> bool:
            if img_gray is None:
                return False
            try:
                from utils import is_italic_patch

                cx = box.center_x if box.center_x is not None else 0.5
                cy = box.center_y if box.center_y is not None else 0.5
                bw = box.width if box.width is not None else 0.1
                bh = box.height if box.height is not None else 0.1

                pad_x = bw * 0.05
                pad_y = bh * 0.05
                x1 = int(max(0, (cx - bw / 2 - pad_x) * width))
                x2 = int(min(width, (cx + bw / 2 + pad_x) * width))
                y1 = int(max(0, (cy - bh / 2 - pad_y) * height))
                y2 = int(min(height, (cy + bh / 2 + pad_y) * height))

                if x2 - x1 < 10 or y2 - y1 < 8:
                    return False

                return is_italic_patch(img_gray[y1:y2, x1:x2])
            except Exception:
                return False

        if self.line_break_mode == "line":
            lines_text = []
            for p in paragraphs:
                for l in p.lines:
                    line_str = "".join(
                        (w.plain_text or "") + (w.text_separator or "")
                        for w in l.words
                    ).strip()
                    if line_str:
                        if self.detect_italic and _is_line_italic(l.geometry.bounding_box):
                            line_str = f"{{\\i1}}{line_str}{{\\i0}}"
                        lines_text.append(line_str)
            return "\n".join(lines_text)

        elif self.line_break_mode == "paragraph":
            result = ""
            separator = "\n"
            for index, paragraph in enumerate(paragraphs):
                if index > 0:
                    result += separator
                for line in paragraph.lines:
                    for word in line.words:
                        plain_text = word.plain_text or ""
                        separator_text = word.text_separator or ""
                        result += plain_text + separator_text
            return result

        else:  # default: "y_cluster"
            all_lines = []
            for p in paragraphs:
                for l in p.lines:
                    box = l.geometry.bounding_box
                    cy = box.center_y if box.center_y is not None else 0.5
                    cx = box.center_x if box.center_x is not None else 0.5
                    h = box.height if box.height is not None else 0.1
                    line_str = "".join(
                        (w.plain_text or "") + (w.text_separator or "")
                        for w in l.words
                    ).strip()
                    if line_str:
                        is_ita = _is_line_italic(box) if self.detect_italic else False
                        all_lines.append({"text": line_str, "cy": cy, "cx": cx, "h": h, "is_italic": is_ita})

            return cluster_lines_by_y(all_lines)