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


# ---------- 16A — Credit Cards (REPEATABLE) ----------

class CreditCard(BaseModel):
    card_name: Optional[str] = None
    card_type: Optional[str] = None
    card_type_other: Optional[str] = None

    card_number: Optional[str] = None
    account_number: Optional[UploadField] = None

    credit_limit: Optional[str] = None
    current_balance: Optional[str] = None
    monthly_payment: Optional[str] = None

    autopay_setup: Optional[str] = None
    card_benefits: Optional[str] = None

    customer_service: Optional[UploadField] = None
    online_account: Optional[str] = None
    authorized_users: Optional[str] = None

    card_documents: Optional[UploadField] = None


# ---------- 16B — Other Debts (REPEATABLE) ----------

class Debt(BaseModel):
    debt_type: Optional[str] = None
    debt_type_other: Optional[str] = None

    creditor_name: Optional[str] = None
    account_number: Optional[UploadField] = None

    current_balance: Optional[str] = None
    monthly_payment: Optional[str] = None
    payment_due_date: Optional[str] = None
    next_payment_due_date: Optional[str] = None
    loan_maturity_date: Optional[str] = None
    interest_rate: Optional[str] = None

    payment_method: Optional[str] = None
    cosigners: Optional[str] = None
    collateral: Optional[str] = None

    creditor_contact: Optional[UploadField] = None
    debt_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# {
#   "16A": [ { credit card }, ... ],
#   "16B": [ { debt }, ... ]
# }

class Section16CreditCardsDebtPayload(
    RootModel[Dict[str, object]]
):
    pass
