import os
import platform
import subprocess
from pathlib import Path

from utils import get_in_path


class VSFError(Exception):
    """Raised when VideoSubFinder execution fails."""
    pass


class VideoSubFinder:
    def __init__(
        self,
        vsf_exe_path: str | None = None,
        clear_dirs: bool = True,
        run_search: bool = True,
        create_cleared_text_images: bool = False,
        open_video_opencv: bool = True,
        open_video_ffmpeg: bool = False,
        use_cuda: bool = False,
        start_time: str | None = None,
        end_time: str | None = None,
        top_video_image_percent_end: float | None = None,
        bottom_video_image_percent_end: float | None = None,
        left_video_image_percent_end: float | None = None,
        right_video_image_percent_end: float | None = None,
        general_settings: str | None = None,
        num_threads: int | None = None,
        num_ocr_threads: int | None = None,
        use_filter_color: str | None = None,
        moderate_threshold: float | None = None,
        moderate_threshold_for_scaled_image: float | None = None,
        image_scale_for_clear_image: int | None = None,
        use_ILA_images_before_clear_txt_images_from_borders: bool | None = None,
        use_ILA_images_for_getting_txt_symbols_areas: bool | None = None,
        use_ILA_images_for_clear_txt_images: bool | None = None,
        clear_txt_images_by_main_color: bool | None = None,
        video_gamma: float | None = None,
        **kwargs,
    ):
        if vsf_exe_path is None:
            self.exe_path = get_in_path("VideoSubFinderWXW")
        else:
            self.exe_path = vsf_exe_path

        if self.exe_path is None:
            raise FileNotFoundError(
                "VideoSubFinderWXW executable not found. Please place VideoSubFinder in PATH or specify via -vsf."
            )
        if not os.access(self.exe_path, os.X_OK):
            raise PermissionError(
                f"VideoSubFinderWXW executable at '{self.exe_path}' is not accessible or not executable."
            )

        # Auto-detect settings/general.cfg located next to VideoSubFinderWXW.exe
        if general_settings is None:
            auto_cfg = Path(self.exe_path).resolve().parent / "settings" / "general.cfg"
            if auto_cfg.is_file():
                general_settings = str(auto_cfg)
                print(f"Using VSF settings: {general_settings}")
            else:
                print(f"Warning: general.cfg not found at {auto_cfg}")

        self.txtimage = create_cleared_text_images

        # Core execution flags
        run_list = [self.exe_path]
        if clear_dirs:
            run_list.append("--clear_dirs")
        if run_search:
            run_list.append("--run_search")
        if create_cleared_text_images:
            run_list.append("--create_cleared_text_images")
        if open_video_opencv:
            run_list.append("--open_video_opencv")
        elif open_video_ffmpeg:
            run_list.append("--open_video_ffmpeg")
        if use_cuda:
            run_list.append("--use_cuda")
        if general_settings:
            run_list.extend(["--general_settings", general_settings])

        # CLI overrides (only added if user explicitly specified a non-None value)
        cli_overrides = {
            "start_time": start_time,
            "end_time": end_time,
            "top_video_image_percent_end": top_video_image_percent_end,
            "bottom_video_image_percent_end": bottom_video_image_percent_end,
            "left_video_image_percent_end": left_video_image_percent_end,
            "right_video_image_percent_end": right_video_image_percent_end,
            "num_threads": num_threads,
            "num_ocr_threads": num_ocr_threads,
        }
        for k, v in cli_overrides.items():
            if v is not None:
                run_list.extend([f"--{k}", str(v)])

        start = "/" if platform.system() == "Windows" else "-"
        algo_overrides = {
            "use_filter_color": use_filter_color,
            "moderate_threshold": moderate_threshold,
            "moderate_threshold_for_scaled_image": moderate_threshold_for_scaled_image,
            "image_scale_for_clear_image": image_scale_for_clear_image,
            "use_ILA_images_for_getting_txt_symbols_areas": use_ILA_images_for_getting_txt_symbols_areas,
            "use_ILA_images_before_clear_txt_images_from_borders": use_ILA_images_before_clear_txt_images_from_borders,
            "use_ILA_images_for_clear_txt_images": use_ILA_images_for_clear_txt_images,
            "clear_txt_images_by_main_color": clear_txt_images_by_main_color,
            "video_gamma": video_gamma,
        }
        for k, v in algo_overrides.items():
            if v is not None:
                val_str = str(int(v)) if isinstance(v, bool) else str(v)
                run_list.extend([f"{start}{k}", val_str])

        self.run_list = run_list

    def __call__(self, video_path: str | Path, output_dir: str | Path):
        cmd = list(self.run_list) + ["--input_video", str(video_path), "--output_dir", str(output_dir)]
        exe_dir = str(Path(self.exe_path).resolve().parent)

        proc = subprocess.run(cmd, cwd=exe_dir)
        if proc.returncode != 0:
            raise VSFError(
                f"VideoSubFinder failed with exit code {proc.returncode} for video '{video_path}'."
            )
