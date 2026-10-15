import random

from engine import OCREngine, check_italic_patch
from lens import (
    AUTO_FILTER,
    PLATFORM_WEB,
    SURFACE_CHROMIUM,
    LensOverlayServerRequest,
    LensOverlayServerResponse,
)
from utils import get_image_raw_bytes_and_dims


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

        res = self.post_with_retry(
            self.LENS_ENDPOINT,
            data=payload,
            headers=self.HEADERS,
            timeout=30,
            error_context=img_path,
        )

        if res is None or res.status_code != 200:
            return ""

        try:
            response_proto = LensOverlayServerResponse()
            response_proto.ParseFromString(res.content)
            paragraphs = response_proto.objects_response.text.text_layout.paragraphs
            if not paragraphs:
                return ""
        except Exception as e:
            self.console.log(f"[yellow]Protobuf parse error for {img_path}: {e}[/yellow]")
            return ""

        img_gray = self.decode_grayscale(raw_bytes)

        all_lines = []
        para_lines_list = []
        for p in paragraphs:
            para_lines = []
            for l in p.lines:
                # proto3 floats have no presence: an absent box reads as all zeros
                box = l.geometry.bounding_box
                if box.width > 0 and box.height > 0:
                    cy, cx, bw, bh = box.center_y, box.center_x, box.width, box.height
                else:
                    cy, cx, bw, bh = 0.5, 0.5, 0.1, 0.1

                line_str = "".join(
                    (w.plain_text or "") + (w.text_separator or "")
                    for w in l.words
                ).strip()
                if not line_str:
                    continue

                is_ita = False
                if img_gray is not None:
                    x1 = int(max(0, (cx - bw / 2) * width))
                    x2 = int(min(width, (cx + bw / 2) * width))
                    y1 = int(max(0, (cy - bh / 2) * height))
                    y2 = int(min(height, (cy + bh / 2) * height))
                    is_ita = check_italic_patch(img_gray, x1, y1, x2, y2)

                line = {"text": line_str, "cy": cy, "cx": cx, "h": bh, "is_italic": is_ita}
                all_lines.append(line)
                para_lines.append(line)

            if para_lines:
                para_lines_list.append(para_lines)

        return self.format_recognized_text(all_lines, para_lines_list)