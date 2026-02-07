from pydantic import BaseModel
from typing import List
from .patient import Patient

class PracticeDetails(BaseModel):
    external_id: str
    name: str
    email_address: str
    phone_number: str
    hours: str

# === Practice ==
# Root model
class Practice(PracticeDetails):
    patients: List[Patient]