"""Remove legacy delete_file loops from section routers (ownership via process_section_deleted_files)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "sections"

LOOP_PATTERN = re.compile(
    r"\n    # [^\n]*\n"
    r"(?:    (?:for |if ).*\n)*"
    r"(?:        .*\n)*?"
    r"                    delete_file\([^\)]+\)\n",
    re.MULTILINE,
)

ALT_PATTERN = re.compile(
    r"\n    # [^\n]*DELETE[^\n]*\n(?:    .*\n)*?delete_file\([^\)]+\)\n",
    re.MULTILINE | re.IGNORECASE,
)

for path in sorted(ROOT.glob("**/router.py")):
    text = path.read_text(encoding="utf-8")
    orig = text
    text = LOOP_PATTERN.sub("\n", text)
    text = ALT_PATTERN.sub("\n", text)
    # drop orphan delete_file lines
    text = re.sub(r"\n\s+delete_file\([^\)]+\)\n", "\n", text)
    if text != orig:
        path.write_text(text, encoding="utf-8")
        print("cleaned", path.name)
