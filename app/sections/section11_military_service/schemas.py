# app/sections/section11_military_service/schemas.py

from pydantic import BaseModel, RootModel
from typing import Dict, List, Optional


# ---------- Upload Models ----------

class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    files: List[UploadedFile] = []
    _deleted_files: List[str] = []


# ---------- Military Service Record ----------

class MilitaryServiceRecord(BaseModel):
    branch_of_service: Optional[str] = None
    branch_of_service_other: Optional[str] = None

    service_dates: Optional[str] = None
    rank_achieved: Optional[str] = None
    military_occupational_specialty: Optional[str] = None
    deployments: Optional[str] = None

    combat_service: Optional[str] = None
    awards_decorations: Optional[str] = None
    discharge_type: Optional[str] = None
    va_benefits: Optional[str] = None

    military_documents: Optional[UploadField] = None
    burial_preferences: Optional[str] = None
    veteran_contacts: Optional[UploadField] = None


# ---------- Root Payload ----------
# { "11A": [ { service }, { service } ] }

class Section11MilitaryServicePayload(
    RootModel[Dict[str, List[MilitaryServiceRecord]]]
):
    pass
