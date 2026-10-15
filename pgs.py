from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops


def ycbcr_to_rgb(y: int, cb: int, cr: int) -> Tuple[int, int, int]:
    """
    Converts YCbCr color to RGB using BT.709 color space standard (Blu-ray).
    """
    y_scaled = (y - 16) * 1.164383562
    cb_offset = cb - 128
    cr_offset = cr - 128

    r = y_scaled + 1.792741071 * cr_offset
    g = y_scaled - 0.5329093286 * cr_offset - 0.2132486143 * cb_offset
    b = y_scaled + 2.112401786 * cb_offset

    # Compensate for slight rounding losses near 255
    if r > 254.0:
        r += 0.35
    if g > 254.0:
        g += 0.35
    if b > 254.0:
        b += 0.35

    return (
        max(0, min(255, round(r))),
        max(0, min(255, round(g))),
        max(0, min(255, round(b))),
    )


def crop_subtitle_image(img: Image.Image, padding: int = 10) -> Image.Image:
    """
    Crops subtitle image tightly around the text content by detecting either transparent
    borders or uniform background borders. Adds a small padding.
    """
    bbox = None
    if img.mode == "RGBA":
        alpha = img.split()[3]
        bbox = alpha.getbbox()

    if not bbox or bbox == (0, 0, img.width, img.height):
        corner_color = img.getpixel((0, 0))
        bg = Image.new(img.mode, img.size, corner_color)
        diff = ImageChops.difference(img, bg)
        bbox = diff.convert("RGB").getbbox()

    if not bbox:
        return img

    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(img.width, bbox[2] + padding)
    bottom = min(img.height, bbox[3] + padding)

    return img.crop((left, top, right, bottom))


@dataclass
class PcsObjectInfo:
    object_id: int
    window_id: int
    is_forced: bool
    origin_x: int
    origin_y: int


@dataclass
class PcsData:
    start_ms: int
    screen_width: int
    screen_height: int
    composition_number: int
    composition_state: int
    palette_id: int
    objects: List[PcsObjectInfo] = field(default_factory=list)


@dataclass
class OdsData:
    object_id: int
    version: int
    is_first: bool
    is_last: bool
    width: int
    height: int
    rle_data: bytearray


@dataclass
class DisplaySet:
    pcs: PcsData
    palettes: Dict[int, Dict[int, Tuple[int, int, int, int]]]
    objects: Dict[int, OdsData]


@dataclass
class PgsSubtitle:
    start_ms: int
    end_ms: int
    image: Image.Image
    is_top: bool = False
    is_forced: bool = False
    origin_x: int = 0
    origin_y: int = 0
    screen_width: int = 1920
    screen_height: int = 1080


class PgsParser:
    """
    Pure Python parser and decoder for Blu-ray Presentation Graphic Stream (.sup) files.
    """

    SEG_PALETTE = 0x14  # PDS
    SEG_OBJECT = 0x15   # ODS
    SEG_PICTURE = 0x16  # PCS
    SEG_WINDOW = 0x17   # WDS
    SEG_END = 0x80      # END

    def __init__(self, sup_path: str | Path):
        self.sup_path = Path(sup_path)

    @staticmethod
    def _decode_rle(buf: bytes | bytearray, width: int, height: int, palette: Dict[int, Tuple[int, int, int, int]]) -> Image.Image:
        """
        Decodes PGS Run-Length Encoded (RLE) bitmap data into an RGBA Pillow Image.
        Pixels are decoded as palette indices (1 byte each) and Pillow maps them to RGBA.
        """
        total_pixels = width * height
        indices = bytearray(total_pixels)
        buflen = len(buf)
        idx = 0
        ofs = 0
        xpos = 0

        def fill(val: int, size: int) -> None:
            nonlocal ofs
            count = max(0, min(size, total_pixels - ofs))
            if val:  # the buffer already starts out as index 0
                indices[ofs:ofs + count] = bytes((val,)) * count
            ofs += count

        while idx < buflen:
            b = buf[idx]
            idx += 1
            if b == 0 and idx < buflen:
                b = buf[idx]
                idx += 1
                if b == 0:
                    # End of line
                    ofs = (ofs // width) * width
                    if xpos < width:
                        ofs += width
                    xpos = 0
                elif (b & 0xC0) == 0x40 and idx < buflen:
                    # 00 4x xx -> xxx zeroes
                    size = ((b - 0x40) << 8) + buf[idx]
                    idx += 1
                    fill(0, size)
                    xpos += size
                elif (b & 0xC0) == 0x80 and idx < buflen:
                    # 00 8x yy -> x times value y
                    size = b - 0x80
                    fill(buf[idx], size)
                    idx += 1
                    xpos += size
                elif (b & 0xC0) == 0xC0 and idx + 1 < buflen:
                    # 00 cx yy zz -> xyy times value z
                    size = ((b - 0xC0) << 8) + buf[idx]
                    fill(buf[idx + 1], size)
                    idx += 2
                    xpos += size
                else:
                    # 00 xx -> xx times 0
                    fill(0, b)
                    xpos += b
            else:
                # Regular single pixel
                if ofs < total_pixels:
                    indices[ofs] = b
                    ofs += 1
                xpos += 1

        # Unknown palette entries render like entry 0 (normally fully transparent)
        transparent_color = palette.get(0, (0, 0, 0, 0))
        flat_palette = [v for i in range(256) for v in palette.get(i, transparent_color)]
        img = Image.frombytes("P", (width, height), bytes(indices))
        img.putpalette(flat_palette, rawmode="RGBA")
        return img.convert("RGBA")

    def parse(
        self,
        forced_only: bool = False,
        range_start_ms: Optional[int] = None,
        range_end_ms: Optional[int] = None,
    ) -> List[PgsSubtitle]:
        """
        Parses the .sup file and extracts a list of PgsSubtitle objects with timing and images.
        With range_start_ms / range_end_ms, only subtitles on screen at some point in that range are decoded.
        """
        with self.sup_path.open("rb") as f:
            data = f.read()

        pos = 0
        total_len = len(data)

        palettes: Dict[int, Dict[int, Tuple[int, int, int, int]]] = {}
        objects: Dict[int, OdsData] = {}
        current_pcs: Optional[PcsData] = None
        display_sets: List[DisplaySet] = []

        while pos < total_len:
            if pos + 13 > total_len:
                break

            magic = data[pos:pos + 2]
            if magic != b"PG":
                # Find next PG sync header if corrupted
                next_pg = data.find(b"PG", pos + 1)
                if next_pg == -1:
                    break
                pos = next_pg
                continue

            pts = int.from_bytes(data[pos + 2:pos + 6], "big")
            seg_type = data[pos + 10]
            seg_size = int.from_bytes(data[pos + 11:pos + 13], "big")

            payload = data[pos + 13:pos + 13 + seg_size]
            pos += 13 + seg_size

            pts_ms = pts // 90

            if seg_type == self.SEG_PICTURE:
                if len(payload) >= 11:
                    screen_w = int.from_bytes(payload[0:2], "big")
                    screen_h = int.from_bytes(payload[2:4], "big")
                    comp_num = int.from_bytes(payload[5:7], "big")
                    comp_state = payload[7]
                    pal_id = payload[9]
                    obj_count = payload[10]

                    current_pcs = PcsData(
                        start_ms=pts_ms,
                        screen_width=screen_w,
                        screen_height=screen_h,
                        composition_number=comp_num,
                        composition_state=comp_state,
                        palette_id=pal_id,
                    )

                    offset = 11
                    for _ in range(obj_count):
                        if offset + 8 <= len(payload):
                            o_id = int.from_bytes(payload[offset:offset + 2], "big")
                            w_id = payload[offset + 2]
                            forced_cropped = payload[offset + 3]
                            is_forced = (forced_cropped & 0x40) != 0
                            ox = int.from_bytes(payload[offset + 4:offset + 6], "big")
                            oy = int.from_bytes(payload[offset + 6:offset + 8], "big")
                            current_pcs.objects.append(PcsObjectInfo(o_id, w_id, is_forced, ox, oy))
                            offset += 8

                    # Epoch Start clears previous caches
                    if comp_state == 0x80:
                        objects.clear()
                        palettes.clear()

            elif seg_type == self.SEG_PALETTE:
                if len(payload) >= 2:
                    pal_id = payload[0]
                    num_entries = (seg_size - 2) // 5
                    pal_dict = palettes.setdefault(pal_id, {})
                    for i in range(num_entries):
                        e_offset = 2 + i * 5
                        if e_offset + 5 <= len(payload):
                            pal_idx, y, cr, cb, a = payload[e_offset:e_offset + 5]
                            r, g, b = ycbcr_to_rgb(y, cb, cr)
                            pal_dict[pal_idx] = (r, g, b, a)

            elif seg_type == self.SEG_OBJECT:
                if len(payload) >= 4:
                    obj_id = int.from_bytes(payload[0:2], "big")
                    obj_ver = payload[2]
                    seq_flag = payload[3]
                    is_first = (seq_flag & 0x80) != 0
                    is_last = (seq_flag & 0x40) != 0

                    if is_first and len(payload) >= 11:
                        w = int.from_bytes(payload[7:9], "big")
                        h = int.from_bytes(payload[9:11], "big")
                        rle_chunk = bytearray(payload[11:])
                        objects[obj_id] = OdsData(
                            object_id=obj_id,
                            version=obj_ver,
                            is_first=True,
                            is_last=is_last,
                            width=w,
                            height=h,
                            rle_data=rle_chunk,
                        )
                    elif obj_id in objects:
                        objects[obj_id].rle_data.extend(payload[4:])

            elif seg_type == self.SEG_END:
                if current_pcs is not None:
                    # Palette copy
                    pal_copy = {pid: dict(entries) for pid, entries in palettes.items()}
                    # A shallow copy is enough: a new ODS replaces the dict entry with a new OdsData,
                    # and continuation fragments only extend the object started in the same display set
                    display_sets.append(DisplaySet(pcs=current_pcs, palettes=pal_copy, objects=dict(objects)))
                    current_pcs = None

        # Pair display sets and decode subtitles
        subtitles: List[PgsSubtitle] = []
        num_ds = len(display_sets)

        for i in range(num_ds):
            ds = display_sets[i]
            if not ds.pcs.objects:
                # Clear-screen display set (has no objects to render)
                continue

            # Acquisition points repeat the composition on screen (same composition number, often every
            # second) so players can start mid-stream; they are the same subtitle, not a new one
            comp_num = ds.pcs.composition_number
            if i > 0 and display_sets[i - 1].pcs.composition_number == comp_num and display_sets[i - 1].pcs.objects:
                continue

            start_ms = ds.pcs.start_ms

            # Determine end_ms: the next display set that actually changes the composition
            end_ms = 0
            for j in range(i + 1, num_ds):
                next_ds = display_sets[j]
                if next_ds.pcs.start_ms > start_ms and next_ds.pcs.composition_number != comp_num:
                    end_ms = next_ds.pcs.start_ms
                    break

            if end_ms <= start_ms:
                end_ms = start_ms + 3000

            # Outside the requested time range: skip before the (expensive) bitmap decoding
            if (range_start_ms is not None and end_ms <= range_start_ms) or (
                range_end_ms is not None and start_ms >= range_end_ms
            ):
                continue

            palette = ds.palettes.get(ds.pcs.palette_id, {})
            if not palette and ds.palettes:
                palette = next(iter(ds.palettes.values()))

            is_forced_sub = any(o.is_forced for o in ds.pcs.objects)
            if forced_only and not is_forced_sub:
                continue

            # Decode all objects in this display set
            decoded_objects = []
            for obj_info in ds.pcs.objects:
                ods = ds.objects.get(obj_info.object_id)
                if ods and ods.width > 0 and ods.height > 0 and ods.rle_data:
                    img = self._decode_rle(ods.rle_data, ods.width, ods.height, palette)
                    if img.getchannel("A").getbbox():  # skip fully transparent objects
                        decoded_objects.append((obj_info, img))

            if not decoded_objects:
                continue

            # Objects at the top of screen (< 35% height) and at the bottom are separate subtitles
            # (e.g. a sign and a dialogue line shown at the same time), so they are rendered separately
            top_limit = ds.pcs.screen_height * 0.35
            groups = {
                True: [(o, img) for o, img in decoded_objects if o.origin_y < top_limit],
                False: [(o, img) for o, img in decoded_objects if o.origin_y >= top_limit],
            }

            for is_top, group in groups.items():
                if not group or (forced_only and not any(o.is_forced for o, _ in group)):
                    continue
                final_img, final_x, final_y = self._compose_objects(group)

                subtitles.append(
                    PgsSubtitle(
                        start_ms=start_ms,
                        end_ms=end_ms,
                        image=flatten_on_black(crop_subtitle_image(final_img, padding=10)),
                        is_top=is_top,
                        is_forced=any(o.is_forced for o, _ in group),
                        origin_x=final_x,
                        origin_y=final_y,
                        screen_width=ds.pcs.screen_width,
                        screen_height=ds.pcs.screen_height,
                    )
                )

        return subtitles

    @staticmethod
    def _compose_objects(objs: List[Tuple[PcsObjectInfo, Image.Image]]) -> Tuple[Image.Image, int, int]:
        """Pastes decoded objects onto one canvas at their screen positions."""
        if len(objs) == 1:
            obj_info, obj_img = objs[0]
            return obj_img, obj_info.origin_x, obj_info.origin_y

        min_x = min(o.origin_x for o, _ in objs)
        min_y = min(o.origin_y for o, _ in objs)
        max_x = max(o.origin_x + img.width for o, img in objs)
        max_y = max(o.origin_y + img.height for o, img in objs)

        canvas = Image.new("RGBA", (max(1, max_x - min_x), max(1, max_y - min_y)), (0, 0, 0, 0))
        for o, img in objs:
            canvas.paste(img, (o.origin_x - min_x, o.origin_y - min_y), mask=img)
        return canvas, min_x, min_y


def flatten_on_black(img: Image.Image) -> Image.Image:
    """Composites an image onto a solid black background for maximum contrast with white/yellow subtitles."""
    bg = Image.new("RGB", img.size, (0, 0, 0))
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img)
    return bg


def _format_filename_timecode(total_ms: int) -> str:
    h, rest = divmod(max(0, total_ms), 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h}_{m:02d}_{s:02d}_{ms:03d}"


def save_subtitle_images(subtitles: list, output_dir: Path) -> Path:
    """
    Saves subtitle images (PgsSubtitle / DvdSubtitle) into output_dir with VideoSubFinder-compatible filenames:
    {prefix}{H}_{MM}_{SS}_{mmm}__{H}_{MM}_{SS}_{mmm}_{width}x{height}[_{n}].png
    (prefix is 'top_' if is_top, else empty; _{n} is only added to keep identical names apart).
    Images left over from a previous run in output_dir are removed first, so only these subtitles get OCRed.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("*.png"):
        old.unlink()
    used_names: set[str] = set()

    for sub in subtitles:
        prefix = "top_" if sub.is_top else ""
        stem = (
            f"{prefix}{_format_filename_timecode(sub.start_ms)}__"
            f"{_format_filename_timecode(sub.end_ms)}_"
            f"{sub.image.width}x{sub.image.height}"
        )
        filename = f"{stem}.png"
        counter = 1
        while filename in used_names:
            filename = f"{stem}_{counter}.png"
            counter += 1
        used_names.add(filename)

        sub.image.save(output_dir / filename, format="PNG", compress_level=1)

    return output_dir
