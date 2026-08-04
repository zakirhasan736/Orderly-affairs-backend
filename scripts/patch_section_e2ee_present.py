"""One-shot: patch section routers to return E2EE-aware payloads."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "app" / "sections"

OLD_BLOCKS = [
    """    decrypted = decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"])

    return {
        "section_key": SECTION_KEY,
        "data": decrypted,
    }""",
    """    return {
        "section_key": SECTION_KEY,
        "data": decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"]),
    }""",
]
NEW = "    return present_section_for_api(owner_id, SECTION_ID, SECTION_KEY, section)"
IMPORT_OLD = "from app.security.section_crypto import encrypt_section_data, decrypt_section_data"
IMPORT_NEW = (
    "from app.security.section_crypto import encrypt_section_data, decrypt_section_data\n"
    "from app.security.section_e2ee import present_section_for_api"
)


def main() -> None:
    for path in sorted(ROOT.glob("*/router.py")):
        text = path.read_text(encoding="utf-8")
        if "present_section_for_api" in text and "return present_section_for_api" in text:
            print("skip", path.parent.name)
            continue
        if IMPORT_OLD in text and "present_section_for_api" not in text:
            text = text.replace(IMPORT_OLD, IMPORT_NEW)
        for block in OLD_BLOCKS:
            if block in text:
                text = text.replace(block, NEW)
        path.write_text(text, encoding="utf-8")
        leftover = 'decrypt_section_data(owner_id, SECTION_ID, section["encrypted_data"])' in text
        print(("PARTIAL" if leftover else "OK"), path.parent.name)


if __name__ == "__main__":
    main()
