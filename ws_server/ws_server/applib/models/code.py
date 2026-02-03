from pydantic import BaseModel
from typing import Optional

class CodeGuidance(BaseModel):
    group_code: Optional[str] = None
    reason_code: Optional[str] = None
    description: Optional[str] = None
    summary: Optional[str] = None
    provider_action: Optional[str] = None
    patient_action: Optional[str] = None

    def __bool__(self) -> bool:
        return bool(self.patient_action)