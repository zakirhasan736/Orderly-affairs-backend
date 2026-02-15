from pydantic import BaseModel
from typing import Dict, Any, List


class Section1VitalInformationPayload(BaseModel):
    vital_info: Dict[str, Any]

    next_of_kin: List[Dict[str, Any]] = []
    executor_trustee: List[Dict[str, Any]] = []
    additional_contacts: List[Dict[str, Any]] = []
