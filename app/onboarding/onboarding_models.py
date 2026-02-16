from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class OnboardingState(BaseModel):
    user_id: str
    role: str  # owner | nextkin

    version: Optional[str] = None
    has_completed: bool = False
    manually_started: bool = False

    last_run_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
