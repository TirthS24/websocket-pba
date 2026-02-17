from .code import CodeGuidance
from pydantic import BaseModel
from typing import List, Optional

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
    service_date: str
    service_period_start: str
    service_period_end: str
    service_allowed_amount: float
    service_charge_amount: float
    service_paid_amount: float
    service_balance: float
    qualifier: Optional[str] = None
    procedure_modifier: Optional[str] = None
    procedure_code: Optional[str] = None
    service_code: Optional[str] = None
    service_allowed_units: Optional[int] = None
    service_billed_units: Optional[int] = None
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
    patient_icq: str
    patient_ic: str
    claim_type: str
    total_charge_amount: float
    total_allowed_amount: float
    total_paid_amount: float
    total_balance: float
    payment_effective_date: str
    claim_statement_period_start: str
    claim_statement_period_end: str
    rendering_provider: RenderingProvider
    status: ClaimStatus
    services: List[Service]


# === ClaimDBData ===
# Represents claim-level data enriched or tracked internally in the database.
class Claim(BaseModel):
    external_id: str | None = None
    date_of_service: str | None = None
    total_due: float | None = None
    total_fee: float | None = None
    total_paid: float | None = None
    total_manual_paid: float | None = None
    total_network_discount: float | None = None
    total_insurance: float | None = None
    total_resolved_amount: float | None = None
    resolved_at: str | None = None
    provider_name: str | None = None
    is_resolved_as_not_present: Optional[str | bool] = None
    edi_mappings: Optional[List[Claim835Data]] = None
    adjustments: Optional[List[Adjustment]] = None




