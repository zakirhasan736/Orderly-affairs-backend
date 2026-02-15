from pydantic import BaseModel
from typing import List
from datetime import datetime


class SectionData(BaseModel):
    owner_id: str
    section_id: str
    section_key: str

    encrypted_data: str
    subsections: List[str]

    created_at: datetime | None = None
    updated_at: datetime | None = None
