# app/nok_letter/models.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class NOKLetterIn(BaseModel):
    letter_date: Optional[str] = None
    letter_to: Optional[str] = None
    letter_greeting: Optional[str] = None
    letter_opening: Optional[str] = None
    kit_description: Optional[str] = None
    access_url: Optional[str] = None
    login_credentials_text: Optional[str] = None
    nok_email: Optional[str] = None
    nok_phone: Optional[str] = None
    password_card_location: Optional[str] = None
    accessible_sections: Optional[str] = None
    key_bag_info: Optional[str] = None
    key_bag_location: Optional[str] = None
    documents_bag_info: Optional[str] = None
    documents_bag_location: Optional[str] = None
    incomplete_kit_message: Optional[str] = None
    closing_message: Optional[str] = None
    letter_signature: Optional[str] = None
    delivery_trigger: Optional[str] = None
    delivery_status: Optional[str] = None
    scheduled_send_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None

class NOKLetterOut(NOKLetterIn):
    id: str
    owner_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
