from pydantic import BaseModel, EmailStr
from typing import Optional, List

class NextKinBase(BaseModel):
    email: EmailStr
    full_name: str
    relationship: str
    phone_number: Optional[str] = None
    access_level: Optional[str] = "limited"
    authorized_sections: Optional[List[str]] = []
    immediate_access: Optional[bool] = False
    nok_letter_received: Optional[bool] = False
    master_password: Optional[str] = None
    password_card_generated: Optional[bool] = False
    card_storage_location: Optional[str] = None
    key_bag_location: Optional[str] = None
    documents_bag_location: Optional[str] = None
    special_instructions: Optional[str] = None


class NextKinCreateRequest(NextKinBase):
    pass


class NextKinLoginRequest(BaseModel):
    email: EmailStr
    password: str


class NextKinResponse(NextKinBase):
    id: str
    owner_id: str
    role: str = "nextkin"

    class Config:
        orm_mode = True
