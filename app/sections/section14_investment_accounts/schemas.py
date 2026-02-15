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


# ---------- 14A — Investment Account ----------

class InvestmentAccount(BaseModel):
    account_type: Optional[str] = None
    account_type_other: Optional[str] = None

    financial_institution: Optional[str] = None
    account_number: Optional[UploadField] = None
    account_value: Optional[str] = None

    beneficiaries: Optional[str] = None
    advisor_contact: Optional[UploadField] = None
    employer_connection: Optional[str] = None

    login_credentials: Optional[str] = None
    distribution_instructions: Optional[str] = None

    account_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# {
#   "14A": [ { investment account }, ... ]
# }

class Section14InvestmentAccountsPayload(
    RootModel[Dict[str, List[InvestmentAccount]]]
):
    pass
