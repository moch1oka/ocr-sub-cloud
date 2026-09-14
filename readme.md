# TPN-Team's OCR tool

A tool to extract and OCR subtitles from video files or extracted images using **VideoSubFinder**, **Google Lens**, and **Bing Visual Search OCR**, generating Aegisub-compliant ASS subtitle files.

## Features

- **Multiple Cloud OCR Engines**: Google Lens and Bing Visual Search OCR with high-speed multithreading.
- **Language Presets (`-l / --lang {vi, en, ja, zh}`)**:
  - Automatically tunes OCR engine requests, character whitelisting, and subtitle styles.
  - **Japanese (`ja`) & Chinese (`zh`)**: Removes accidental OCR spaces between CJK characters, preserves full-width CJK punctuation without inserting unwanted ASCII spaces, and sets native CJK ASS subtitle fonts (`Meiryo` / `Microsoft YaHei`).
  - **Vietnamese (`vi`) & English (`en`)**: Preserves Vietnamese diacritics, normalizes quotes, and standardizes spacing around punctuation.
- **Smart Line Breaks & Layout**: Geometry Y-coordinate clustering (`y_cluster`) accurately reconstructs multi-line dialogue layouts without arbitrary breaks.
- **Intelligent Subtitle Merging**: Fuzzy SequenceMatcher merges consecutive frames of the same utterance even when minor OCR artifacts (missing punctuation, character variations) occur.
- **Dedicated Dual Export Formats**:
  - `.srt`: Uses exact 3-digit millisecond timecodes directly from image filenames (`HH:MM:SS,mmm`) with `{\an8}` top-subtitle support.
  - `.ass`: Aegisub-compliant half-up centisecond rounding (`H:MM:SS.cc`) with language-optimized fonts and styles.
  - Output `.ass`, `.srt`, or `both` simultaneously.
- **Seamless VideoSubFinder Integration**: Automatically detects and loads `settings/general.cfg` located next to `VideoSubFinderWXW.exe`, fully preserving user settings.

---

## Setup

### Step 1: Install Python
Ensure Python 3.12+ is installed.

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: VideoSubFinder
If extracting subtitles directly from video files, download [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/). You can either place `VideoSubFinderWXW.exe` in your system `PATH` or pass its path via `-vsf`.

---

## Usage

### 1. OCR from Extracted Images Directory
If you already have extracted images (e.g. from VideoSubFinder's `RGBImages` folder):

```bash
# Using Google Lens (default)
python run.py --img_dir "path/to/RGBImages" -o outputs -n my_subtitles

# Using Bing OCR
python run.py --img_dir "path/to/RGBImages" -o outputs -n my_subtitles --ocr_engine bing
```

### 2. Full Pipeline from Video File
Extract frames with VideoSubFinder and OCR in one command:

```bash
# VideoSubFinder auto-detects its general.cfg next to VideoSubFinderWXW.exe
python run.py -i "path/to/video.mp4" -o outputs

# If VideoSubFinder is not in PATH, specify with -vsf:
python run.py -vsf "E:\VideoSubFinder_6.10_x64\Release_x64\VideoSubFinderWXW.exe" -i "path/to/video.mp4" -o outputs --ocr_engine bing
```

---

## CLI Options

### OCR Engine Settings
```text
  --ocr_engine {gglens,bing}
                        Select OCR engine. Choices: ['gglens', 'bing']. Default: gglens
  -f, --format {ass,srt,both}
                        Subtitle output format. Choices: ['ass', 'srt', 'both']. Default: ass
  -l, --lang {vi,en,ja,zh}
                        Language preset for OCR engine, text cleanup, font, and merging. Default: vi
  --font FONT           Override default font for ASS subtitles (e.g. 'Arial', 'Meiryo', 'Microsoft YaHei').
  --gglens_thread GGLENS_THREAD
                        Google Lens OCR threads. Default: 16
  --bing_thread BING_THREAD
                        Bing OCR threads. Default: 16
  --line_break_mode {y_cluster,line,paragraph}
                        Line break detection mode:
                        - 'y_cluster': Smart Y-coordinate clustering (recommended)
                        - 'line': Break on every line
                        - 'paragraph': Break only on paragraphs
                        Default: y_cluster
```

### VideoSubFinder Settings
```text
  -vsf, --vsf_exe_path VSF_EXE_PATH
                        Full path to VideoSubFinderWXW.exe (default: auto-detected from PATH or Scoop).
  -i, --video_dir VIDEO_DIR
                        Path to video file or directory of videos.
  -gs, --general_settings GENERAL_SETTINGS
                        Path to general.cfg (default: auto-detected from [VSF PATH]/settings/general.cfg).
  --clear_dirs / --no-clear_dirs
                        Clear previous image folders before search. Default: True.
  --run_search / --no-run_search
                        Run search for hardcoded text. Default: True.
  --open_video_opencv / --open_video_ffmpeg
                        Video open backend. Default: OpenCV.
  -te, -be, -le, -re    Top/Bottom/Left/Right crop percentage offsets (overrides general.cfg if specified).
  --use_filter_color USE_FILTER_COLOR
                        Override filter color (e.g. "Lab: l:200-255 a:118-138 b:118-138").
```

---

## Acknowledgement
- [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/)
- [owocr](https://github.com/AuroraWright/owocr)
