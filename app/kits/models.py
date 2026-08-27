from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime

class SubsectionInput(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)

class SectionInput(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)

class TogglesInput(BaseModel):
    disabled_sections: Dict[str, bool] = Field(default_factory=dict)
    disabled_subsections: Dict[str, bool] = Field(default_factory=dict)

class KitDoc(BaseModel):
    owner_id: str
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    disabled_sections: Dict[str, bool] = Field(default_factory=dict)
    disabled_subsections: Dict[str, bool] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

class ChecklistUpdate(BaseModel):
    section_id: str
    items: Dict[str, bool]
    notes: Optional[str] = None