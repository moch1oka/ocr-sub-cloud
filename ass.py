from typing import override


def format_ass_timestamp(total_ms: int) -> str:
    """
    Converts milliseconds into Aegisub-standard ASS timestamp (H:MM:SS.cc).
    Uses half-up centisecond rounding: cs = (total_ms + 5) // 10.
    """
    total_ms = max(0, total_ms)
    cs = (total_ms + 5) // 10
    out_h = cs // 360000
    cs %= 360000
    out_m = cs // 6000
    cs %= 6000
    out_s = cs // 100
    cs %= 100
    return f"{out_h}:{out_m:02d}:{out_s:02d}.{cs:02d}"


def round_to_ass_timestamp(s: str) -> str:
    """
    Rounds a timestamp string (H:MM:SS.mmm or H:MM:SS,mmm or H:MM:SS.cc)
    to Aegisub ASS format (H:MM:SS.cc) with standard half-up rounding.
    """
    s = s.replace(",", ".")
    h, m, rest = s.split(":")
    sec, frac = rest.split(".")
    if len(frac) == 2:
        ms_val = int(frac) * 10
    elif len(frac) > 3:
        ms_val = round(float(f"0.{frac}") * 1000)
    else:
        ms_val = int(frac.ljust(3, "0"))
    total_ms = int(h) * 3600000 + int(m) * 60000 + int(sec) * 1000 + ms_val
    return format_ass_timestamp(total_ms)


def get_ass_header(font_name: str = "Arial") -> str:
    """Generates ASS header with the specified font name for Default and Top styles."""
    return f"""[Script Info]
ScriptType: v4.00+
PlayDepth: 0
ScaledBorderAndShadow: Yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},60,&H00FFFFFF,&H00000000,&H4D000000,&H81000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,40,1
Style: Top,{font_name},60,&H00FFFFFF,&H00000000,&H4D000000,&H81000000,-1,0,0,0,100,100,0,0,1,3,0,8,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class AssSubtitle:
    """Represents a single Advanced SubStation Alpha (.ass) dialogue line."""
    ASS_HEADER = get_ass_header("Arial")

    def __init__(self, start_time: int | str, end_time: int | str, text_content: str, is_top: bool = False):
        if isinstance(start_time, int):
            self.start_ms: int = start_time
            self.start_time: str = format_ass_timestamp(start_time)
        else:
            self.start_time: str = round_to_ass_timestamp(start_time)
            self.start_ms: int = self._parse_to_ms(self.start_time)

        if isinstance(end_time, int):
            self.end_ms: int = end_time
            self.end_time: str = format_ass_timestamp(end_time)
        else:
            self.end_time: str = round_to_ass_timestamp(end_time)
            self.end_ms: int = self._parse_to_ms(self.end_time)

        self.text_content: str = text_content
        self.style_name: str = "Top" if is_top else "Default"
        self.is_top: bool = is_top

    @staticmethod
    def _parse_to_ms(ts: str) -> int:
        try:
            ts = ts.replace(",", ".")
            h, m, rest = ts.split(":")
            s, frac = rest.split(".")
            ms_val = int(frac) * 10 if len(frac) == 2 else int(frac.ljust(3, "0")[:3])
            return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + ms_val
        except Exception:
            return 0

    def to_ass(self) -> str:
        processed_text = self.text_content.replace("\n", r"\N")
        return f"Dialogue: 0,{self.start_time},{self.end_time},{self.style_name},,0,0,0,,{processed_text}\n"

    @override
    def __str__(self) -> str:
        return self.to_ass()