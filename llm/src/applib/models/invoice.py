from pydantic import BaseModel
from .claim import Claim
from .patient import PatientDetails
from .payment import Payment
from .practice import PracticeDetails
from typing import Optional


class Invoice(BaseModel):
    """
    Invoice-level data.
    This should represent the rolled-up claims that constitute the single invoice we are asking the user to pay.
    """
    patient: PatientDetails
    practice: PracticeDetails
    payments: Optional[list[Payment]] = [] # All payments that have been made toward this invoice.
    claims: list[Claim] # All internal claims that have been rolled up into this invoice. If an invoice only ever has one internal claim, this can be a single Claim object rather than a list.
