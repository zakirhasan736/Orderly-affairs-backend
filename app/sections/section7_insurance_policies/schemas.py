from pydantic import BaseModel, RootModel, Field, field_validator
from typing import Dict, List, Optional, Union


class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    files: List[UploadedFile] = Field(default_factory=list)
    deleted_files: List[str] = Field(default_factory=list, alias="_deleted_files")

    class Config:
        populate_by_name = True


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
    # Emails selected for expiry reminders (owner + immediate-access people).
    # None = default all; [] = nobody; list of emails = explicit selection.
    reminder_recipients: Optional[List[str]] = None

    # 🔥 normalize "" → None
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
        return v


class Section7InsurancePoliciesPayload(
    RootModel[Dict[str, List[InsurancePolicy]]]
):
    pass
