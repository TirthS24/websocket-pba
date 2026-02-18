from .code import CodeGuidance
from applib.helpers import redact_string
from pydantic import BaseModel, AfterValidator
from typing import Annotated, List, Optional

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
    first_name: str
    last_name: str
    identification_code_qualifier: str
    identification_code: str


# === Adjustment ===
# Represents a single adjustment entry on a service line.
class Adjustment(BaseModel):
    id: int
    identifier: Optional[str] = None
    group_code: str
    group_code_description: Optional[str] = None
    reason_code: str
    reason_code_description: Optional[str] = None
    amount: float
    guidance: Optional[CodeGuidance] = None


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
    payment_effective_date: Optional[str] = None
    claim_statement_period_start: Optional[str] = None
    claim_statement_period_end: Optional[str] = None
    rendering_provider: Optional[RenderingProvider] = None
    status: Optional[ClaimStatus] = None
    services: List[Service]


# === ClaimDBData ===
# Represents claim-level data enriched or tracked internally in the database.
class Claim(BaseModel):
    external_id: str
    date_of_service: str
    total_due: float
    total_fee: float
    total_paid: float
    total_manual_paid: Optional[float] = None
    total_network_discount: Optional[float] = None
    total_insurance: Optional[float] = None
    total_resolved_amount: Optional[float] = None
    resolved_at: Optional[str] = None
    provider_name: Optional[str] = None
    is_resolved_as_not_present: Optional[str | bool] = None
    # FE/API often send a simpler claim (no EDI); optional so Invoice validates from WebSocket payload.
    edi_mappings: Optional[List[Claim835Data]] = None
    adjustments: Optional[List[Adjustment]] = None




