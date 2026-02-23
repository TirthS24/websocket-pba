from .code import CodeGuidance
from applib.helpers import redact_string
from pydantic import BaseModel, AfterValidator
from typing import Annotated, List, Literal, Optional

from .payment import Payment


# === ClaimStatus ===
# Represents the claim status section (from the 835 file).
class ClaimStatus(BaseModel):
    code: str
    description: str
    payer_classification: str
    was_forwarded: bool


# === RenderingProvider ===
# Represents the rendering provider section (from the 835 file).
class RenderingProvider(BaseModel):
    first_name: Optional[str | None] = None
    last_name: Optional[str | None] = None
    identification_code_qualifier: Optional[str | None] = None
    identification_code: Optional[str | None] = None


# === Adjustment ===
# Represents a single adjustment entry on a service line.
class Adjustment(BaseModel):
    id: int
    identifier: str
    group_code: str
    group_code_description: str
    reason_code: str
    reason_code_description: str
    amount: float
    guidance: Optional[CodeGuidance] = None

    @property
    def responsibility(self) -> Literal['insurance', 'patient']:
        if self.group_code.upper() == 'PR':
            return 'patient'
        return 'insurance'


# === Service ===
# Represents a line-item service billed in a claim, with adjustments.
class Service(BaseModel):
    id: int
    service_date: Optional[str | None] = None
    service_period_start: Optional[str | None] = None
    service_period_end: Optional[str | None] = None
    service_allowed_amount: float
    service_charge_amount: float
    service_paid_amount: float
    service_balance: float
    qualifier: str
    modifier: Optional[str] = None
    service_code: str
    service_allowed_units: int
    service_billed_units: int
    adjustments: List[Adjustment]

    @property
    def insurance_adjustments(self) -> List[Adjustment]:
        return list(filter(lambda x: x.group_code.upper() != 'PR', self.adjustments))

    @property
    def patient_responsibility_adjustments(self) -> List[Adjustment]:
        return list(filter(lambda x: x.group_code == 'PR', self.adjustments))

    @property
    def has_adjustments(self):
        return bool(self.adjustments)


# === Claim835Data ===
# Represents claim-level information from the 835 file.
class Claim835Data(BaseModel):
    id: int
    claim_id: str
    icn: str
    patient_icq: Annotated[Optional[str], AfterValidator(lambda x: redact_string(x, redaction_type='start'))] = None
    patient_ic: Annotated[Optional[str], AfterValidator(lambda x: redact_string(x, redaction_type='start'))] = None
    claim_type: str
    total_charge_amount: float
    total_allowed_amount: float
    total_paid_amount: float
    total_balance: float
    payment_effective_date: str
    claim_statement_period_start: Optional[str] = None
    claim_statement_period_end: Optional[str] = None
    rendering_provider: Optional[RenderingProvider] = None
    status: Optional[ClaimStatus] = None
    services: List[Service]

    @property
    def has_adjustments(self) -> bool:
        return any(map(lambda x: x.has_adjustments, self.services))

# === ClaimDBData ===
# Represents claim-level data enriched or tracked internally in the database.
class Claim(BaseModel):
    external_id: str
    date_of_service: str
    total_due: float
    total_fee: float
    total_paid: float
    total_manual_paid: float
    total_network_discount: float
    total_insurance: float
    total_resolved_amount: float
    resolved_at: Optional[str]
    provider_name: Optional[str]
    is_resolved_as_not_present: Optional[str | bool] = None
    edi_mappings: Optional[List[Claim835Data]] = [] # Need to make it mandatory later
    adjustments: Optional[List[Adjustment]] = []
    payments: Optional[List[Payment]] = []

    @property
    def has_service_level_adjustments(self) -> bool:
        """
        Checks edi data for service-level adjustments toward gracefully falling back to claim-level adjustments
        """

        for claim_835 in self.edi_mappings:
            if claim_835.has_adjustments:
                return True
        return False
