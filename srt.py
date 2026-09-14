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


def format_srt_block(start_ms: int, end_ms: int, text_content: str, index: int, is_top: bool = False) -> str:
    """Formats an SRT subtitle block with index, timestamps, and text."""
    start_ts = format_srt_timestamp(start_ms)
    end_ts = format_srt_timestamp(end_ms)
    text = text_content.replace(r"\N", "\n").replace(r"\n", "\n").strip()
    if is_top:
        text = f"{{\\an8}}{text}"
    return f"{index}\n{start_ts} --> {end_ts}\n{text}\n\n"


class SrtSubtitle:
    """Represents a single SubRip (SRT) subtitle entry with millisecond precision."""

    def __init__(self, start_ms: int, end_ms: int, text_content: str, is_top: bool = False):
        self.start_ms: int = start_ms
        self.end_ms: int = end_ms
        self.text_content: str = text_content
        self.is_top: bool = is_top

    @property
    def start_time(self) -> str:
        return format_srt_timestamp(self.start_ms)

    @property
    def end_time(self) -> str:
        return format_srt_timestamp(self.end_ms)

    def format(self, index: int) -> str:
        """
        Formats the subtitle into an SRT block.
        Top subtitles are prefixed with standard {\\an8}.
        """
        text = self.text_content.replace(r"\N", "\n").replace(r"\n", "\n").strip()
        if self.is_top:
            text = f"{{\\an8}}{text}"
        return f"{index}\n{self.start_time} --> {self.end_time}\n{text}\n\n"

    def __str__(self) -> str:
        return self.format(1)
