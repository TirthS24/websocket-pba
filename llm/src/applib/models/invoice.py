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
    claims: list[Claim] # All internal claims that have been rolled up into this invoice. If an invoice only ever has one internal claim, this can be a single Claim object rather than a list.
    total_fee: Optional[float] = None # INVOICE TOTAL CHARGE
    total_due: Optional[float] = None # REMAINING BALANCE; total_fee LESS ANY PAYMENTS RECEIVED BY PATIENT
    total_network_discount: Optional[float] = None # TOTAL NETWORK DISCOUNT; the contractually agreed-upon amount that has been removed from the original charge
    total_paid: Optional[float] = None # TOTAL PAYMENTS made by patient
    total_insurance: Optional[float] = None # TOTAL PAYMENTS made by insurance
