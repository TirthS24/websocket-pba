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

    @property
    def phone_number_readable(self) -> str:
        if not self.phone_number:
            return ''
        area_code = self.phone_number[:3]
        prefix = self.phone_number[3:6]
        line = self.phone_number[6:]

        return f"({area_code}) {prefix}-{line}"
