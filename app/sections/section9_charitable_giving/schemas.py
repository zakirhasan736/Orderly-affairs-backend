# app/sections/section9_charitable_giving/schemas.py

from pydantic import BaseModel, RootModel, field_validator
from typing import Dict, List, Optional, Union

from app.sections.common_upload_field import UploadField


UploadValue = Union[str, UploadField, None]


class CharitableContribution(BaseModel):
    charity_name: Optional[str] = None
    cause_type: Optional[str] = None
    cause_type_other: Optional[str] = None

    contribution_type: Optional[str] = None
    contribution_type_other: Optional[str] = None

    contribution_amount: Optional[str] = None
    payment_method: Optional[str] = None

    # AI often returns plain strings for these TextInputWithUpload fields.
    account_info: UploadValue = None
    contact_details: UploadValue = None

    special_instructions: Optional[str] = None
    will_trust_provision: Optional[str] = None
    tax_documents: UploadValue = None

    @field_validator(
        "account_info",
        "contact_details",
        "tax_documents",
        mode="before",
    )
    @classmethod
    def coerce_upload_fields(cls, v):
        if v == "" or v is None:
            return None
        if isinstance(v, str) or isinstance(v, (int, float, bool)):
            return {"text": str(v), "files": []}
        return v


# ---------- Root Payload ----------
# { "9A": [ { charity }, { charity } ] }

class Section9CharitableGivingPayload(
    RootModel[Dict[str, List[CharitableContribution]]]
):
    pass
