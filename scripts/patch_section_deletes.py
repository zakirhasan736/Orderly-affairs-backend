"""Patch section routers to use process_section_deleted_files with ownership checks."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "sections"

for path in sorted(ROOT.glob("**/router.py")):
    text = path.read_text(encoding="utf-8")
    if "delete_file(" not in text:
        continue
    orig = text

    text = text.replace(
        "from app.security.cloudinary_service import delete_file",
        "from app.security.section_file_cleanup import process_section_deleted_files",
    )

    text = re.sub(
        r"\n    # [^\n]*DELETE[^\n]*\n(?:    .*\n)*?(?=    # |\n    (?:encrypted|await SectionRepository|process_section))",
        "\n",
        text,
    )

    if "process_section_deleted_files(data" not in text:
        for marker in (
            "    encrypted_payload = encrypt_section_data",
            "    # ENCRYPT DATA",
            "    # encrypt",
        ):
            if marker in text:
                insert = "    process_section_deleted_files(data, owner['email'])\n\n"
                if insert.strip() not in text:
                    text = text.replace(marker, insert + marker, 1)
                break

    if text != orig:
        path.write_text(text, encoding="utf-8")
        print(f"patched {path.relative_to(ROOT.parents[1])}")
