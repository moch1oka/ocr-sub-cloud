import base64
import json
from urllib.parse import urlparse

from curl_cffi import CurlMime

from engine import OCREngine, check_italic_patch
from utils import get_image_raw_bytes_and_dims


class BingOCR(OCREngine):
    def __init__(
        self,
        threads: int = 32,
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
            return ""

        loc = r1.headers.get("Location", "")
        if "insightsToken=" not in loc:
            return ""

        token = loc.split("insightsToken=")[1].split("&")[0]
        parsed = urlparse(loc)
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
            return ""

        try:
            data = r2.json()
        except Exception:
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

        img_gray = self.decode_grayscale(raw_bytes)

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
