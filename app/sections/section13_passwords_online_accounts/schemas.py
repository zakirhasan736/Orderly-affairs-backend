# app/sections/section13_passwords_online_accounts/schemas.py

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


# ---------- Online Account ----------

class OnlineAccount(BaseModel):
    account_type: Optional[str] = None
    account_type_other: Optional[str] = None

    service_name: Optional[str] = None
    account_username: Optional[str] = None
    account_password: Optional[str] = None

    email_associated: Optional[str] = None
    phone_associated: Optional[str] = None

    recovery_info: Optional[str] = None
    two_factor_auth: Optional[str] = None

    account_value: Optional[str] = None
    closure_instructions: Optional[str] = None

    account_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# {
#   "13A": [ { online account }, ... ]
# }

class Section13PasswordsOnlineAccountsPayload(
    RootModel[Dict[str, List[OnlineAccount]]]
):
    pass
