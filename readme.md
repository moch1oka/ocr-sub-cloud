# cloudocr-sub

Turns image-based subtitles into ASS/SRT text using cloud OCR. Based on TPN-Team's OCR tool. It takes:

- hardsubbed video (frames extracted with [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/))
- Blu-ray PGS `.sup`
- DVD-SUP `.sup` (optionally with the disc's `.ifo` for colors)
- DVD VobSub `.sub` + `.idx`
- a folder of already-extracted images

The text recognition is done by Google Lens (default) or Bing. Both are the unofficial web endpoints those services use, so every subtitle image is uploaded to Google or Microsoft, an internet connection is required, and things can break if they change their APIs.

PGS, DVD-SUP and VobSub are decoded in pure Python; VideoSubFinder is only needed for video.

## Setup

Python 3.12 or newer.

```bash
pip install -r requirements.txt
```

For hardsubbed video, also download VideoSubFinder and either put `VideoSubFinderWXW.exe` on your `PATH` or pass its path with `-vsf`. If there is a `settings/general.cfg` next to the exe, it's picked up automatically.

## Usage

```bash
# Blu-ray PGS or DVD-SUP (detected from the file header)
python run.py -i "movie.sup" -f both

# DVD-SUP with colors from the disc
python run.py --sup "movie.sup" --ifo "VTS_01_0.IFO"

# VobSub: pass the .sub or the .idx, the other one must have the same name
python run.py -i "movie.sub" -l en
python run.py --vobsub "movie.sub" --stream_id 1      # pick a stream when there are several

# Only forced subtitles (PGS / DVD)
python run.py -i "movie.sup" --forced_only

# Only part of the file, e.g. to try settings on a few minutes before running the whole thing
python run.py -i "movie.sup" --start_time 1:28:00 --end_time 1:31:00

# A folder of images, e.g. VideoSubFinder's RGBImages
python run.py --img_dir "path/to/RGBImages" -n ep01

# Hardsubbed video, or a folder of videos (searched recursively)
python run.py -i "ep01.mkv" -f both
python run.py -vsf "D:\VideoSubFinder\VideoSubFinderWXW.exe" -i "D:\season1"

# Use Bing instead of Google Lens
python run.py -i "movie.sup" --ocr_engine bing
```

### Where the output goes

- `--img_dir`: `outputs/<name>.ass`, where the name is `-n` or `output_subtitles`.
- Everything else: `outputs/<name>/<name>.ass`, where the name is `-n` or the input file's name. The extracted images are kept next to it (`PGS_Images/`, `DvdSup_Images/`, `VobSub_Images/` or `VSF_Results/`).
- `-n` is ignored when a folder contains more than one video.
- Existing files are never overwritten; a `_1`, `_2`, … suffix is added instead.
- Images that gave no text are left out of the subtitles and listed in `<name>_no_text.txt` next to them, so you can check them by hand.
- OCR results are cached in `.ocr_cache.json` in the output folder. Running the same command again (after a crash, Ctrl+C, or to try another output format) only sends images that aren't done yet. Images that gave no text are always retried. Use `--no-ocr_cache` to turn this off.

### Image file names

When you use `--img_dir`, the timing comes from the file names, in the format VideoSubFinder writes:

```
0_00_02_169__0_00_05_004_<anything>.jpeg       start 0:00:02.169, end 0:00:05.004
top_0_01_19_120__0_01_20_038_<anything>.png    same, shown at the top of the screen
```

Files whose names don't follow this pattern are skipped.

## Things worth knowing

- **Language (`-l`)** controls a few things: the language hint sent to the OCR service, which characters survive cleanup, the default ASS font (Arial for `vi`/`en`, Meiryo for `ja`, Microsoft YaHei for `zh`), and how aggressively near-identical neighbouring lines are merged. For `ja`/`zh` it also removes the stray spaces OCR puts between CJK characters. For VobSub it's also used to pick the stream when `--stream_id` isn't given.
- **Merging:** consecutive images with the same (or almost the same) text and less than 1 s between them become one subtitle line. This is what turns VideoSubFinder's many frames per line into a single entry.
- **Top/bottom:** subtitles in the upper 35% of the screen get the `Top` style in ASS and `{\an8}` in SRT. In PGS, a top sign and a bottom line shown at the same time become two separate entries.
- **Italics (`--detect_italic`)** are guessed locally from the slant of each text line, since neither OCR service reports font style. Off by default; check the results.
- **Line breaks (`--line_break_mode`):** `y_cluster` (default) groups the recognized lines into rows by their vertical position; `line` keeps every line the OCR service returns; `paragraph` joins each paragraph into one line.
- **Braces:** `{` and `}` start override tags in ASS, so any the OCR reads are written as `｛` `｝` in the `.ass` file (the `.srt` keeps them as they are).
- **Timing:** SRT keeps the exact milliseconds. ASS only has centiseconds, so times are rounded the way Aegisub does it.
- **DVD:** without an `.ifo`, DVD-SUP uses a generic palette, so colors may be off. Usually it doesn't matter for OCR. Subtitles with no end time get one from the next subtitle's start, capped at 5 s.

## Options

General:

| Option | Default | |
|---|---|---|
| `-i`, `--video_dir` | | Video, folder of videos, `.sup`, `.sub` or `.idx` |
| `--img_dir` | | Folder of images; skips extraction |
| `--sup` / `--vobsub` / `--idx` / `--ifo` | | Explicit paths, instead of `-i` |
| `--stream_id` | auto | VobSub stream to use |
| `--forced_only` | off | Only forced subtitles (PGS / DVD) |
| `--start_time`, `--end_time` | whole file | Only subtitles on screen in this range (`.sup`, `.sub`, video). Seconds, `M:SS`, `H:MM:SS`, `H:MM:SS.mmm` or `H:MM:SS:mmm` |
| `-o`, `--output_dir` | `outputs` | |
| `-n`, `--output_name` | input name | Base name of the output file |
| `-f`, `--format` | `ass` | `ass`, `srt` or `both` |
| `-l`, `--lang` | `vi` | `vi`, `en`, `ja`, `zh` |
| `--font` | per language | ASS font |

OCR:

| Option | Default | |
|---|---|---|
| `--ocr_engine` | `gglens` | `gglens` or `bing` |
| `--gglens_thread` | 32 | Parallel requests to Google Lens |
| `--bing_thread` | 32 | Parallel requests to Bing |
| `--line_break_mode` | `y_cluster` | `y_cluster`, `line`, `paragraph` |
| `--detect_italic` | off | |
| `--[no-]ocr_cache` | on | Reuse results of earlier runs |

The defaults can also be changed at the top of `run.py`.

VideoSubFinder. These are passed straight to VideoSubFinder, and anything you don't set comes from `general.cfg`:

| Option | |
|---|---|
| `-vsf`, `--vsf_exe_path` | Path to `VideoSubFinderWXW.exe` (default: from `PATH`) |
| `-gs`, `--general_settings` | Path to a `general.cfg` |
| `-te`, `-be`, `-le`, `-re` | Search area as a fraction of the frame (top/bottom measured from the bottom) |
| `-nthr`, `-nocrthr` | Threads for searching / for cleared text images |
| `--[no-]clear_dirs` | Clear previous images first (default on) |
| `--[no-]run_search` | Search for frames with text (default on) |
| `--[no-]create_cleared_text_images` | OCR the cleaned `TXTImages` instead of `RGBImages` (default off) |
| `--[no-]open_video_opencv`, `--[no-]open_video_ffmpeg` | Video decoder; OpenCV unless ffmpeg is asked for |
| `--[no-]use_cuda` | |
| `--use_filter_color`, `--moderate_threshold`, `--moderate_threshold_for_scaled_image`, `--image_scale_for_clear_image`, `--video_gamma`, `--[no-]clear_txt_images_by_main_color`, `--[no-]use_ILA_images_*` | VideoSubFinder's text-cleaning settings |

Recognized video extensions: `.mp4 .mkv .avi .mov .m4v .ts .m2ts .mts .webm .wmv .flv .mpg .mpeg`.

Run `python run.py -h` for the full list.

## Tests

```bash
# Offline unit tests
python -m unittest discover -s tests -v

# Real OCR on the sample images in test/, then check the result
python run.py --img_dir test -f both
python tests/check_output.py outputs/output_subtitles.ass outputs/output_subtitles.srt
```

## Credits

- [VideoSubFinder](https://sourceforge.net/projects/videosubfinder/)
- [owocr](https://github.com/AuroraWright/owocr)
- [BDSup2Sub](https://github.com/mjuhasz/BDSup2Sub)
- [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit)
