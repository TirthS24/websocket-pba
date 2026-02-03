from pydantic import BaseModel
from typing import List, Optional

from pydantic.v1 import PathError

from .claim import Claim
from .payment import Payment

class PatientDetails(BaseModel):
    external_id: str
    first_name: str
    last_name: str
    middle_name: Optional[str] = None
    gender: str
    phone_number: str
    email_address: str
    dob: str

# === PatientData ===
# Represents additional patient details stored in the internal database.
class Patient(PatientDetails):
    claims: List[Claim]
    patient_payments: List[Payment]