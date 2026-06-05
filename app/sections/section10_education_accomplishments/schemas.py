# app/sections/section10_education_accomplishments/schemas.py

from pydantic import BaseModel, RootModel
from typing import Dict, List, Optional


# ---------- Upload Models ----------

class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    text: Optional[str] = None
    files: List[UploadedFile] = []
    _deleted_files: List[str] = []


# ---------- Education Item ----------

class EducationItem(BaseModel):
    institution_name: Optional[str] = None
    degree_type: Optional[str] = None
    degree_type_other: Optional[str] = None

    field_of_study: Optional[str] = None
    graduation_year: Optional[str] = None
    honors_awards: Optional[str] = None

    documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# { "10A": [ { education }, { education } ] }

class Section10EducationAccomplishmentsPayload(
    RootModel[Dict[str, List[EducationItem]]]
):
    pass
