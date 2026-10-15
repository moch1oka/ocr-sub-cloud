import re


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


_ITALIC_TAG = re.compile(r"(\{\\i[01]\})")


def escape_ass_braces(text: str) -> str:
    """
    Braces start override blocks in ASS, so a '{' read by OCR would hide the rest of the line.
    ASS has no escape every renderer understands; outside our own {\\i1}/{\\i0} tags,
    braces become their full-width look-alikes.
    """
    parts = _ITALIC_TAG.split(text)
    return "".join(
        p if _ITALIC_TAG.fullmatch(p) else p.replace("{", "｛").replace("}", "｝") for p in parts
    )


def format_ass_dialogue(start_ms: int, end_ms: int, text_content: str, is_top: bool = False) -> str:
    """Formats a dialogue line into standard ASS syntax."""
    start_ts = format_ass_timestamp(start_ms)
    end_ts = format_ass_timestamp(end_ms)
    style = "Top" if is_top else "Default"
    processed_text = escape_ass_braces(text_content.replace("\n", r"\N"))
    return f"Dialogue: 0,{start_ts},{end_ts},{style},,0,0,0,,{processed_text}\n"