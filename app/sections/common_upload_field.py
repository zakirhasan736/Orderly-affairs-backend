from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Any


class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1
    model_config = {"extra": "ignore"}


class UploadField(BaseModel):
    """
    Shared shape for TextInputWithUpload fields.
    Must keep `text` — AI autofill and the UI store the typed value here.
    Schemas that omit `text` silently drop policy numbers / notes on save.
    """

    text: Optional[str] = None
    files: List[UploadedFile] = Field(default_factory=list)
    deleted_files: List[str] = Field(default_factory=list, alias="_deleted_files")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def coerce_string_or_object(cls, value: Any):
        if value is None or value == "":
            return None
        if isinstance(value, str) or isinstance(value, (int, float, bool)):
            return {"text": str(value), "files": []}
        if isinstance(value, dict):
            data = dict(value)
            if "_deleted_files" in data and "deleted_files" not in data:
                data["deleted_files"] = data.pop("_deleted_files")
            # Plain AI string mistakenly nested
            if "text" not in data and isinstance(data.get("value"), str):
                data["text"] = data["value"]
            return data
        return value
