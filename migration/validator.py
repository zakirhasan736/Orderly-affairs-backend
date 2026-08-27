from __future__ import annotations

from .manifests import load_manifest, verify_local_media


def validate_slug_export(manifest_path, root):
    manifest = load_manifest(manifest_path)
    return verify_local_media(manifest, root)
