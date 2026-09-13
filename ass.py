from typing import override

class AssSubtitle:
    ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayDepth: 0
ScaledBorderAndShadow: Yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,60,&H00FFFFFF,&H00000000,&H4D000000,&H81000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,40,1
Style: Top,Arial,60,&H00FFFFFF,&H00000000,&H4D000000,&H81000000,-1,0,0,0,100,100,0,0,1,3,0,8,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def round_to_ass_timestamp(s: str) -> str:
    """
    Rounds a timestamp string (H:MM:SS.mmm or H:MM:SS,mmm)
    to Aegisub ASS format (H:MM:SS.cc) with standard half-up rounding.
    Matching Aegisub's exact algorithm: cs = (total_ms + 5) // 10.
    """
    s = s.replace(",", ".")
    h, m, rest = s.split(":")
    sec, frac = rest.split(".")
    if len(frac) > 3:
        ms_val = round(float(f"0.{frac}") * 1000)
    else:
        ms_val = int(frac.ljust(3, "0"))
    total_ms = int(h) * 3600000 + int(m) * 60000 + int(sec) * 1000 + ms_val

    cs = (total_ms + 5) // 10
    out_h = cs // 360000
    cs -= out_h * 360000
    out_m = cs // 6000
    cs -= out_m * 6000
    out_s = cs // 100
    cs -= out_s * 100
    return f"{out_h}:{out_m:02d}:{out_s:02d}.{cs:02d}"


class AssSubtitle:
    ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayDepth: 0
ScaledBorderAndShadow: Yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,60,&H00FFFFFF,&H00000000,&H4D000000,&H81000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,40,1
Style: Top,Arial,60,&H00FFFFFF,&H00000000,&H4D000000,&H81000000,-1,0,0,0,100,100,0,0,1,3,0,8,60,60,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def __init__(self, start_time: str, end_time: str, text_content: str, is_top: bool = False):
        self.start_time: str = self.convert_timestamp(start_time)
        self.end_time: str = self.convert_timestamp(end_time)
        self.text_content: str = text_content
        self.style_name: str = "Top" if is_top else "Default"
        self.is_top: bool = is_top

    def convert_timestamp(self, s: str) -> str:
        return round_to_ass_timestamp(s)

    @override
    def __str__(self):
        processed_text = self.text_content.replace('\n', '\\N')
        return f"Dialogue: 0,{self.start_time},{self.end_time},{self.style_name},,0,0,0,,{processed_text}\n"