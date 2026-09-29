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

        all_lines = []
        para_texts = []
        for r in regions:
            para_lines = []
            for l in r.get("lines", []):
                txt = l.get("text", "").strip()
                if not txt:
                    continue

                para_lines.append(txt)

                quad = l.get("boundingBox")
                if quad and all(k in quad for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")):
                    cy = sum(quad[k]["y"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")) / 4.0
                    cx = sum(quad[k]["x"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")) / 4.0
                    h = max(quad["bottomRight"]["y"], quad["bottomLeft"]["y"]) - min(quad["topLeft"]["y"], quad["topRight"]["y"])
                else:
                    cy = 0.5
                    cx = 0.5
                    h = 0.1

                is_ita = False
                if img_gray is not None and quad and all(k in quad for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")):
                    xs = [quad[k]["x"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")]
                    ys = [quad[k]["y"] for k in ("topLeft", "topRight", "bottomRight", "bottomLeft")]
                    if max(xs) <= 1.0 and max(ys) <= 1.0:
                        min_x, max_x = min(xs) * width, max(xs) * width
                        min_y, max_y = min(ys) * height, max(ys) * height
                    else:
                        min_x, max_x = min(xs), max(xs)
                        min_y, max_y = min(ys), max(ys)
                    is_ita = check_italic_patch(img_gray, int(min_x), int(min_y), int(max_x), int(max_y))

                all_lines.append({"text": txt, "cy": cy, "cx": cx, "h": h, "is_italic": is_ita})

            if para_lines:
                para_texts.append(" ".join(para_lines))

        return self.format_recognized_text(all_lines, para_texts)
