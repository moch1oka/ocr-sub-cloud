# TPN-Team's OCR tool

A tool to extract and OCR subtitles from video files or extracted images using VideoSubFinder and Google Lens, generating Aegisub-compliant ASS subtitle files.

## Features
- **OCR Engine**: Google Lens with HTTP/2 session reuse and Chrome impersonation (`curl_cffi`).
- **Smart Line Breaks**: Geometry Y-clustering (`y_cluster`) accurately reconstructs multi-line dialogue layouts without incorrect breaks.
- **Aegisub-Standard Timing**: Centisecond rounding matching Aegisub's timecode algorithm (`(total_ms + 5) // 10`).

## Setup

### Step 1: Install Python
Ensure Python 3.10+ is installed.

### Step 2: Install Python Libraries
```bash
pip install -r requirements.txt
```

### Step 3: (Optional) Install VideoSubFinder
If extracting subtitles directly from video files, download [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/).

## Usage

### 1. OCR from Extracted Images Directory
If you already have extracted images (e.g. from VideoSubFinder):
```sh
python run.py --img_dir "path/to/images" -o outputs
```

### 2. VideoSubFinder Method
Extract frames directly from video file/folder and OCR:
```sh
python run.py -i "path/to/video.mp4" -o outputs
```
If `VideoSubFinderWXW.exe` is not in your system PATH, specify its path:
```sh
python run.py -vsf "path/to/VideoSubFinderWXW.exe" -i "path/to/video.mp4" -o outputs
```

### 3. OCR Engine Settings
```sh
OCR Engine Settings:
  --ocr_engine OCR_ENGINE
                        Select OCR engine. Choices: ['gglens']. Default: gglens
  --gglens_thread GGLENS_THREAD
                        Google Lens OCR threads. Default: 16
  --line_break_mode {y_cluster,line,paragraph}
                        Line break detection mode: 'y_cluster' (smart Y-coordinate clustering, recommended),
                        'line' (break on every line), 'paragraph' (break only on paragraphs). Default: y_cluster
```

## Acknowledgement
- [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/)
- [owocr](https://github.com/AuroraWright/owocr)

