from pydantic import BaseModel, RootModel
from typing import Dict, Optional, List


# ---------- Upload Models ----------

class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    files: List[UploadedFile] = []
    _deleted_files: List[str] = []


# ---------- 21A — Estate Planning Documents ----------

class Section21A(BaseModel):
    will_location: Optional[UploadField] = None
    will_date: Optional[str] = None
    executor_info: Optional[str] = None
    alternate_executor: Optional[str] = None
    will_attorney: Optional[UploadField] = None

    trust_info: Optional[UploadField] = None
    trustee_info: Optional[str] = None
    successor_trustee: Optional[str] = None
    trust_attorney: Optional[UploadField] = None

    financial_poa: Optional[UploadField] = None
    medical_poa: Optional[UploadField] = None

    living_will: Optional[UploadField] = None
    dnr_orders: Optional[UploadField] = None
    organ_donation: Optional[str] = None

    primary_beneficiaries: Optional[str] = None
    contingent_beneficiaries: Optional[str] = None
    special_bequests: Optional[str] = None
    charitable_bequests: Optional[str] = None


# ---------- 21B — Final Arrangements & Wishes ----------

class Section21B(BaseModel):
    funeral_type: Optional[str] = None
    funeral_type_other: Optional[str] = None
    service_location: Optional[str] = None
    funeral_home: Optional[UploadField] = None
    clergy_officiant: Optional[UploadField] = None
    service_preferences: Optional[str] = None

    disposition_type: Optional[str] = None
    disposition_type_other: Optional[str] = None
    burial_location: Optional[UploadField] = None
    cremation_preferences: Optional[str] = None
    body_donation_info: Optional[UploadField] = None

    headstone_marker: Optional[str] = None
    memorial_donations: Optional[str] = None
    special_requests: Optional[str] = None

    obituary_details: Optional[str] = None
    photo_for_obituary: Optional[UploadField] = None

    prepaid_funeral: Optional[UploadField] = None
    cemetery_plot: Optional[UploadField] = None
    funeral_insurance: Optional[UploadField] = None


# ---------- 21C — Guardianship Arrangements ----------

class Section21C(BaseModel):
    minor_children_info: Optional[str] = None

    primary_guardian_name: Optional[str] = None
    primary_guardian_relationship: Optional[str] = None
    primary_guardian_contact: Optional[UploadField] = None
    primary_guardian_consent: Optional[str] = None

    alternate_guardian_name: Optional[str] = None
    alternate_guardian_relationship: Optional[str] = None
    alternate_guardian_contact: Optional[UploadField] = None
    alternate_guardian_consent: Optional[str] = None

    parenting_philosophy: Optional[str] = None
    education_preferences: Optional[str] = None
    religious_preferences: Optional[str] = None
    healthcare_instructions: Optional[str] = None
    special_needs: Optional[str] = None
    extracurricular_activities: Optional[str] = None
    relationship_maintenance: Optional[str] = None

    trust_arrangements: Optional[UploadField] = None
    life_insurance: Optional[str] = None
    education_funding: Optional[str] = None
    guardian_compensation: Optional[str] = None

    guardianship_will: Optional[UploadField] = None
    guardian_letters: Optional[UploadField] = None
    custody_agreements: Optional[UploadField] = None
    guardianship_attorney: Optional[UploadField] = None

    excluded_persons: Optional[str] = None
    temporary_caregivers: Optional[str] = None
    school_contacts: Optional[str] = None
    medical_contacts: Optional[str] = None


# ---------- ROOT PAYLOAD ----------

class Section21EstatePlanningPayload(
    RootModel[Dict[str, object]]
):
    pass
