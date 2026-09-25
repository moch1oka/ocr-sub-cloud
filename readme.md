# TPN-Team's OCR tool

A tool to extract and OCR subtitles from video files, Blu-ray PGS (`.sup`), DVD-SUP (`.sup`), DVD VobSub (`.sub`/`.idx`), or extracted images using **VideoSubFinder**, **Google Lens**, and **Bing Visual Search OCR**, generating Aegisub-compliant ASS and SRT subtitle files.

## Features

- **Multiple Cloud OCR Engines**:
  - **Google Lens**: Uses official `google.protobuf` (compiled C++ `upb` backend) over persistent HTTP/1.1 connection pools, achieving blazing speeds of **25–30 images/sec** (via `--gglens_thread 64-80`).
  - **Bing Visual Search OCR**: High-accuracy multi-threaded recognition with two-step token negotiation.
- **Pure Python Image Subtitle Decoders (Self-contained, No External Tools)**:
  - **Blu-ray PGS (.sup)**: Decodes 90kHz PTS timecodes, RLE bitmaps, and BT.709 color palettes directly in memory.
  - **DVD-SUP (.sup / .ifo)**: Decodes DVD SPU frames (SP header) with optional `.ifo` video attributes and BT.601 color palettes.
  - **DVD VobSub (.sub / .idx)**: Reassembles MPEG-2 Program Stream PES packets, parses `.idx` timestamps and 16-color palettes, with support for multi-language stream selection.
  - **Auto-Detection for `.sup` files**: Automatically detects whether a `.sup` file is Blu-ray PGS (`PG` header) or DVD-SUP (`SP` header) without requiring manual format flags.
  - **High-Contrast Preprocessing**: Automatically detects and tightly crops subtitle bounding boxes (`crop_subtitle_image`), compositing onto solid black backgrounds for optimal cloud OCR accuracy.
  - **Zero-Duration and Invisible Caption Fixes**: Automatically compensates for missing `StopDisplay` commands and zero-alpha authoring bugs common on DVD discs.
- **Language Presets (`-l / --lang {vi, en, ja, zh}`)**:
  - Automatically tunes OCR engine requests, character whitelisting, and subtitle styles.
  - **Japanese (`ja`) & Chinese (`zh`)**: Removes accidental OCR spaces between CJK characters, preserves full-width CJK punctuation without inserting unwanted ASCII spaces, and sets native CJK ASS subtitle fonts (`Meiryo` / `Microsoft YaHei`).
  - **Vietnamese (`vi`) & English (`en`)**: Preserves Vietnamese diacritics, normalizes quotes, and standardizes spacing around punctuation.
- **Smart Line Breaks & Layout**: Geometry Y-coordinate clustering (`y_cluster`) accurately reconstructs multi-line dialogue layouts without arbitrary breaks or merging separate speakers.
- **Local Italic Style Detection (`--detect_italic`)**: Cloud OCR engines (Google Lens, Bing) do not detect font styles. This tool analyzes text line patches locally using shear projection profile variance, detecting italic/slanted text in ~1ms and preserving it via `{\i1}...{\i0}` in ASS and `<i>...</i>` in SRT.
- **Intelligent Subtitle Merging**: Fuzzy SequenceMatcher merges consecutive frames of the same utterance even when minor OCR artifacts (missing punctuation, character variations) occur.
- **Dedicated Dual Export Formats**:
  - `.srt`: Uses exact 3-digit millisecond timecodes directly from image filenames (`HH:MM:SS,mmm`) with `{\an8}` top-subtitle support.
  - `.ass`: Aegisub-compliant half-up centisecond rounding (`H:MM:SS.cc`) with language-optimized fonts and styles.
  - Output `.ass`, `.srt`, or `both` simultaneously.
  - Custom base naming via `-n / --output_name`.
- **Seamless VideoSubFinder Integration**: Automatically detects and loads `settings/general.cfg` located next to `VideoSubFinderWXW.exe`, fully preserving user settings.

---

## Setup

### Step 1: Install Python
Ensure Python 3.12+ is installed.

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: VideoSubFinder (Optional, only needed for video hardsubs)
If extracting hardcoded subtitles from video files, download [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/). You can either place `VideoSubFinderWXW.exe` in your system `PATH` or pass its path via `-vsf`.
*(Note: VideoSubFinder is NOT needed when processing PGS, DVD-SUP, VobSub, or pre-extracted images!)*

---

## Usage

### 1. OCR from DVD VobSub (.sub / .idx)
Directly extract and OCR DVD VobSub subtitle files without external tools:

```bash
# Provide .sub file (companion .idx with same base name in same directory is auto-detected)
python run.py -i "path/to/movie.sub" -o outputs -n movie_sub -f both

# Or pass .idx directly:
python run.py -i "path/to/movie.idx" -o outputs -n movie_sub -f both

# Or specify both explicitly:
python run.py --vobsub "path/to/movie.sub" --idx "path/to/movie.idx" -o outputs -n movie_sub -l en

# Select a specific subtitle stream ID when multiple languages exist in VobSub:
python run.py --vobsub "path/to/movie.sub" --stream_id 1 -o outputs -n movie_sub -l en
```

### 2. OCR from Blu-ray PGS or DVD-SUP (.sup)
The tool auto-detects Blu-ray PGS vs DVD-SUP formats by inspecting file headers:

```bash
# Auto-detects Blu-ray PGS or DVD-SUP from .sup file:
python run.py -i "path/to/subtitles.sup" -o outputs -n my_subtitles -f both

# Supply an optional .ifo file for DVD-SUP palette:
python run.py --sup "path/to/movie.sup" --ifo "path/to/VTS_01_0.IFO" -o outputs -n movie_sub

# Only extract and OCR forced subtitles:
python run.py --sup "path/to/subtitles.sup" -o outputs -n forced_sub --forced_only
```

### 3. OCR from Extracted Images Directory
If you already have extracted images (e.g. from VideoSubFinder's `RGBImages` folder):

```bash
# Using Google Lens (default, high speed with 64-80 threads)
python run.py --img_dir "path/to/RGBImages" -o outputs -n my_subtitles --gglens_thread 80

# Using Bing OCR
python run.py --img_dir "path/to/RGBImages" -o outputs -n my_subtitles --ocr_engine bing
```

### 4. Full Pipeline from Video File (Hardsub)
Extract frames with VideoSubFinder and OCR in one command:

```bash
# VideoSubFinder auto-detects its general.cfg next to VideoSubFinderWXW.exe
python run.py -i "path/to/video.mp4" -o outputs -n ep01_sub -f both

# If VideoSubFinder is not in PATH, specify with -vsf:
python run.py -vsf "E:\VideoSubFinder\Release_x64\VideoSubFinderWXW.exe" -i "path/to/video.mp4" -o outputs -n ep01_sub --ocr_engine gglens --gglens_thread 64
```

---

## CLI Options

### General & Output Options
```text
  -o, --output_dir OUTPUT_DIR
                        The path to output directory. Default: outputs
  -n, --output_name, --output-name <outputname>
                        Base name for output subtitle file (without extension).
                        Default: derived from input file stem or 'output_subtitles'
  -f, --format {ass,srt,both}
                        Subtitle output format. Choices: ['ass', 'srt', 'both']. Default: ass
  -l, --lang {vi,en,ja,zh}
                        Language preset for OCR engine, text cleanup, font, and subtitle merging.
                        Choices: ['vi', 'en', 'ja', 'zh']. Default: vi
  --font FONT           Override default font for ASS subtitles (e.g. 'Arial', 'Meiryo', 'Microsoft YaHei').
```

### Subtitle & Image Input Options
```text
  -i, --video_dir VIDEO_DIR
                        Path to video, directory of videos, Blu-ray PGS (.sup),
                        DVD-SUP (.sup), or DVD VobSub (.sub / .idx).
  --sup SUP             Path to subtitle file (.sup), supporting both Blu-ray PGS and DVD-SUP.
                        Can also be passed via -i / --video_dir.
  --vobsub VOBSUB       Path to DVD VobSub file (.sub). Can also be passed via -i / --video_dir.
  --idx IDX             Path to DVD VobSub index file (.idx). Optional if located in the same directory as .sub.
  --ifo IFO             Path to DVD IFO file (.ifo) for DVD-SUP palette. Optional if located in the same directory as .sup.
  --stream_id STREAM_ID Stream ID for DVD VobSub when multiple languages exist. Default: auto.
  --forced_only         Only extract and OCR forced subtitles from PGS / DVD subtitle file. Default: False.
  --img_dir IMG_DIR     Path of directory containing extracted images (skips image extraction step).
```

### OCR Engine Settings
```text
  --ocr_engine {gglens,bing}
                        Select OCR engine. Choices: ['gglens', 'bing']. Default: gglens
  --gglens_thread GGLENS_THREAD
                        Google Lens OCR threads (e.g. 32-80 threads for 20-30 img/s). Default: 32
  --bing_thread BING_THREAD
                        Bing OCR threads. Default: 16
  --line_break_mode {y_cluster,line,paragraph}
                        Line break detection mode:
                        - 'y_cluster': Smart Y-coordinate clustering (recommended)
                        - 'line': Break on every line
                        - 'paragraph': Break only on paragraphs
                        Default: y_cluster
  --detect_italic / --no-detect_italic
                        Enable local detection and preservation of italic text (e.g. {\i1} in ASS, <i> in SRT).
                        Default: False
```

### VideoSubFinder Settings
```text
  -vsf, --vsf_exe_path VSF_EXE_PATH
                        Full path to VideoSubFinderWXW.exe (default: auto-detected from PATH).
  -gs, --general_settings GENERAL_SETTINGS
                        Path to general.cfg (default: auto-detected from [VSF PATH]/settings/general.cfg).
  --start_time START_TIME
                        Start time (format hour:min:sec:millisec, default: from general.cfg or video start).
  --end_time END_TIME   End time (format hour:min:sec:millisec, default: from general.cfg or video end).
  -te, --top_video_image_percent_end TOP_VIDEO_IMAGE_PERCENT_END
                        Top video image percent offset from image bottom (override general.cfg).
  -be, --bottom_video_image_percent_end BOTTOM_VIDEO_IMAGE_PERCENT_END
                        Bottom video image percent offset from image bottom (override general.cfg).
  -le, --left_video_image_percent_end LEFT_VIDEO_IMAGE_PERCENT_END
                        Left video image percent end (override general.cfg).
  -re, --right_video_image_percent_end RIGHT_VIDEO_IMAGE_PERCENT_END
                        Right video image percent end (override general.cfg).
  -nthr, --num_threads NUM_THREADS
                        Number of threads used for Run Search (override general.cfg).
  -nocrthr, --num_ocr_threads NUM_OCR_THREADS
                        Number of threads used for Create Cleared TXT Images (override general.cfg).
  --clear_dirs / --no-clear_dirs
                        Enable/disable clearing previous image folders before search. Default: True.
  --run_search / --no-run_search
                        Enable/disable Run Search (find hardsub frames). Default: True.
  --create_cleared_text_images / --no-create_cleared_text_images
                        Enable/disable Create Cleared Text Images. Default: False.
  --open_video_opencv / --no-open_video_opencv
                        Enable/disable opening video by OpenCV (default). Default: True.
  --open_video_ffmpeg / --no-open_video_ffmpeg
                        Enable/disable opening video by FFMPEG.
  --use_cuda / --no-use_cuda
                        Enable/disable CUDA GPU acceleration.
  --use_filter_color USE_FILTER_COLOR
                        Define filter color (e.g. "Lab: l:200-255 a:118-138 b:118-138").
  --moderate_threshold MODERATE_THRESHOLD
                        Define moderate_threshold (override general.cfg).
  --moderate_threshold_for_scaled_image MODERATE_THRESHOLD_FOR_SCALED_IMAGE
                        Define moderate_threshold_for_scaled_image [0.1 - moderate_threshold].
  --image_scale_for_clear_image IMAGE_SCALE_FOR_CLEAR_IMAGE
                        Define image scale for clear image (override general.cfg).
  --video_gamma VIDEO_GAMMA
                        Define video_gamma [0.0 - 1.0] (override general.cfg).
  --clear_txt_images_by_main_color / --no-clear_txt_images_by_main_color
                        Enable/disable clear txt images by main color.
  --use_ILA_images_for_getting_txt_symbols_areas / --no-use_ILA_images_for_getting_txt_symbols_areas
                        Enable/disable use ILA images for getting txt symbols areas.
  --use_ILA_images_before_clear_txt_images_from_borders / --no-use_ILA_images_before_clear_txt_images_from_borders
                        Enable/disable use ILA images before clear txt images from borders.
  --use_ILA_images_for_clear_txt_images / --no-use_ILA_images_for_clear_txt_images
                        Enable/disable use ILA images for clear txt images.
```

---

## Acknowledgements
- [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/)
- [owocr](https://github.com/AuroraWright/owocr)
- [BDSup2Sub](https://github.com/mjuhasz/BDSup2Sub)
- [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit)
