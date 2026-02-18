from pydantic import BaseModel
from typing import List, Optional


class PracticeDetails(BaseModel):
    external_id: str
    name: str
    email_address: Optional[str] = None
    phone_number: Optional[str] = None
    hours: Optional[str] = None
    work_start_time: Optional[str] = None
    work_end_time: Optional[str] = None
