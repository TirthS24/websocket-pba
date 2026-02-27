from pydantic import BaseModel
from typing import Optional


class PracticeDetails(BaseModel):
    external_id: str
    name: str
    email_address: Optional[str] = None
    phone_number: Optional[str] = None
    work_start_time: str
    work_end_time: str
    timezone: str

    @property
    def hours(self) -> str:
        return f"Monday-Friday {self.work_start_time} - {self.work_end_time} {self.timezone}"
