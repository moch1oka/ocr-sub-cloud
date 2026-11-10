"""
Checks subtitles produced from the sample images in test/ (used by CI after a live OCR run).
Usage: python tests/check_output.py [--allow-refused] <file.ass|file.srt> [...]

--allow-refused: a file with no entries at all passes with a warning. Use it for a service that is
known to turn away CI servers (Bing); a run that returns text must still be complete and correct.
"""

import sys
from pathlib import Path

# Words that any decent OCR of the sample images must find (6 images -> 6 subtitle entries)
EXPECTED_ENTRIES = 6
EXPECTED_SNIPPETS = ["bầu trời", "Contractor", "Mai-san", "đi lạc", "Không phải"]


def count_entries(path: Path, text: str) -> int:
    if path.suffix == ".ass":
        return sum(1 for line in text.splitlines() if line.startswith("Dialogue:"))
    return sum(1 for line in text.splitlines() if "-->" in line)


def check(path: Path, allow_refused: bool = False) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) for one subtitle file."""
    if not path.is_file():
        return [f"{path} not found"], []
    text = path.read_text(encoding="utf-8")
    count = count_entries(path, text)

    if count == 0 and allow_refused:
        return [], [f"{path}: the OCR service returned nothing for any image (refused this network?), not checked"]

    errors = []
    if count != EXPECTED_ENTRIES:
        errors.append(f"{path}: expected {EXPECTED_ENTRIES} entries, got {count}")
    errors += [f"{path}: missing '{s}'" for s in EXPECTED_SNIPPETS if s not in text]
    return errors, []


if __name__ == "__main__":
    args = sys.argv[1:]
    allow_refused = "--allow-refused" in args
    errors, warnings = [], []
    for arg in args:
        if arg != "--allow-refused":
            file_errors, file_warnings = check(Path(arg), allow_refused)
            errors += file_errors
            warnings += file_warnings

    for w in warnings:
        print(f"::warning::{w}")  # shown as an annotation on the GitHub Actions run
    for e in errors:
        print(e)
    if errors:
        sys.exit(1)
    print("Output OK" if not warnings else "Output not checked (see warning)")
