from pydantic import BaseModel
from typing import List, Optional
from .patient import Patient

class PracticeDetails(BaseModel):
    external_id: str
    name: str
    email_address: str | None = None
    phone_number: str | None = None
    hours: Optional[str] = None
    work_start_time: str | None = None
    work_end_time: str | None = None

# === Practice ==
# Root model
class Practice(PracticeDetails):
    patients: List[Patient]