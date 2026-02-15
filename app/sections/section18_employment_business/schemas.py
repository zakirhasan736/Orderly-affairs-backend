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


# ---------- 18A — Current Employment (NON-REPEATABLE) ----------

class CurrentEmployment(BaseModel):
    employment_status: Optional[str] = None
    employer_name: Optional[str] = None
    job_title: Optional[str] = None
    work_address: Optional[str] = None
    work_phone: Optional[str] = None
    supervisor_hr: Optional[UploadField] = None
    employee_id: Optional[str] = None
    start_date: Optional[str] = None
    salary_wage: Optional[str] = None
    benefits: Optional[str] = None
    vacation_sick_time: Optional[str] = None
    work_equipment: Optional[str] = None
    employment_documents: Optional[UploadField] = None


# ---------- 18B — Business Ownership (REPEATABLE) ----------

class BusinessOwnership(BaseModel):
    business_name: Optional[str] = None
    business_type: Optional[str] = None
    business_type_other: Optional[str] = None
    business_address: Optional[str] = None
    business_phone: Optional[str] = None
    tax_id: Optional[str] = None
    business_description: Optional[str] = None
    ownership_percentage: Optional[str] = None
    business_partners: Optional[str] = None
    key_employees: Optional[str] = None
    succession_plan: Optional[str] = None
    business_attorney: Optional[UploadField] = None
    business_accounts: Optional[str] = None
    business_documents: Optional[UploadField] = None


# ---------- 18C — Past Employment (REPEATABLE) ----------

class PastEmployment(BaseModel):
    employer_name: Optional[str] = None
    job_title: Optional[str] = None
    employment_dates: Optional[str] = None
    job_description: Optional[str] = None
    employer_address: Optional[str] = None
    supervisor_contact: Optional[UploadField] = None
    reason_for_leaving: Optional[str] = None
    achievements: Optional[str] = None
    employment_documents: Optional[UploadField] = None


# ---------- 18D — Income Sources (REPEATABLE) ----------

class IncomeSource(BaseModel):
    income_type: Optional[str] = None
    income_type_other: Optional[str] = None
    income_source: Optional[str] = None
    income_amount: Optional[str] = None
    payment_method: Optional[str] = None
    tax_withholding: Optional[str] = None
    income_contact: Optional[UploadField] = None
    income_documents: Optional[UploadField] = None


# ---------- Root Payload ----------
# {
#   "18A": {...},
#   "18B": [ {...} ],
#   "18C": [ {...} ],
#   "18D": [ {...} ]
# }

class Section18EmploymentBusinessPayload(
    RootModel[Dict[str, object]]
):
    pass
