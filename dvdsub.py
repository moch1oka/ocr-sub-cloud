from dataclasses import dataclass
import io
from pathlib import Path
import re
import struct
from typing import Dict, List, Optional, Tuple

from PIL import Image

from pgs import crop_subtitle_image

# Default 16-color DVD CLUT palette (RGB)
DEFAULT_DVD_PALETTE: List[Tuple[int, int, int]] = [
    (0x00, 0x00, 0x00),  # 0: black
    (0xF0, 0xF0, 0xF0),  # 1: white
    (0xCC, 0xCC, 0xCC),  # 2: light gray
    (0x99, 0x99, 0x99),  # 3: gray
    (0x33, 0x33, 0xFA),  # 4: blue
    (0x11, 0x11, 0xBB),  # 5: dark blue
    (0xFA, 0x33, 0x33),  # 6: red
    (0xBB, 0x11, 0x11),  # 7: dark red
    (0x33, 0xFA, 0xFA),  # 8: cyan
    (0x11, 0xBB, 0xBB),  # 9: dark cyan
    (0xFA, 0xFA, 0x33),  # 10: yellow
    (0xBB, 0xBB, 0x11),  # 11: dark yellow
    (0xFA, 0x33, 0xFA),  # 12: magenta
    (0xBB, 0x11, 0xBB),  # 13: dark magenta
    (0x33, 0xFA, 0x33),  # 14: green
    (0x11, 0xBB, 0x11),  # 15: dark green
]


def ycbcr_to_rgb_bt601(y: int, cb: int, cr: int) -> Tuple[int, int, int]:
    """
    Converts YCbCr color to RGB using BT.601 color space standard (DVD).
    """
    y1 = (y - 16) * 1.164383562
    cb_offset = cb - 128
    cr_offset = cr - 128

    r = y1 + 1.596026317 * cr_offset
    g = y1 - 0.8129674985 * cr_offset - 0.3917615979 * cb_offset
    b = y1 + 2.017232218 * cb_offset

    return (
        max(0, min(255, round(r))),
        max(0, min(255, round(g))),
        max(0, min(255, round(b))),
    )


def detect_sup_type(sup_path: str | Path) -> str:
    """
    Detects whether a .sup file is Blu-ray PGS ('bluray') or DVD-SUP ('dvd').
    Blu-ray PGS segments start with 'PG' (0x50 0x47).
    DVD-SUP frames start with 'SP' (0x53 0x50).
    """
    p = Path(sup_path)
    if not p.is_file():
        return "unknown"
    with open(p, "rb") as f:
        magic = f.read(2)
        if magic == b"PG":
            return "bluray"
        if magic == b"SP":
            return "dvd"
    return "unknown"


@dataclass
class DvdSubtitle:
    """
    Represents a single decoded DVD subtitle event.
    Compatible with PgsSubtitle structure.
    """
    start_ms: int
    end_ms: int
    image: Image.Image
    is_top: bool = False
    is_forced: bool = False
    origin_x: int = 0
    origin_y: int = 0
    screen_width: int = 720
    screen_height: int = 480


class DvdSpu:
    """
    Decodes DVD Subpicture Unit (SPU) binary data.
    SPU contains 2-bit interlaced RLE bitmaps and Display Control Sequence (DCSQ) tables.
    """

    @staticmethod
    def decode_rle_field(
        src: bytes | bytearray,
        src_offset: int,
        src_len: int,
        trg: bytearray,
        trg_start_line: int,
        width: int,
        height: int,
    ) -> None:
        """
        Decodes one RLE field (even or odd interlaced lines) into target buffer.
        """
        if src_len <= 0 or src_offset >= len(src):
            return

        # Unpack bytes into 4-bit nibbles
        end_src = min(len(src), src_offset + src_len)
        data_slice = src[src_offset:end_src]
        nibbles = bytearray(len(data_slice) * 2)
        for i, b in enumerate(data_slice):
            nibbles[2 * i] = b >> 4
            nibbles[2 * i + 1] = b & 0x0F

        nibble_idx = 0
        total_nibbles = len(nibbles)
        line = trg_start_line
        x = 0

        while nibble_idx < total_nibbles and line < height:
            b = nibbles[nibble_idx]
            nibble_idx += 1

            if b != 0:
                # 1 or 2 nibble code
                length = b >> 2
                if length == 0:
                    # 2-nibble code
                    if nibble_idx >= total_nibbles:
                        break
                    b2 = nibbles[nibble_idx]
                    nibble_idx += 1
                    length = (b << 2) | (b2 >> 2)
                    col = b2 & 0x03
                else:
                    # 1-nibble code
                    col = b & 0x03
            else:
                # 3 or 4 nibble code / EOL
                if nibble_idx >= total_nibbles:
                    break
                b2 = nibbles[nibble_idx]
                nibble_idx += 1

                if (b2 & 0x0C) != 0:
                    # 3-nibble code
                    if nibble_idx >= total_nibbles:
                        break
                    b3 = nibbles[nibble_idx]
                    nibble_idx += 1
                    length = (b2 << 2) | (b3 >> 2)
                    col = b3 & 0x03
                else:
                    # 4-nibble code or EOL
                    if nibble_idx + 1 >= total_nibbles:
                        break
                    b3 = nibbles[nibble_idx]
                    b4 = nibbles[nibble_idx + 1]
                    nibble_idx += 2
                    length = (b2 << 6) | (b3 << 2) | (b4 >> 2)
                    col = b4 & 0x03

                    if length == 0:
                        # End of line (EOL) marker
                        length = width - x
                        # Byte-align nibbles after EOL
                        if (nibble_idx & 1) == 1:
                            nibble_idx += 1

            # Put pixels
            trg_row_offset = line * width
            for _ in range(length):
                if x < width and line < height:
                    trg[trg_row_offset + x] = col
                    x += 1
                if x >= width:
                    line += 2  # Interlaced step
                    x = 0
                    trg_row_offset = line * width
                    # Byte-align nibbles after line wrap
                    if (nibble_idx & 1) == 1:
                        nibble_idx += 1

    @classmethod
    def decode(
        cls,
        spu_data: bytes | bytearray,
        start_ms: int = 0,
        palette: Optional[List[Tuple[int, int, int]]] = None,
        screen_width: int = 720,
        screen_height: int = 480,
    ) -> Optional[DvdSubtitle]:
        """
        Parses an SPU binary unit and renders the resulting DvdSubtitle.
        """
        if len(spu_data) < 4:
            return None

        master_pal = palette or DEFAULT_DVD_PALETTE
        spu_size = (spu_data[0] << 8) | spu_data[1]
        dcsqt_offset = (spu_data[2] << 8) | spu_data[3]

        if dcsqt_offset >= len(spu_data) or dcsqt_offset < 4:
            return None

        # Parse Control Sequence Table (DCSQ)
        curr_dcsq = dcsqt_offset
        is_forced = False
        duration_ms: Optional[int] = None

        color_indices = [0, 1, 2, 3]
        alpha_values = [0, 15, 15, 15]

        x_start = 0
        x_end = 0
        y_start = 0
        y_end = 0
        top_offset = 4
        bottom_offset = 4

        visited_dcsq = set()

        while curr_dcsq < len(spu_data) and curr_dcsq not in visited_dcsq:
            visited_dcsq.add(curr_dcsq)
            if curr_dcsq + 4 > len(spu_data):
                break

            delay_val = (spu_data[curr_dcsq] << 8) | spu_data[curr_dcsq + 1]
            next_dcsq = (spu_data[curr_dcsq + 2] << 8) | spu_data[curr_dcsq + 3]

            cmd_ptr = curr_dcsq + 4
            parsing_cmds = True

            while cmd_ptr < len(spu_data) and parsing_cmds:
                cmd = spu_data[cmd_ptr]
                cmd_ptr += 1

                if cmd == 0x00:  # Forced display
                    is_forced = True
                elif cmd == 0x01:  # Start display
                    pass
                elif cmd == 0x02:  # Stop display
                    duration_ms = int(round((delay_val * 1024) / 90.0))
                elif cmd == 0x03:  # Set color mapping (4 colors)
                    if cmd_ptr + 2 <= len(spu_data):
                        b0 = spu_data[cmd_ptr]
                        b1 = spu_data[cmd_ptr + 1]
                        color_indices = [
                            b1 & 0x0F,
                            (b1 >> 4) & 0x0F,
                            b0 & 0x0F,
                            (b0 >> 4) & 0x0F,
                        ]
                        cmd_ptr += 2
                elif cmd == 0x04:  # Set contrast / alpha mapping (4 alphas)
                    if cmd_ptr + 2 <= len(spu_data):
                        b0 = spu_data[cmd_ptr]
                        b1 = spu_data[cmd_ptr + 1]
                        alpha_values = [
                            b1 & 0x0F,
                            (b1 >> 4) & 0x0F,
                            b0 & 0x0F,
                            (b0 >> 4) & 0x0F,
                        ]
                        cmd_ptr += 2
                elif cmd == 0x05:  # Display area coordinates
                    if cmd_ptr + 6 <= len(spu_data):
                        b = spu_data[cmd_ptr : cmd_ptr + 6]
                        x_start = (b[0] << 4) | (b[1] >> 4)
                        x_end = ((b[1] & 0x0F) << 8) | b[2]
                        y_start = (b[3] << 4) | (b[4] >> 4)
                        y_end = ((b[4] & 0x0F) << 8) | b[5]
                        cmd_ptr += 6
                elif cmd == 0x06:  # Pixel data offsets
                    if cmd_ptr + 4 <= len(spu_data):
                        top_offset = (spu_data[cmd_ptr] << 8) | spu_data[cmd_ptr + 1]
                        bottom_offset = (spu_data[cmd_ptr + 2] << 8) | spu_data[cmd_ptr + 3]
                        cmd_ptr += 4
                elif cmd == 0x07:  # Change color & contrast
                    if cmd_ptr + 2 <= len(spu_data):
                        param_len = (spu_data[cmd_ptr] << 8) | spu_data[cmd_ptr + 1]
                        cmd_ptr += 2 + param_len
                elif cmd == 0xFF:  # End of DCSQ
                    parsing_cmds = False
                    break
                else:
                    # Unknown command, terminate DCSQ
                    parsing_cmds = False
                    break

            if next_dcsq <= curr_dcsq or next_dcsq >= len(spu_data):
                break
            curr_dcsq = next_dcsq

        width = max(1, x_end - x_start + 1)
        height = max(1, y_end - y_start + 1)

        # Workaround for invisible captions where all alphas are 0
        if sum(alpha_values) == 0:
            alpha_values = [0, 15, 15, 15]

        # Calculate lengths of top and bottom fields
        if bottom_offset > top_offset:
            top_len = bottom_offset - top_offset
            bottom_len = max(0, dcsqt_offset - bottom_offset)
        else:
            bottom_len = top_offset - bottom_offset
            top_len = max(0, dcsqt_offset - top_offset)

        # Allocate 2-bit pixel buffer for uncompressed image
        raw_pixels = bytearray(width * height)

        # Decode even lines (Top field)
        cls.decode_rle_field(
            src=spu_data,
            src_offset=top_offset,
            src_len=top_len,
            trg=raw_pixels,
            trg_start_line=0,
            width=width,
            height=height,
        )

        # Decode odd lines (Bottom field)
        cls.decode_rle_field(
            src=spu_data,
            src_offset=bottom_offset,
            src_len=bottom_len,
            trg=raw_pixels,
            trg_start_line=1,
            width=width,
            height=height,
        )

        # Map 4-entry palette & alpha to RGBA values
        rgba_palette: List[Tuple[int, int, int, int]] = []
        for i in range(4):
            pal_idx = color_indices[i] % len(master_pal)
            r, g, b = master_pal[pal_idx]
            a = (alpha_values[i] * 255) // 15
            rgba_palette.append((r, g, b, a))

        # Build RGBA bytearray
        rgba_bytes = bytearray(width * height * 4)
        for idx, col_idx in enumerate(raw_pixels):
            r, g, b, a = rgba_palette[col_idx & 3]
            offset = idx * 4
            rgba_bytes[offset] = r
            rgba_bytes[offset + 1] = g
            rgba_bytes[offset + 2] = b
            rgba_bytes[offset + 3] = a

        raw_img = Image.frombytes("RGBA", (width, height), bytes(rgba_bytes))

        # Crop tightly around text
        cropped_img = crop_subtitle_image(raw_img, padding=10)

        # Composite onto solid black background for maximum contrast with OCR engines
        bg = Image.new("RGB", cropped_img.size, (0, 0, 0))
        if cropped_img.mode == "RGBA":
            bg.paste(cropped_img, mask=cropped_img.split()[3])
        else:
            bg.paste(cropped_img)

        # Determine end time
        if duration_ms is not None and duration_ms > 0:
            end_ms = start_ms + duration_ms
        else:
            end_ms = start_ms

        is_top = y_start < (screen_height * 0.35)

        return DvdSubtitle(
            start_ms=start_ms,
            end_ms=end_ms,
            image=bg,
            is_top=is_top,
            is_forced=is_forced,
            origin_x=x_start,
            origin_y=y_start,
            screen_width=screen_width,
            screen_height=screen_height,
        )


class IdxParser:
    """
    Parses VobSub .idx index files.
    """

    def __init__(self, idx_path: str | Path):
        self.idx_path = Path(idx_path)
        self.screen_width = 720
        self.screen_height = 480
        self.palette: List[Tuple[int, int, int]] = list(DEFAULT_DVD_PALETTE)
        self.languages: Dict[int, str] = {}  # stream_id -> lang_code
        self.entries: List[Tuple[int, int, int]] = []  # (start_ms, filepos, stream_id)
        self.parse()

    def parse(self) -> None:
        if not self.idx_path.is_file():
            return

        current_stream_id = 0

        with open(self.idx_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Screen size (e.g. "size: 720x480" or "size: 720x576")
                if line.lower().startswith("size:"):
                    val = line[5:].strip()
                    m = re.search(r"(\d+)\s*[xX,]\s*(\d+)", val)
                    if m:
                        self.screen_width = int(m.group(1))
                        self.screen_height = int(m.group(2))
                    continue

                # Master Palette (16 hex colors)
                if line.lower().startswith("palette:"):
                    val = line[8:].strip()
                    hex_colors = [c.strip().lstrip("#") for c in val.split(",") if c.strip()]
                    parsed_pal = []
                    for hc in hex_colors:
                        if len(hc) == 6:
                            try:
                                r = int(hc[0:2], 16)
                                g = int(hc[2:4], 16)
                                b = int(hc[4:6], 16)
                                parsed_pal.append((r, g, b))
                            except ValueError:
                                pass
                    if parsed_pal:
                        self.palette = parsed_pal
                    continue

                # Language / Stream ID (e.g. "id: en, index: 0")
                if line.lower().startswith("id:"):
                    val = line[3:].strip()
                    parts = [p.strip() for p in val.split(",")]
                    lang_code = parts[0].lower()
                    if len(parts) > 1 and "index" in parts[1].lower():
                        idx_match = re.search(r"index\s*:\s*(\d+)", parts[1], re.IGNORECASE)
                        if idx_match:
                            current_stream_id = int(idx_match.group(1))
                    self.languages[current_stream_id] = lang_code
                    continue

                # Subtitle timestamp & file position (e.g. "timestamp: 00:01:23:456, filepos: 000000000")
                if line.lower().startswith("timestamp:"):
                    # Format: timestamp: HH:MM:SS:mmm, filepos: XXXXXXXXX
                    m = re.search(
                        r"timestamp:\s*(\d+):(\d+):(\d+):(\d+),\s*filepos:\s*([0-9a-fA-F]+)",
                        line,
                        re.IGNORECASE,
                    )
                    if m:
                        h = int(m.group(1))
                        minute = int(m.group(2))
                        s = int(m.group(3))
                        ms = int(m.group(4))
                        start_ms = h * 3600000 + minute * 60000 + s * 1000 + ms
                        filepos = int(m.group(5), 16)
                        self.entries.append((start_ms, filepos, current_stream_id))


class IfoParser:
    """
    Parses DVD IFO files to extract video resolution and 16-color PGC palette.
    """

    def __init__(self, ifo_path: str | Path):
        self.ifo_path = Path(ifo_path)
        self.screen_width = 720
        self.screen_height = 480
        self.palette: List[Tuple[int, int, int]] = list(DEFAULT_DVD_PALETTE)
        self.parse()

    def parse(self) -> None:
        if not self.ifo_path.is_file():
            return

        with open(self.ifo_path, "rb") as f:
            data = f.read()

        if len(data) < 0x300 or not data.startswith(b"DVDVIDEO-VTS"):
            return

        # Check PAL vs NTSC from video attributes at 0x200
        vid_attr = (data[0x200] << 8) | data[0x201]
        is_pal = (vid_attr & 0x3000) != 0
        if is_pal:
            self.screen_width = 720
            self.screen_height = 576
        else:
            self.screen_width = 720
            self.screen_height = 480

        # Read VTS_PGCITI offset
        if len(data) > 0xD0:
            vts_pgciti_sector = struct.unpack(">I", data[0xCC:0xD0])[0]
            vts_pgciti_ofs = vts_pgciti_sector * 2048
            if vts_pgciti_ofs + 0x10 <= len(data):
                table_rel = struct.unpack(">I", data[vts_pgciti_ofs + 0x0C : vts_pgciti_ofs + 0x10])[0]
                pgc_ofs = vts_pgciti_ofs + table_rel

                # 16-color palette table at pgc_ofs + 0xA4
                pal_ofs = pgc_ofs + 0xA4
                if pal_ofs + 64 <= len(data):
                    extracted_pal = []
                    for i in range(16):
                        entry_ofs = pal_ofs + i * 4
                        y = data[entry_ofs + 1]
                        cb = data[entry_ofs + 2]
                        cr = data[entry_ofs + 3]
                        rgb = ycbcr_to_rgb_bt601(y, cb, cr)
                        extracted_pal.append(rgb)
                    if len(extracted_pal) == 16:
                        self.palette = extracted_pal


class VobSubParser:
    """
    Parses VobSub (.sub + .idx) subtitle files.
    """

    def __init__(self, sub_path: str | Path, idx_path: Optional[str | Path] = None):
        self.sub_path = Path(sub_path)
        if idx_path:
            self.idx_path = Path(idx_path)
        else:
            self.idx_path = self.sub_path.with_suffix(".idx")

        self.idx = IdxParser(self.idx_path)

    def parse(
        self,
        stream_id: Optional[int] = None,
        lang_code: Optional[str] = None,
        forced_only: bool = False,
    ) -> List[DvdSubtitle]:
        """
        Parses the .sub binary stream using the index offsets from .idx.
        """
        if not self.sub_path.is_file():
            return []

        # Determine target stream ID
        target_stream = stream_id
        if target_stream is None and lang_code:
            for sid, code in self.idx.languages.items():
                if code == lang_code.lower():
                    target_stream = sid
                    break
        if target_stream is None:
            # Default to first stream available
            target_stream = self.idx.entries[0][2] if self.idx.entries else 0

        subtitles: List[DvdSubtitle] = []

        with open(self.sub_path, "rb") as f:
            file_size = self.sub_path.stat().st_size

            for entry_idx, (start_ms, filepos, entry_stream) in enumerate(self.idx.entries):
                if entry_stream != target_stream:
                    continue
                if filepos >= file_size:
                    continue

                f.seek(filepos)
                spu_buf = bytearray()
                spu_size = -1
                pos = filepos

                # Accumulate SPU packets across MPEG-2 sectors
                while pos < file_size:
                    f.seek(pos)
                    sector = f.read(2048)
                    if len(sector) < 16:
                        break

                    # Check MPEG-2 Pack Header (0x000001BA)
                    if sector[0:4] == b"\x00\x00\x01\xba":
                        stuffing = sector[13] & 0x07
                        pes_start = 14 + stuffing
                    elif sector[0:4] == b"\x00\x00\x01\xbd":
                        pes_start = 0
                    else:
                        break

                    # Check Private Stream 1 (0x000001BD)
                    if pes_start + 9 >= len(sector) or sector[pes_start : pes_start + 4] != b"\x00\x00\x01\xbd":
                        break

                    pes_len = (sector[pes_start + 4] << 8) | sector[pes_start + 5]
                    header_data_len = sector[pes_start + 8]
                    stream_byte = sector[pes_start + 9 + header_data_len]

                    # DVD subpicture stream IDs are 0x20 to 0x3F
                    pack_stream_id = stream_byte - 0x20
                    if pack_stream_id == target_stream:
                        payload_start = pes_start + 9 + header_data_len + 1
                        payload_len = pes_len - (3 + header_data_len + 1)
                        if payload_len > 0 and payload_start < len(sector):
                            chunk = sector[payload_start : payload_start + payload_len]
                            if not spu_buf and len(chunk) >= 4:
                                spu_size = (chunk[0] << 8) | chunk[1]
                            spu_buf.extend(chunk)

                    if spu_size > 0 and len(spu_buf) >= spu_size:
                        break

                    pos += 2048

                if spu_buf and spu_size > 0:
                    sub = DvdSpu.decode(
                        spu_data=spu_buf[:spu_size],
                        start_ms=start_ms,
                        palette=self.idx.palette,
                        screen_width=self.idx.screen_width,
                        screen_height=self.idx.screen_height,
                    )
                    if sub:
                        if not forced_only or sub.is_forced:
                            subtitles.append(sub)

        # Fix missing or corrupt end times
        self._fix_subtitle_durations(subtitles)

        return subtitles

    @staticmethod
    def _fix_subtitle_durations(subtitles: List[DvdSubtitle]) -> None:
        """
        Fixes subtitles with missing or negative durations (common on DVD authoring).
        """
        for i, sub in enumerate(subtitles):
            if sub.end_ms <= sub.start_ms or (sub.end_ms - sub.start_ms) > 8000:
                if i + 1 < len(subtitles):
                    gap = subtitles[i + 1].start_ms - sub.start_ms - 50
                    sub.end_ms = sub.start_ms + max(100, min(5000, gap))
                else:
                    sub.end_ms = sub.start_ms + 3000


class DvdSupParser:
    """
    Parses DVD-SUP (.sup) subtitle files, optionally loading colors from .ifo.
    """

    def __init__(self, sup_path: str | Path, ifo_path: Optional[str | Path] = None):
        self.sup_path = Path(sup_path)
        if ifo_path:
            self.ifo_path: Optional[Path] = Path(ifo_path)
        else:
            candidate = self.sup_path.with_suffix(".ifo")
            self.ifo_path = candidate if candidate.is_file() else None

        if self.ifo_path and self.ifo_path.is_file():
            self.ifo = IfoParser(self.ifo_path)
            self.palette = self.ifo.palette
            self.screen_width = self.ifo.screen_width
            self.screen_height = self.ifo.screen_height
        else:
            self.palette = list(DEFAULT_DVD_PALETTE)
            self.screen_width = 720
            self.screen_height = 480

    def parse(self, forced_only: bool = False) -> List[DvdSubtitle]:
        """
        Parses all 'SP' frames from the DVD-SUP file.
        """
        if not self.sup_path.is_file():
            return []

        subtitles: List[DvdSubtitle] = []

        with open(self.sup_path, "rb") as f:
            data = f.read()

        offset = 0
        total_len = len(data)

        while offset + 10 < total_len:
            # Check 2-byte magic 'SP' (0x53 0x50)
            if data[offset : offset + 2] != b"SP":
                # Advance 1 byte to seek next frame
                offset += 1
                continue

            # PTS is a 32-bit little-endian integer in 90 kHz clock units
            pts = struct.unpack("<I", data[offset + 2 : offset + 6])[0]
            start_ms = int(round(pts / 90.0))

            # SPU unit begins at offset + 10
            spu_start = offset + 10
            if spu_start + 4 > total_len:
                break

            spu_size = (data[spu_start] << 8) | data[spu_start + 1]
            if spu_size <= 0 or spu_start + spu_size > total_len:
                break

            spu_data = data[spu_start : spu_start + spu_size]
            sub = DvdSpu.decode(
                spu_data=spu_data,
                start_ms=start_ms,
                palette=self.palette,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
            )

            if sub:
                if not forced_only or sub.is_forced:
                    subtitles.append(sub)

            offset = spu_start + spu_size

        # Fix missing or corrupt end times
        VobSubParser._fix_subtitle_durations(subtitles)

        return subtitles


def save_dvd_images(subtitles: List[DvdSubtitle], output_dir: Path) -> Path:
    """
    Saves subtitle images into output_dir with VideoSubFinder-compatible filenames:
    {prefix}{H}_{MM}_{SS}_{mmm}__{H}_{MM}_{SS}_{mmm}_{width}x{height}.png
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    for sub in subtitles:
        s_total = sub.start_ms
        s_h = s_total // 3600000
        s_total %= 3600000
        s_m = s_total // 60000
        s_total %= 60000
        s_s = s_total // 1000
        s_ms = s_total % 1000

        e_total = sub.end_ms
        e_h = e_total // 3600000
        e_total %= 3600000
        e_m = e_total // 60000
        e_total %= 60000
        e_s = e_total // 1000
        e_ms = e_total % 1000

        prefix = "top_" if sub.is_top else ""
        filename = (
            f"{prefix}{s_h}_{s_m:02d}_{s_s:02d}_{s_ms:03d}__"
            f"{e_h}_{e_m:02d}_{e_s:02d}_{e_ms:03d}_"
            f"{sub.image.width}x{sub.image.height}.png"
        )

        img_path = output_dir / filename
        sub.image.save(img_path, format="PNG", compress_level=1)

    return output_dir
