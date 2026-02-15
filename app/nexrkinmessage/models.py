from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, model_validator


class LetterCreate(BaseModel):
    title: str
    subject: Optional[str] = None
    content: Optional[str] = None

    recipient: str
    recipient_email: EmailStr

    message_type: str  # letter | audio | video
    media: Optional[dict] = None

    delivery_trigger: str  # death | date
    delivery_date: Optional[datetime] = None
    delivery_occasion: Optional[str] = None  # ✅ ADDED

    @model_validator(mode="after")
    def validate_delivery_date(self):
        if self.delivery_trigger == "date" and not self.delivery_date:
            raise ValueError("delivery_date is required when delivery_trigger is 'date'")
        return self

class LetterUpdate(BaseModel):
    title: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None

    recipient: Optional[str] = None
    recipient_email: Optional[EmailStr] = None

    message_type: Optional[str] = None
    media: Optional[dict] = None

    delivery_trigger: Optional[str] = None
    delivery_date: Optional[datetime] = None
    delivery_occasion: Optional[str] = None  # ✅ ADDED

    @model_validator(mode="after")
    def validate_delivery_date(self):
        if self.delivery_trigger == "date" and not self.delivery_date:
            raise ValueError("delivery_date is required when delivery_trigger is 'date'")
        return self

class LetterDB(BaseModel):
    owner_id: str

    title: str
    encrypted_payload: str

    recipient: str
    recipient_email: EmailStr

    message_type: str
    media: Optional[dict]

    delivery_trigger: str
    delivery_date: Optional[datetime]
    delivery_occasion: Optional[str]

    status: str = "pending"
    is_deleted: bool = False

    created_at: datetime
    updated_at: datetime
    sent_at: Optional[datetime] = None
