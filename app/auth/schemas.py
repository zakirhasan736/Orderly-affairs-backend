from pydantic import BaseModel, EmailStr
from typing import Optional

class SignupSchema(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None

class LoginSchema(BaseModel):
    email: EmailStr
    password: str

class VerifyTOTPRequest(BaseModel):
    email: EmailStr
    code: str

class LinkAuthenticatorRequest(BaseModel):
    email: EmailStr
    code: str
    secret: str

class EmailRequest(BaseModel):
    email: EmailStr

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: int
    
# ============================================================
# 🔸 Next-of-Kin
# ============================================================
class NextKinCreateRequest(BaseModel):
    email: EmailStr
    full_name: str
    relationship: str
    phone_number: str | None = None
    access_level: str | None = "limited"
    authorized_sections: list[str] | None = []
    immediate_access: bool | None = False
    nok_letter_received: bool | None = False
    master_password: str | None = None
    password_card_generated: bool | None = False
    card_storage_location: str | None = None
    key_bag_location: str | None = None
    documents_bag_location: str | None = None
    special_instructions: str | None = None


class NextKinLoginRequest(BaseModel):
    email: EmailStr
    password: str
