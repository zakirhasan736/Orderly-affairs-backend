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
    model_config = {"extra": "ignore"}


# ---------- 20A — Personal Legal Documents ----------

class Section20A(BaseModel):
    birth_certificate: Optional[UploadField] = None
    social_security_card: Optional[UploadField] = None
    passport: Optional[UploadField] = None
    drivers_license: Optional[UploadField] = None
    marriage_certificate: Optional[UploadField] = None
    divorce_decree: Optional[UploadField] = None
    name_change_documents: Optional[UploadField] = None
    naturalization_certificate: Optional[UploadField] = None
    immigration_documents: Optional[UploadField] = None
    children_birth_certificates: Optional[UploadField] = None
    adoption_documents: Optional[UploadField] = None
    custody_agreements: Optional[UploadField] = None


# ---------- 20B — Tax Documents ----------

class Section20B(BaseModel):
    current_tax_year: Optional[UploadField] = None
    previous_tax_years: Optional[UploadField] = None
    tax_preparer_info: Optional[UploadField] = None
    tax_software: Optional[str] = None
    business_tax_documents: Optional[UploadField] = None
    estimated_tax_payments: Optional[str] = None
    tax_filing_deadline: Optional[str] = None
    tax_debt_issues: Optional[UploadField] = None


# ---------- 20C — Other Important Documents (REPEATABLE) ----------

class OtherLegalDocument(BaseModel):
    document_type: Optional[str] = None
    document_description: Optional[str] = None
    parties_involved: Optional[str] = None
    important_dates: Optional[str] = None
    expiration_date: Optional[str] = None
    document_location: Optional[str] = None
    renewal_requirements: Optional[str] = None
    contact_information: Optional[UploadField] = None
    document_upload: Optional[UploadField] = None


# ---------- ROOT PAYLOAD ----------

class Section20LegalDocumentsPayload(
    RootModel[Dict[str, object]]
):
    pass
