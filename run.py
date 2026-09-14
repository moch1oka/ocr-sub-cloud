import argparse
from argparse import BooleanOptionalAction
from pathlib import Path

from engine import OCREngine, OCREngineType
from ocr import OCR_Subtitles
from utils import create_ocr_engine, float_range, ocr_engine_type
from vsf import VideoSubFinder


def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="A tool to OCR subtitles from video files or extracted images using VideoSubFinder, Google Lens, and Bing OCR."
    )
    parser.add_argument(
        "-o",
        "--output_dir",
        type=str,
        default="outputs",
        help="The path to output directory. Default: outputs",
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
        "-f",
        "--format",
        type=str,
        choices=["ass", "srt", "both"],
        default="ass",
        help="Subtitle output format. Choices: ['ass', 'srt', 'both']. Default: ass",
    )

    parser.add_argument(
        "-l",
        "--lang",
        type=str,
        choices=["vi", "en", "ja", "zh"],
        default="vi",
        help="Language preset for OCR engine, text cleanup, font, and subtitle merging. Choices: ['vi', 'en', 'ja', 'zh']. Default: vi",
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
        default=OCREngineType.GGLENS,
        help=f"Select OCR engine. Choices: {[e.value for e in OCREngineType]}. Default: {OCREngineType.GGLENS.value}",
    )

    # Google Lens settings
    ocr_group.add_argument(
        "--gglens_thread",
        type=int,
        default=16,
        help="Google Lens OCR threads. Default: 16",
    )
    # Bing OCR settings
    ocr_group.add_argument(
        "--bing_thread",
        type=int,
        default=16,
        help="Bing OCR threads. Default: 16",
    )

    ocr_group.add_argument(
        "--line_break_mode",
        type=str,
        choices=["y_cluster", "line", "paragraph"],
        default="y_cluster",
        help="Line break detection mode: 'y_cluster' (smart Y-coordinate clustering, recommended), 'line' (break on every line), 'paragraph' (break only on paragraphs). Default: y_cluster",
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
        help="The path of video or the path of video directory.",
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

    # Process video with VideoSubFinder
    video_path = args.video_dir
    if video_path is None:
        parser.error("--video_dir (or --img_dir) is required")

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
