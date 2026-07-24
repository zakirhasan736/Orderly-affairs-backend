from pydantic import BaseModel, RootModel, model_validator
from typing import Dict, List, Optional


class UploadedFile(BaseModel):
    name: str
    url: str
    public_id: str
    version: Optional[int] = 1


class UploadField(BaseModel):
    text: Optional[str] = None
    files: List[UploadedFile] = []
    deleted_files: List[str] = []

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def coerce_string_or_object(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            return {"text": value, "files": []}
        if isinstance(value, dict):
            data = dict(value)
            if "_deleted_files" in data and "deleted_files" not in data:
                data["deleted_files"] = data.pop("_deleted_files")
            return data
        return value


class Section6AData(BaseModel):
    inventory_instructions: Optional[str] = None
    home_address: Optional[str] = None
    residence_type: Optional[str] = None
    custom_residence_type: Optional[str] = None
    ownership_status: Optional[str] = None
    ownership_type: Optional[str] = None
    custom_ownership_type: Optional[str] = None
    year_purchased_leased: Optional[str] = None
    joint_owners: Optional[str] = None
    county: Optional[str] = None

    mortgage_lienholder_landlord: Optional[UploadField] = None
    payment_methods: Optional[str] = None
    mortgage_maturity_date: Optional[str] = None
    lease_end_date: Optional[str] = None
    property_tax_due_date: Optional[str] = None
    property_deeds_titles: Optional[UploadField] = None
    mortgage_lease_statement: Optional[UploadField] = None
    second_mortgage_heloc: Optional[UploadField] = None
    property_tax_bills: Optional[UploadField] = None
    closing_refinancing_docs: Optional[UploadField] = None
    paid_off_documentation: Optional[UploadField] = None
    reverse_mortgage_info: Optional[UploadField] = None
    realtor_landlord_contact: Optional[UploadField] = None

    residents: Optional[str] = None
    pets: Optional[str] = None
    year_built: Optional[str] = None
    square_footage: Optional[str] = None
    lot_size: Optional[str] = None
    bedrooms: Optional[str] = None
    bathrooms: Optional[str] = None
    home_features: Optional[str] = None
    major_appliances: Optional[str] = None

    home_inventory: Optional[UploadField] = None
    inventory_date_location: Optional[str] = None
    builder_info: Optional[UploadField] = None
    home_warranty: Optional[UploadField] = None
    appliance_manuals: Optional[UploadField] = None
    utility_shutoffs: Optional[UploadField] = None
    circuit_breaker: Optional[UploadField] = None
    home_systems_notes: Optional[str] = None
    security_system: Optional[UploadField] = None
    smart_home_devices: Optional[str] = None


class Section6MainResidencePayload(
    RootModel[Dict[str, Section6AData]]
):
    pass
