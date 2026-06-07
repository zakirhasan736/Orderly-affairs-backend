from pydantic import BaseModel, RootModel, field_validator
from typing import Dict, List, Optional, Any


class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1
    model_config = {"extra": "ignore"}


class UploadField(BaseModel):
    text: Optional[str] = None
    files: List[UploadedFile] = []
    _deleted_files: List[str] = []
    model_config = {"extra": "ignore"}


class Vehicle(BaseModel):
    year: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    vin: Optional[str] = None
    license_plate: Optional[str] = None
    registration_expiry: Optional[str] = None
    insurance_company: Optional[str] = None
    insurance_policy: Optional[str] = None
    financing: Optional[str] = None
    maintenance_records: Optional[UploadField] = None
    parking_location: Optional[str] = None
    spare_keys: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("maintenance_records", mode="before")
    @classmethod
    def normalize_maintenance_records(cls, value: Any):
        if value is None or value == "":
            return None
        return value


# ✅ ROOT MODEL (Pydantic v2)
class Section5VehiclesPayload(RootModel[Dict[str, List[Vehicle]]]):
    pass
