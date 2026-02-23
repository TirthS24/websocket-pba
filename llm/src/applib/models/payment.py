from datetime import datetime

from pydantic import BaseModel

# === PaymentData ===
# Represents payment data stored in the internal database.
class Payment(BaseModel):
    payment_status: str
    payment_amount: float
    remaining_amount: float
    payment_method: str
    external_transaction_at: datetime

    @property
    def transaction_date(self) -> str:
        return self.external_transaction_at.date().strftime("%Y/%m/%d")
