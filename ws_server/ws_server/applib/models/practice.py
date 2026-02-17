from pydantic import BaseModel
from typing import List, Optional
from .patient import Patient

class PracticeDetails(BaseModel):
    external_id: str
    name: str
    email_address: str
    phone_number: str
    hours: Optional[str] = None
    work_start_time: str | None = None
    work_end_time: str | None = None

# === Practice ==
# Root model
class Practice(PracticeDetails):
    patients: List[Patient]