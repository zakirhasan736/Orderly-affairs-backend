# app/security/file_validation.py

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "application/pdf",
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def validate_upload(file):
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise ValueError("Unsupported file type")

    file.file.seek(0, 2)  # move to end
    size = file.file.tell()
    file.file.seek(0)

    if size > MAX_FILE_SIZE:
        raise ValueError("File too large (max 10MB)")
