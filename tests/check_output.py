"""
Checks subtitles produced from the sample images in test/ (used by CI after a live OCR run).
Usage: python tests/check_output.py <file.ass|file.srt> [...]
"""

import sys
from pathlib import Path

# Words that any decent OCR of the sample images must find (6 images -> 6 subtitle entries)
EXPECTED_ENTRIES = 6
EXPECTED_SNIPPETS = ["bầu trời", "Contractor", "Mai-san", "đi lạc", "Không phải"]


def check(path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path} not found"]
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".ass":
        count = sum(1 for line in text.splitlines() if line.startswith("Dialogue:"))
    else:
        count = sum(1 for line in text.splitlines() if "-->" in line)

    errors = []
    if count != EXPECTED_ENTRIES:
        errors.append(f"{path}: expected {EXPECTED_ENTRIES} entries, got {count}")
    errors += [f"{path}: missing '{s}'" for s in EXPECTED_SNIPPETS if s not in text]
    return errors


if __name__ == "__main__":
    errors = [e for arg in sys.argv[1:] for e in check(Path(arg))]
    for e in errors:
        print(e)
    if errors:
        sys.exit(1)
    print("Output OK")
