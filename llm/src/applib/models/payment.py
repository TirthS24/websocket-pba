from pydantic import BaseModel

# === PaymentData ===
# Represents payment data stored in the internal database.
class Payment(BaseModel):
    transaction_id: str
    payment_status: str
    payment_amount: float
    remaining_amount: float
    communication_channel: str
    payment_method: str
    external_transaction_at: str
    first_outreach_date: str
    old_patient_due: float
    old_patient_insurance: float
    old_patient_network_discount: float
    old_patient_paid: float
    old_patient_fee: float