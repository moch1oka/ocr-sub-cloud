import random
import threading
from typing import Any

from curl_cffi import requests

from engine import OCREngine, cluster_lines_by_y
from lens import (
    AppliedFilter,
    LensOverlayFilterType,
    LensOverlayRoutingInfo,
    LensOverlayServerRequest,
    LensOverlayServerResponse,
    Platform,
    Surface,
)
from utils import get_image_raw_bytes_and_dims

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
        super().__init__(threads=threads, line_break_mode=line_break_mode, lang=lang)

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
        request.objects_request.request_context.request_id.analytics_id = random.randbytes(n=16)
        request.objects_request.request_context.request_id.routing_info = LensOverlayRoutingInfo()

        request.objects_request.request_context.client_context.platform = Platform.WEB
        request.objects_request.request_context.client_context.surface = Surface.CHROMIUM

        request.objects_request.request_context.client_context.locale_context.language = self.lang_cfg.lens_lang
        request.objects_request.request_context.client_context.locale_context.region = self.lang_cfg.lens_region
        request.objects_request.request_context.client_context.locale_context.time_zone = ""

        request.objects_request.request_context.client_context.app_id = ""

        filter_obj = AppliedFilter()
        filter_obj.filter_type = LensOverlayFilterType.AUTO_FILTER
        request.objects_request.request_context.client_context.client_filters.filter.append(filter_obj)

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

            except Exception as e:
                last_exception = e
                if attempt == max_retries - 1:
                    self.console.log(f"[yellow]Failed to upload image {img_path} after {max_retries} attempts: {last_exception}[/yellow]")
                    return ""
                continue

        if res is None or res.status_code != 200:
            return ""

        response_proto = LensOverlayServerResponse().FromString(res.content)
        response_dict: dict[str, Any] = response_proto.to_dict()

        paragraphs = (
            response_dict.get("objectsResponse", {}).get("text", {}).get("textLayout", {}).get("paragraphs", [])
        )
        if not paragraphs:
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

            return cluster_lines_by_y(all_lines)