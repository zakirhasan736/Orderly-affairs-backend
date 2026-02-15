# app/sections/section9_charitable_giving/schemas.py

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


# ---------- Charity / Contribution ----------

class CharitableContribution(BaseModel):
    charity_name: Optional[str] = None
    cause_type: Optional[str] = None
    cause_type_other: Optional[str] = None

    contribution_type: Optional[str] = None
    contribution_type_other: Optional[str] = None

    contribution_amount: Optional[str] = None
    payment_method: Optional[str] = None

    account_info: Optional[UploadField] = None
    contact_details: Optional[UploadField] = None

    special_instructions: Optional[str] = None
    will_trust_provision: Optional[str] = None
    tax_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# { "9A": [ { charity }, { charity } ] }

class Section9CharitableGivingPayload(
    RootModel[Dict[str, List[CharitableContribution]]]
):
    pass
