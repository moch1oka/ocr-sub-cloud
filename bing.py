import base64
import json
import time
from urllib.parse import urlparse

from curl_cffi import CurlMime

from engine import OCREngine, check_italic_patch
from utils import get_image_raw_bytes_and_dims


class BingOCR(OCREngine):
    # Waits (seconds) before asking again when the answer has no OCR part yet
    OCR_NOT_READY_DELAYS: tuple[float, ...] = (0.6, 1.5)

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

    def process_image(self, img_path: str) -> str:
        img_data = get_image_raw_bytes_and_dims(img_path)
        if img_data is None:
            self.console.log(f"[red]Error preparing image '{img_path}'.[/red]")
            return ""

        raw_bytes, width, height = img_data
        img_b64 = base64.b64encode(raw_bytes).decode("ascii")

        # Step 1: Upload image to obtain insightsToken
        mp1 = CurlMime()
        try:
            mp1.addpart(name="imgurl", data="")
            mp1.addpart(name="cbir", data="sbi")
            mp1.addpart(name="imageBin", data=img_b64)

            r1 = self.post_with_retry(
                self.upload_url,
                multipart=mp1,
                allow_redirects=False,
                success_codes=(302,),
                timeout=20,
                error_context=f"upload {img_path}",
            )
        finally:
            mp1.close()

        if not r1:
            self.note_failure("image upload failed")
            return ""

        loc = r1.headers.get("Location", "")
        token = self.extract_insights_token(loc)
        parsed = urlparse(loc)
        if not token:
            # Typically a consent / verification page instead of the result page
            self.note_failure(f"upload gave no image token (redirected to {parsed.netloc}{parsed.path or '?'})")
            return ""

        api_host = parsed.netloc if parsed.netloc else "www.bing.com"

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

        # The OCR result is produced after the upload; asked too early (fast connections do this),
        # the answer comes back without it, so ask again after a short wait
        data: dict = {}
        for attempt in range(len(self.OCR_NOT_READY_DELAYS) + 1):
            if attempt:
                time.sleep(self.OCR_NOT_READY_DELAYS[attempt - 1])

            mp2 = CurlMime()
            try:
                mp2.addpart(
                    name="knowledgeRequest",
                    content_type="application/json",
                    data=json.dumps(req_data),
                )

                r2 = self.post_with_retry(
                    api_url,
                    multipart=mp2,
                    headers=headers,
                    timeout=20,
                    error_context=f"knowledge API {img_path}",
                )
            finally:
                mp2.close()

            if not r2:
                self.note_failure("OCR request failed")
                return ""

            try:
                data = r2.json()
            except Exception:
                self.note_failure("OCR answer was not JSON")
                return ""

            if self.find_ocr_tag(data) is not None:
                break

        return self.parse_knowledge_response(data, width, height, self.decode_grayscale(raw_bytes))

    @staticmethod
    def find_ocr_tag(data: dict) -> dict | None:
        return next((t for t in data.get("tags", []) if t.get("displayName") == "##TextRecognition"), None)

    @staticmethod
    def extract_insights_token(location: str) -> str:
        """Reads the insightsToken query parameter of the upload redirect (any letter case)."""
        for part in urlparse(location).query.split("&"):
            key, _, value = part.partition("=")
            if key.lower() == "insightstoken":
                return value  # kept as sent (not URL-decoded): it goes back to Bing unchanged
        return ""

    def parse_knowledge_response(self, data: dict, width: int, height: int, img_gray=None) -> str:
        """Step 3: turns Bing's knowledge JSON into text, using the line boxes for layout and italics."""
        ocr_tag = self.find_ocr_tag(data)
        if ocr_tag is None:
            # Also what an image without any text gives; only suspicious when every image does it.
            # What the answer did contain goes into the message, to tell a refusal from an API change.
            tag_names = sorted({t.get("displayName") or "(unnamed)" for t in data.get("tags", [])})
            contents = f"tags: {', '.join(tag_names)}" if tag_names else f"keys: {', '.join(sorted(data)) or 'none'}"
            self.note_failure(f"answer had no text recognition ({contents})", soft=True)
            return ""

        regions = []
        for action in ocr_tag.get("actions", []):
            if action.get("_type") == "ImageKnowledge/TextRecognitionAction":
                regions = action.get("data", {}).get("regions", [])
                break

        if not regions:
            return ""

        corners = ("topLeft", "topRight", "bottomRight", "bottomLeft")
        all_lines = []
        para_lines_list = []
        for r in regions:
            para_lines = []
            for l in r.get("lines", []):
                txt = l.get("text", "").strip()
                if not txt:
                    continue

                quad = l.get("boundingBox")
                is_ita = False
                if quad and all(k in quad for k in corners):
                    xs = [quad[k]["x"] for k in corners]
                    ys = [quad[k]["y"] for k in corners]
                    # Normalize pixel coordinates to 0..1 so clustering thresholds match Google Lens
                    if max(xs) > 1.0 or max(ys) > 1.0:
                        xs = [x / width for x in xs]
                        ys = [y / height for y in ys]
                    cx = sum(xs) / 4.0
                    cy = sum(ys) / 4.0
                    h = max(ys) - min(ys)
                    if img_gray is not None:
                        is_ita = check_italic_patch(
                            img_gray,
                            int(min(xs) * width),
                            int(min(ys) * height),
                            int(max(xs) * width),
                            int(max(ys) * height),
                        )
                else:
                    cy, cx, h = 0.5, 0.5, 0.1

                line = {"text": txt, "cy": cy, "cx": cx, "h": h, "is_italic": is_ita}
                all_lines.append(line)
                para_lines.append(line)

            if para_lines:
                para_lines_list.append(para_lines)

        return self.format_recognized_text(all_lines, para_lines_list)
