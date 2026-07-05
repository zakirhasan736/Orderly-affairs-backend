"""Remove broken leftover delete loops from section routers."""
from __future__ import annotations

import re
from pathlib import Path

SECTIONS = Path(__file__).resolve().parents[1] / "app" / "sections"

CLEANUP_BLOCK = re.compile(
    r"""
    \n\s*#\s*[^\n]*(?:DELETE|Delete)[^\n]*\n
    \s*def\s+cleanup_files\(obj\):.*?
    \s*cleanup_files\(raw_data\)\n
    """,
    re.DOTALL | re.VERBOSE,
)

EMPTY_DELETE_LOOP = re.compile(
    r"""
    \n\s*#\s*[^\n]*(?:DELETE|Delete)[^\n]*\n
    (?:\s+for\s+[^\n]+\n)+
    (?:\s+for\s+[^\n]+\n)*
    (?:\s+if\s+isinstance[^\n]+\n)?
    (?:\s+for\s+public_id\s+in[^\n]+:\n)
    """,
    re.DOTALL | re.VERBOSE,
)


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text

    text = CLEANUP_BLOCK.sub("\n", text)
    text = EMPTY_DELETE_LOOP.sub("\n", text)

    if "process_section_deleted_files" not in text:
        return False

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"fixed {path.relative_to(SECTIONS.parents[1])}")
        return True
    return False


def main() -> None:
    changed = 0
    for path in sorted(SECTIONS.glob("section*/router.py")):
        if fix_file(path):
            changed += 1
    print(f"done — {changed} file(s) updated")


if __name__ == "__main__":
    main()
