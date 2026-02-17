from pydantic import BaseModel

# === PaymentData ===
# Represents payment data stored in the internal database.
class Payment(BaseModel):
    transaction_id: str | None = None
    payment_status: str | None = None
    payment_amount: float | None = None
    remaining_amount: float | None = None
    communication_channel: str | None = None
    payment_method: str | None = None
    external_transaction_at: str | None = None
    first_outreach_date: str | None = None
    old_patient_due: float | None = None
    old_patient_insurance: float | None = None
    old_patient_network_discount: float | None = None
    old_patient_paid: float | None = None
    old_patient_fee: float | None = None