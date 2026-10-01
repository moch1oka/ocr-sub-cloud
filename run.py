import argparse
from argparse import BooleanOptionalAction
from pathlib import Path

from dvdsub import DvdSupParser, VobSubParser, detect_sup_type, save_dvd_images
from engine import OCREngine, OCREngineType, create_ocr_engine, ocr_engine_type
from ocr import OCR_Subtitles
from pgs import PgsParser, save_pgs_images
from utils import float_range
from vsf import VideoSubFinder


# ==============================================================================
# DEFAULT SETTINGS (TÙY CHỈNH MẶC ĐỊNH TẠI ĐÂY)
# ==============================================================================
DEFAULT_OCR_ENGINE = OCREngineType.GGLENS  # OCREngineType.GGLENS hoặc OCREngineType.BING
DEFAULT_GGLENS_THREAD = 32                 # Số luồng Google Lens (khuyên dùng: 32 - 80)
DEFAULT_BING_THREAD = 32                   # Số luồng Bing OCR (khuyên dùng: 16 - 48)
DEFAULT_LANG = "vi"                        # "vi", "en", "ja", "zh"
DEFAULT_FORMAT = "ass"                     # "ass", "srt", "both"
DEFAULT_LINE_BREAK_MODE = "y_cluster"      # "y_cluster", "line", "paragraph"
DEFAULT_DETECT_ITALIC = False              # True để bật nhận diện chữ nghiêng
DEFAULT_OUTPUT_DIR = "outputs"             # Thư mục lưu phụ đề xuất ra
# ==============================================================================


def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A tool to OCR subtitles from video files, Blu-ray PGS (.sup), DVD-SUP (.sup), DVD VobSub (.sub/.idx), or extracted images using VideoSubFinder, Google Lens, and Bing OCR."
    )
    parser.add_argument(
        "-o",
        "--output_dir",
        type=str,
        default=DEFAULT_OUTPUT_DIR,
        help=f"The path to output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )

    parser.add_argument(
        "-n",
        "--output_name",
        "--output-name",
        type=str,
        default=None,
        dest="output_subtitles",
        metavar="<outputname>",
        help="Base name for output subtitle file (without extension). Default: output_subtitles",
    )

    parser.add_argument(
        "--img_dir",
        type=str,
        default=None,
        help="The full path of directory contain extracted images, when specified this will skip image extract step.",
    )

    parser.add_argument(
        "--sup",
        type=str,
        default=None,
        help="The path of subtitle file (.sup), supporting both Blu-ray PGS and DVD-SUP. Can also be passed via -i / --video_dir.",
    )

    parser.add_argument(
        "--vobsub",
        type=str,
        default=None,
        help="The path of DVD VobSub file (.sub). Can also be passed via -i / --video_dir.",
    )

    parser.add_argument(
        "--idx",
        type=str,
        default=None,
        help="The path of DVD VobSub index file (.idx). Optional if located in the same directory as .sub.",
    )

    parser.add_argument(
        "--ifo",
        type=str,
        default=None,
        help="The path of DVD IFO file (.ifo) for DVD-SUP palette. Optional if located in the same directory as .sup.",
    )

    parser.add_argument(
        "--stream_id",
        type=int,
        default=None,
        help="Stream ID for DVD VobSub when multiple languages exist. Default: auto.",
    )

    parser.add_argument(
        "--forced_only",
        action="store_true",
        default=False,
        help="Only extract and OCR forced subtitles from PGS / DVD subtitle file. Default: False",
    )

    parser.add_argument(
        "-f",
        "--format",
        type=str,
        choices=["ass", "srt", "both"],
        default=DEFAULT_FORMAT,
        help=f"Subtitle output format. Choices: ['ass', 'srt', 'both']. Default: {DEFAULT_FORMAT}",
    )

    parser.add_argument(
        "-l",
        "--lang",
        type=str,
        choices=["vi", "en", "ja", "zh"],
        default=DEFAULT_LANG,
        help=f"Language preset for OCR engine, text cleanup, font, and subtitle merging. Choices: ['vi', 'en', 'ja', 'zh']. Default: {DEFAULT_LANG}",
    )

    parser.add_argument(
        "--font",
        type=str,
        default=None,
        help="Override default font for ASS subtitles (e.g. 'Arial', 'Meiryo', 'Microsoft YaHei').",
    )

    ocr_group = parser.add_argument_group(title="OCR Engine Settings")

    ocr_group.add_argument(
        "--ocr_engine",
        type=ocr_engine_type,
        default=DEFAULT_OCR_ENGINE,
        help=f"Select OCR engine. Choices: {[e.value for e in OCREngineType]}. Default: {DEFAULT_OCR_ENGINE.value}",
    )

    # Google Lens settings
    ocr_group.add_argument(
        "--gglens_thread",
        type=int,
        default=DEFAULT_GGLENS_THREAD,
        help=f"Google Lens OCR threads (e.g. 32-80 threads for 20-30 img/s). Default: {DEFAULT_GGLENS_THREAD}",
    )
    # Bing OCR settings
    ocr_group.add_argument(
        "--bing_thread",
        type=int,
        default=DEFAULT_BING_THREAD,
        help=f"Bing OCR threads. Default: {DEFAULT_BING_THREAD}",
    )

    ocr_group.add_argument(
        "--line_break_mode",
        type=str,
        choices=["y_cluster", "line", "paragraph"],
        default=DEFAULT_LINE_BREAK_MODE,
        help=f"Line break detection mode: 'y_cluster' (smart Y-coordinate clustering, recommended), 'line' (break on every line), 'paragraph' (break only on paragraphs). Default: {DEFAULT_LINE_BREAK_MODE}",
    )
    ocr_group.add_argument(
        "--detect_italic",
        action=BooleanOptionalAction,
        default=DEFAULT_DETECT_ITALIC,
        help=f"Enable local detection and preservation of italic text (e.g. {{\\i1}} in ASS, <i> in SRT). Default: {DEFAULT_DETECT_ITALIC}",
    )

    vsf_param_group = parser.add_argument_group(title="VideoSubFinder")
    vsf_param_group.add_argument(
        "-vsf",
        "--vsf_exe_path",
        type=str,
        default=None,
        help="The full path of VideoSubFinderWXW. Default: from PATH (Must have .exe suffix in Windows).",
    )
    vsf_param_group.add_argument(
        "-i",
        "--video_dir",
        type=str,
        default=None,
        help="The path of video, directory of videos, Blu-ray PGS (.sup), DVD-SUP (.sup), or DVD VobSub (.sub / .idx).",
    )
    vsf_param_group.add_argument(
        "--clear_dirs",
        action=BooleanOptionalAction,
        default=True,
        help="Enable or disable clear Folders (remove all images), performed before any other steps. Default: True.",
    )
    vsf_param_group.add_argument(
        "--run_search",
        action=BooleanOptionalAction,
        default=True,
        help="Enable or disable run Search (find frames with hardcoded text (hardsub) on video). Default: True.",
    )
    vsf_param_group.add_argument(
        "--create_cleared_text_images",
        action=BooleanOptionalAction,
        default=False,
        help="Enable or disable create Cleared Text Images. Default: False.",
    )
    vsf_param_group.add_argument(
        "--open_video_opencv",
        action=BooleanOptionalAction,
        default=True,
        help="Enable or disable open video by OpenCV (default). Default: True.",
    )
    vsf_param_group.add_argument(
        "--open_video_ffmpeg",
        action=BooleanOptionalAction,
        default=None,
        help="Enable or disable open video by FFMPEG.",
    )
    vsf_param_group.add_argument(
        "--use_cuda",
        action=BooleanOptionalAction,
        default=None,
        help="use cuda",
    )
    vsf_param_group.add_argument(
        "--start_time",
        type=str,
        default=None,
        help="start time, default = from general.cfg or video start (format hour:min:sec:millisec).",
    )
    vsf_param_group.add_argument(
        "--end_time",
        type=str,
        default=None,
        help="end time, default = from general.cfg or video end",
    )
    vsf_param_group.add_argument(
        "-te",
        "--top_video_image_percent_end",
        type=float_range(0.0, 1.0),
        default=None,
        help="top video image percent offset from image bottom (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "-be",
        "--bottom_video_image_percent_end",
        type=float_range(0.0, 1.0),
        default=None,
        help="bottom video image percent offset from image bottom (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "-le",
        "--left_video_image_percent_end",
        type=float_range(0.0, 1.0),
        default=None,
        help="left video image percent end (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "-re",
        "--right_video_image_percent_end",
        type=float_range(0.0, 1.0),
        default=None,
        help="right video image percent end (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "-gs",
        "--general_settings",
        type=str,
        default=None,
        help="general settings (path to general settings *.cfg file, default: auto-detected from [VideoSubFinderWXW PATH]/settings/general.cfg).",
    )
    vsf_param_group.add_argument(
        "-nthr",
        "--num_threads",
        type=int,
        default=None,
        help="number of threads used for Run Search (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "-nocrthr",
        "--num_ocr_threads",
        type=int,
        default=None,
        help="number of threads used for Create Cleared TXT Images (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--use_filter_color",
        type=str,
        default=None,
        help="Define Use Filter Colors (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--moderate_threshold",
        type=float_range(0.0, 1.0),
        default=None,
        help="Define moderate_threshold (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--moderate_threshold_for_scaled_image",
        type=float_range(0.0, 1.0),
        default=None,
        help="Define moderate_threshold_for_scaled_image [0.1 - moderate_threshold] (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--image_scale_for_clear_image",
        type=int,
        default=None,
        help="Define image scale for clear image (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--use_ILA_images_for_getting_txt_symbols_areas",
        action=BooleanOptionalAction,
        default=None,
        help="Enable or disable use ILA images for getting txt symbols areas (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--use_ILA_images_before_clear_txt_images_from_borders",
        action=BooleanOptionalAction,
        default=None,
        help="Enable or disable use ILA images before clear txt images from borders (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--use_ILA_images_for_clear_txt_images",
        action=BooleanOptionalAction,
        default=None,
        help="Enable or disable use ILA images for clear txt images (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--clear_txt_images_by_main_color",
        action=BooleanOptionalAction,
        default=None,
        help="Enable or disable clear txt images by main color (override general.cfg).",
    )
    vsf_param_group.add_argument(
        "--video_gamma",
        type=float_range(0.0, 1.0),
        default=None,
        help="Define video_gamma [0.0 - 1.0] (override general.cfg).",
    )
    return parser


def process_sup(
    sup_path: Path,
    output_dir: str,
    ocr_engine: OCREngine,
    subtitle_format: str = "ass",
    lang: str = "vi",
    output_subtitles_name: str | None = None,
    font: str | None = None,
    forced_only: bool = False,
    ifo_path: str | Path | None = None,
):
    if not sup_path.is_file():
        raise FileNotFoundError(f"Subtitle file not found: {sup_path}")

    save_name = Path(output_subtitles_name).stem if output_subtitles_name else sup_path.stem
    save_dir = Path(output_dir) / save_name

    sup_type = detect_sup_type(sup_path)
    if sup_type == "dvd":
        target_images_dir = save_dir / "DvdSup_Images"
        print(f"Detected DVD-SUP format in '{sup_path}'")
        dvd_parser = DvdSupParser(sup_path, ifo_path=ifo_path)
        subtitles = dvd_parser.parse(forced_only=forced_only)

        if not subtitles:
            print(f"No subtitle pictures found in {sup_path}")
            return

        print(f"Extracted {len(subtitles)} DVD subtitle pictures. Saving cropped images to {target_images_dir} ...")
        save_dvd_images(subtitles, target_images_dir)
    else:
        target_images_dir = save_dir / "PGS_Images"
        print(f"Parsing Blu-ray PGS file '{sup_path}' ...")
        pgs_parser = PgsParser(sup_path)
        subtitles = pgs_parser.parse(forced_only=forced_only)

        if not subtitles:
            print(f"No subtitle pictures found in {sup_path}")
            return

        print(f"Extracted {len(subtitles)} subtitle pictures. Saving cropped images to {target_images_dir} ...")
        save_pgs_images(subtitles, target_images_dir)

    print(f"Running {ocr_engine.engine_name} on extracted images ...")
    ocr = OCR_Subtitles(
        output_subtitles_name=save_name,
        output_directory=save_dir,
        images_dir_override=target_images_dir,
        ocr_engine=ocr_engine,
        subtitle_format=subtitle_format,
        lang=lang,
        font=font,
    )
    ocr()


def process_vobsub(
    sub_path: Path,
    output_dir: str,
    ocr_engine: OCREngine,
    idx_path: str | Path | None = None,
    subtitle_format: str = "ass",
    lang: str = "vi",
    output_subtitles_name: str | None = None,
    font: str | None = None,
    stream_id: int | None = None,
    forced_only: bool = False,
):
    if not sub_path.is_file():
        raise FileNotFoundError(f"VobSub file not found: {sub_path}")

    save_name = Path(output_subtitles_name).stem if output_subtitles_name else sub_path.stem
    save_dir = Path(output_dir) / save_name
    vobsub_images_dir = save_dir / "VobSub_Images"

    print(f"Parsing DVD VobSub file '{sub_path}' ...")
    parser = VobSubParser(sub_path, idx_path=idx_path)
    subtitles = parser.parse(stream_id=stream_id, lang_code=lang, forced_only=forced_only)

    if not subtitles:
        print(f"No subtitle pictures found in {sub_path}")
        return

    print(f"Extracted {len(subtitles)} VobSub subtitle pictures. Saving cropped images to {vobsub_images_dir} ...")
    save_dvd_images(subtitles, vobsub_images_dir)

    print(f"Running {ocr_engine.engine_name} on extracted VobSub images ...")
    ocr = OCR_Subtitles(
        output_subtitles_name=save_name,
        output_directory=save_dir,
        images_dir_override=vobsub_images_dir,
        ocr_engine=ocr_engine,
        subtitle_format=subtitle_format,
        lang=lang,
        font=font,
    )
    ocr()


def process_vsf(
    video_list: list[Path],
    output_dir: str,
    vsf: VideoSubFinder,
    ocr_engine: OCREngine,
    subtitle_format: str = "ass",
    lang: str = "vi",
    output_subtitles_name: str | None = None,
    font: str | None = None,
):
    print("Extracting subtitle images with VideoSubFinder (takes quite a long time) ...")
    video_num = len(video_list)
    for i, one_video in enumerate(video_list):
        print(f"[{i+1}/{video_num}] Starting to extract {one_video} key frame")

        if len(video_list) == 1 and output_subtitles_name:
            save_name = Path(output_subtitles_name).stem
        else:
            save_name = Path(one_video).stem

        save_dir = Path(output_dir) / save_name
        save_vsf_dir = save_dir / "VSF_Results"

        try:
            vsf(one_video, save_vsf_dir)
        except Exception as e:
            print(f"Extract {one_video} error, {e}, skip")
            continue

        print(f"[{i + 1}/{video_num}] Starting to run {one_video} ocr")

        images_dir = Path(save_vsf_dir) / "RGBImages"
        if vsf.txtimage:
            images_dir = Path(save_vsf_dir) / "TXTImages"

        ocr = OCR_Subtitles(
            output_subtitles_name=save_name,
            output_directory=save_dir,
            images_dir_override=images_dir,
            ocr_engine=ocr_engine,
            subtitle_format=subtitle_format,
            lang=lang,
            font=font,
        )
        ocr()


def main():
    parser = create_arg_parser()
    args = parser.parse_args()

    ocr_engine: OCREngine = create_ocr_engine(args.ocr_engine, args)
    output_dir: str = args.output_dir

    # 1. OCR from extracted images directory
    if args.img_dir:
        subtitle_name = args.output_subtitles or "output_subtitles"
        ocr = OCR_Subtitles(
            output_subtitles_name=subtitle_name,
            output_directory=output_dir,
            images_dir_override=args.img_dir,
            ocr_engine=ocr_engine,
            subtitle_format=args.format,
            lang=args.lang,
            font=args.font,
        )
        ocr()
        return

    # 2. OCR from DVD VobSub (.sub / .idx)
    vobsub_candidate = args.vobsub or (
        args.video_dir if args.video_dir and Path(args.video_dir).suffix.lower() in [".sub", ".idx"] else None
    )
    if vobsub_candidate:
        cand_path = Path(vobsub_candidate)
        if cand_path.suffix.lower() == ".idx":
            sub_path = cand_path.with_suffix(".sub")
            idx_path = cand_path
        else:
            sub_path = cand_path
            idx_path = Path(args.idx) if args.idx else None

        process_vobsub(
            sub_path=sub_path,
            output_dir=output_dir,
            ocr_engine=ocr_engine,
            idx_path=idx_path,
            subtitle_format=args.format,
            lang=args.lang,
            output_subtitles_name=args.output_subtitles,
            font=args.font,
            stream_id=args.stream_id,
            forced_only=args.forced_only,
        )
        return

    # 3. OCR from subtitle file (.sup: Blu-ray PGS or DVD-SUP)
    sup_candidate = args.sup or (
        args.video_dir if args.video_dir and Path(args.video_dir).suffix.lower() == ".sup" else None
    )
    if sup_candidate:
        process_sup(
            sup_path=Path(sup_candidate),
            output_dir=output_dir,
            ocr_engine=ocr_engine,
            subtitle_format=args.format,
            lang=args.lang,
            output_subtitles_name=args.output_subtitles,
            font=args.font,
            forced_only=args.forced_only,
            ifo_path=args.ifo,
        )
        return

    # 4. Process video with VideoSubFinder
    video_path = args.video_dir
    if video_path is None:
        parser.error("--video_dir, --sup, --vobsub, or --img_dir is required")

    video_formats = {".mp4", ".avi", ".mov", ".mkv"}
    vsf = VideoSubFinder(**vars(args))

    if Path(video_path).is_dir():
        video_list = [v.absolute() for v in Path(video_path).rglob("*.*") if v.suffix.lower() in video_formats]
    else:
        video_list = [Path(video_path)]

    if not video_list:
        print(f"No matching video files ({', '.join(video_formats)}) found in {video_path}")
        return

    process_vsf(
        video_list,
        output_dir,
        vsf,
        ocr_engine=ocr_engine,
        subtitle_format=args.format,
        lang=args.lang,
        output_subtitles_name=args.output_subtitles,
        font=args.font,
    )
    print("Done")


if __name__ == "__main__":
    main()
