from pydantic import BaseModel, EmailStr
from bson import ObjectId

class UserModel(BaseModel):
    id: str | None = None
    email: EmailStr
    full_name: str | None = None
    password: str
    verified: bool = False
    totp_secret: str | None = None  # New field for authenticator app secret

