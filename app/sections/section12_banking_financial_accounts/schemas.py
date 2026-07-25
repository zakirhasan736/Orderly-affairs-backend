from pydantic import BaseModel, RootModel
from typing import Dict, List, Optional, Union


# ---------- Upload Models ----------

class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    files: List[UploadedFile] = []
    _deleted_files: List[str] = []


# ---------- 12A — Bank Account ----------

class BankAccount(BaseModel):
    bank_name: Optional[str] = None
    account_type: Optional[str] = None
    account_type_other: Optional[str] = None

    account_number: Optional[UploadField] = None
    routing_number: Optional[str] = None
    account_purpose: Optional[str] = None

    joint_account_holders: Optional[str] = None
    beneficiaries: Optional[str] = None

    bank_contact: Optional[UploadField] = None

    online_banking: Optional[str] = None
    online_banking_password: Optional[str] = None

    cd_maturity_date: Optional[str] = None
    last_statement_date: Optional[str] = None

    automatic_payments: Optional[str] = None
    debit_cards: Optional[UploadField] = None
    safe_deposit_box: Optional[str] = None
    account_documents: Optional[UploadField] = None


# ---------- 12B — Digital Payment Account ----------

class DigitalPaymentAccount(BaseModel):
    service_name: Optional[str] = None
    service_name_other: Optional[str] = None

    account_email_phone: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    linked_accounts: Optional[str] = None
    account_balance: Optional[str] = None

    business_personal: Optional[str] = None
    regular_transactions: Optional[str] = None
    security_info: Optional[str] = None
    subscription_renewal_date: Optional[str] = None

    service_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# {
#   "12A": [BankAccount, ...],
#   "12B": [DigitalPaymentAccount, ...]
# }

Section12Item = Union[BankAccount, DigitalPaymentAccount]


class Section12BankingFinancialAccountsPayload(
    RootModel[Dict[str, List[Section12Item]]]
):
    pass
