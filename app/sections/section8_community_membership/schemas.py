# app/sections/section8_community_membership/schemas.py

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


# ---------- Community Group ----------

class CommunityGroup(BaseModel):
    organization_name: Optional[str] = None
    organization_type: Optional[str] = None
    organization_type_other: Optional[str] = None

    membership_details: Optional[str] = None
    contact_info: Optional[UploadField] = None
    importance: Optional[str] = None
    notify_instructions: Optional[str] = None
    documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# { "8A": [ { group }, { group } ] }

class Section8CommunityMembershipPayload(
    RootModel[Dict[str, List[CommunityGroup]]]
):
    pass
