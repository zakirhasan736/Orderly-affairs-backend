from pydantic import BaseModel, RootModel, field_validator
from typing import Dict, List, Optional, Union

from app.sections.common_upload_field import UploadField


UploadValue = Union[str, UploadField, None]


class InsurancePolicy(BaseModel):
    policy_type: Optional[str] = None
    policy_type_other: Optional[str] = None

    policy_documents_life: UploadValue = None
    policy_company: Optional[str] = None
    policy_number: UploadValue = None
    policy_expiry: Optional[str] = None
    coverage_amount: Optional[str] = None
    beneficiaries: Optional[str] = None
    policy_contact: UploadValue = None
    premium_info: Optional[str] = None
    policy_documents: UploadValue = None
    notes: Optional[str] = None
    # Health / dental / medical card fields
    member_name: Optional[str] = None
    member_id: Optional[str] = None
    group_number: Optional[str] = None
    plan_name: Optional[str] = None
    covered_relationship: Optional[str] = None
    rx_bin: Optional[str] = None
    rx_pcn: Optional[str] = None
    rx_grp: Optional[str] = None
    payer_id: Optional[str] = None
    pharmacy_benefit_manager: Optional[str] = None
    benefit_summary: Optional[str] = None
    # Emails selected for expiry reminders (owner + immediate-access people).
    # None = default all; [] = nobody; list of emails = explicit selection.
    reminder_recipients: Optional[List[str]] = None

    @field_validator(
        "policy_documents_life",
        "policy_number",
        "policy_contact",
        "policy_documents",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, v):
        if v == "":
            return None
        # Prefer structured upload shape so `text` survives round-trips.
        if isinstance(v, str):
            return {"text": v, "files": []}
        return v


class Section7InsurancePoliciesPayload(
    RootModel[Dict[str, List[InsurancePolicy]]]
):
    pass
