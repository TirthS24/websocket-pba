from pydantic import BaseModel
from typing import List, Optional

from pydantic.v1 import PathError

from .claim import Claim
from .payment import Payment

class PatientDetails(BaseModel):
    external_id: str
    first_name: Optional[str | None] = None
    last_name: Optional[str | None] = None
    middle_name: Optional[str] = None
    gender: Optional[str | None] = None
    phone_number: Optional[str | None] = None
    email_address: Optional[str | None] = None
    dob: Optional[str | None] = None

# === PatientData ===
# Represents additional patient details stored in the internal database.
class Patient(PatientDetails):
    claims: List[Claim]
    patient_payments: List[Payment]