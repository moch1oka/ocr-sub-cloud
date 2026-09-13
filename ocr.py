import warnings
from pathlib import Path

from ass import AssSubtitle
from engine import OCREngine
from utils import text_cleanup, timecode_key


def _parse_time_ms(ts: str) -> int:
    try:
        ts = ts.replace(",", ".")
        h, m, rest = ts.split(":")
        s, frac = rest.split(".")
        if len(frac) > 3:
            ms_val = round(float(f"0.{frac}") * 1000)
        else:
            ms_val = int(frac.ljust(3, "0"))
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + ms_val
    except Exception:
        return 0


class OCR_Subtitles:
    def __init__(
        self, 
        output_subtitles_name: str | Path, 
        output_directory: str | Path, 
        images_dir_override: str | Path,
        ocr_engine: OCREngine,
    ) -> None:
        self.ass_dict: dict[str, AssSubtitle] = {}

        self.ocr_engine = ocr_engine

        self.images_dir, self.output_file_path = self._process_file(
            output_subtitles_name, output_directory, images_dir_override
        )

    def __call__(self):
        results = self.ocr_engine(self.images_dir)
        
        if not results:
            warnings.warn("No images processed or no text extracted.")
            return
                
        for img_name, text in results.items():
            self._create_subtitle(img_name, text)
        
        self._write_ass()
        
        print(f"Saved subtitles to {self.output_file_path}")

    def _process_file(
        self,
        output_subtitles_name: str | Path,
        output_directory: str | Path,
        images_dir_override: str | Path | None = None,
    ) -> tuple[Path, Path]:
        try:
            output_path = Path(output_directory).resolve()
            if images_dir_override:
                images_dir_path = Path(images_dir_override).resolve()
            else:
                images_dir_path = (Path.cwd() / "images").resolve()

            images_dir_path.mkdir(parents=True, exist_ok=True)
            output_path.mkdir(parents=True, exist_ok=True)

            counter = 0
            output_file_path: Path = output_path / f"{output_subtitles_name}.ass"
            while output_file_path.exists():
                counter += 1
                output_file_path = output_path / f"{output_subtitles_name}_{counter}.ass"

            return (images_dir_path, output_file_path)
        except (FileNotFoundError, ValueError, ImportError, IOError, Exception) as e:
            print(f"Error type: {type(e).__name__}")
            print(f"Error details: {e}")
            raise

    def _create_subtitle(self, img_name: str, text: str):
        if text is None:
            text = ""
        
        text = text_cleanup(text)

        try:
            img_stem = Path(img_name).stem
            if img_stem.startswith(("top_", "bot_")):
                is_top = img_stem.startswith("top_")
                time_part = img_stem[4:]
            else:
                is_top = False
                time_part = img_stem

            start_part, end_part = time_part.split("__")
            start_parts = start_part.split("_")
            end_parts = end_part.split("_")

            start_hour = start_parts[0]
            start_min = start_parts[1]
            start_sec = start_parts[2]
            start_micro = start_parts[3]

            end_hour = end_parts[0]
            end_min = end_parts[1]
            end_sec = end_parts[2]
            end_micro = end_parts[3]
                
        except (IndexError, ValueError):
            print(f"Error processing {img_name}: Filename format is incorrect.")
            return

        start_time = f"{start_hour}:{start_min}:{start_sec},{start_micro}"
        end_time = f"{end_hour}:{end_min}:{end_sec},{end_micro}"

        subtitle = AssSubtitle(start_time, end_time, text, is_top)
        self.ass_dict[img_name] = subtitle


    def _write_ass(self):
        cleaned_ass_bot = []
        cleaned_ass_top = []
        previous_subtitle_bot = None
        previous_subtitle_top = None
        for _, subtitle in sorted(self.ass_dict.items(), key=timecode_key):
            previous_subtitle = previous_subtitle_top if subtitle.is_top else previous_subtitle_bot
            cleaned_ass = cleaned_ass_top if subtitle.is_top else cleaned_ass_bot
            if not subtitle.text_content or subtitle.text_content.isspace():
                continue
            if not previous_subtitle:
                cleaned_ass.append(subtitle)
                if subtitle.is_top:
                    previous_subtitle_top = subtitle
                else:
                    previous_subtitle_bot = subtitle
                continue

            gap_ms = _parse_time_ms(subtitle.start_time) - _parse_time_ms(previous_subtitle.end_time)
            if previous_subtitle.text_content.lower() == subtitle.text_content.lower() and gap_ms <= 1000:
                end_time = (
                    subtitle.end_time
                    if _parse_time_ms(subtitle.end_time) >= _parse_time_ms(previous_subtitle.end_time)
                    else previous_subtitle.end_time
                )
                merged_subtitle = AssSubtitle(
                    start_time=previous_subtitle.start_time,
                    end_time=end_time,
                    text_content=previous_subtitle.text_content,
                    is_top=subtitle.is_top,
                )
                cleaned_ass.pop()
                cleaned_ass.append(merged_subtitle)
            else:
                cleaned_ass.append(subtitle)

            if subtitle.is_top:
                previous_subtitle_top = cleaned_ass[-1]
            else:
                previous_subtitle_bot = cleaned_ass[-1]
        try:
            all_subtitles = sorted(
                cleaned_ass_bot + cleaned_ass_top,
                key=lambda s: _parse_time_ms(s.start_time),
            )
            with self.output_file_path.open("w", encoding="utf-8") as ass_file:
                _ = ass_file.write(AssSubtitle.ASS_HEADER)
                for subtitle in all_subtitles:
                    _ = ass_file.write(str(subtitle))
        except IOError as e:
            print(f"Error writing to output file {self.output_file_path}: {e}")
            raise
