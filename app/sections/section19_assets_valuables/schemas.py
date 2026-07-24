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


# ---------- 19A — Valuable Items (REPEATABLE) ----------

class ValuableItem(BaseModel):
    item_type: Optional[str] = None
    item_type_other: Optional[str] = None
    item_description: Optional[str] = None
    estimated_value: Optional[str] = None
    purchase_info: Optional[str] = None
    current_location: Optional[str] = None
    insurance_info: Optional[str] = None
    appraisal_info: Optional[UploadField] = None
    intended_recipient: Optional[str] = None
    care_instructions: Optional[str] = None
    item_history: Optional[str] = None
    item_documents: Optional[UploadField] = None


# ---------- 19B — Real Estate Properties (REPEATABLE) ----------

class RealEstateProperty(BaseModel):
    property_type: Optional[str] = None
    property_type_other: Optional[str] = None
    property_address: Optional[str] = None
    property_description: Optional[str] = None
    ownership_details: Optional[str] = None
    purchase_info: Optional[str] = None
    current_value: Optional[str] = None
    mortgage_info: Optional[str] = None
    mortgage_maturity_date: Optional[str] = None
    rental_info: Optional[str] = None
    property_manager: Optional[UploadField] = None
    property_taxes: Optional[UploadField] = None
    property_tax_due_date: Optional[str] = None
    insurance_info: Optional[UploadField] = None
    intended_disposition: Optional[str] = None
    property_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# {
#   "19A": [ {...} ],
#   "19B": [ {...} ]
# }

class Section19AssetsValuablesPayload(
    RootModel[Dict[str, object]]
):
    pass
