from pydantic import BaseModel, RootModel, field_validator
from typing import Dict, List, Optional, Union, Any

from app.sections.common_upload_field import UploadField


UploadValue = Union[str, UploadField, None]


class Vehicle(BaseModel):
    year: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    vin: UploadValue = None
    license_plate: UploadValue = None
    registration_expiry: Optional[str] = None
    insurance_company: Optional[str] = None
    insurance_policy: UploadValue = None
    financing: Optional[str] = None
    maintenance_records: UploadValue = None
    parking_location: Optional[str] = None
    spare_keys: Optional[str] = None
    notes: Optional[str] = None

    @field_validator(
        "vin",
        "license_plate",
        "insurance_policy",
        "maintenance_records",
        mode="before",
    )
    @classmethod
    def normalize_upload_fields(cls, value: Any):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return {"text": value, "files": []}
        return value


# ✅ ROOT MODEL (Pydantic v2)
class Section5VehiclesPayload(RootModel[Dict[str, List[Vehicle]]]):
    pass
