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


# ---------- 15A — Health Insurance & Medical Info (NON-REPEATABLE) ----------

class HealthOverview(BaseModel):
    primary_health_insurance: Optional[UploadField] = None
    secondary_health_insurance: Optional[UploadField] = None
    medicare_medicaid: Optional[UploadField] = None

    current_conditions: Optional[str] = None
    allergies: Optional[str] = None
    current_medications: Optional[UploadField] = None
    medical_devices: Optional[str] = None

    emergency_contact_1: Optional[str] = None
    emergency_contact_2: Optional[str] = None

    medical_power_of_attorney: Optional[UploadField] = None


# ---------- 15B — Healthcare Provider (REPEATABLE) ----------

class HealthcareProvider(BaseModel):
    provider_name: Optional[str] = None
    specialty: Optional[str] = None
    doctor_name: Optional[str] = None

    contact_info: Optional[UploadField] = None
    patient_id: Optional[str] = None
    frequency: Optional[str] = None
    last_visit: Optional[str] = None

    conditions_treated: Optional[str] = None
    insurance_accepted: Optional[str] = None
    portal_access: Optional[str] = None
    important_notes: Optional[str] = None


# ---------- Root Payload ----------
# {
#   "15A": { ... },
#   "15B": [ { provider }, ... ]
# }

class Section15HealthInformationPayload(
    RootModel[Dict[str, object]]
):
    pass
