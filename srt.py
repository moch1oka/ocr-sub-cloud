import re


def format_srt_timestamp(total_ms: int) -> str:
    """
    Formats total milliseconds into standard SubRip (SRT) timestamp format: HH:MM:SS,mmm
    Preserves exact 3-digit millisecond precision.
    """
    total_ms = max(0, total_ms)
    out_h = total_ms // 3600000
    total_ms %= 3600000
    out_m = total_ms // 60000
    total_ms %= 60000
    out_s = total_ms // 1000
    out_ms = total_ms % 1000
    return f"{out_h:02d}:{out_m:02d}:{out_s:02d},{out_ms:03d}"


def ass_to_srt_text(text: str) -> str:
    """Converts ASS line breaks and formatting tags (e.g. italics) to SRT compatible tags."""
    text = text.replace(r"\N", "\n").replace(r"\n", "\n")
    text = re.sub(r"\{\\i1\}", "<i>", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\\i0\}", "</i>", text, flags=re.IGNORECASE)
    return text.strip()


def format_srt_block(start_ms: int, end_ms: int, text_content: str, index: int, is_top: bool = False) -> str:
    """Formats an SRT subtitle block with index, timestamps, and text."""
    start_ts = format_srt_timestamp(start_ms)
    end_ts = format_srt_timestamp(end_ms)
    text = ass_to_srt_text(text_content)
    if is_top:
        text = f"{{\\an8}}{text}"
    return f"{index}\n{start_ts} --> {end_ts}\n{text}\n\n"
