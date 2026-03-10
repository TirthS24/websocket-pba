from datetime import datetime, time, timedelta, timezone
from typing import Optional

from pydantic import BaseModel
from zoneinfo import ZoneInfo


class PracticeDetails(BaseModel):
    external_id: str
    name: str
    email_address: Optional[str] = None
    phone_number: Optional[str] = None
    work_start_time: str
    work_end_time: str
    timezone: str
    timezone_region: Optional[str]

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
    
    def _parse_work_time(self, time_str: str) -> time:
        """Parse work_start_time or work_end_time in 12-hour format (e.g. '05:30 PM', '12:00 AM')."""
        s = time_str.strip()
        for fmt in ("%I:%M %p", "%I:%M:%S %p"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue
        raise ValueError(f"Invalid time format: {time_str!r}")

    @property
    def is_within_timezone(self) -> bool:
        """True if current UTC time is within work_start_time..work_end_time in the practice's timezone.
        Returns False if timezone_region is missing, empty, or invalid."""
        if not self.timezone_region or not self.timezone_region.strip():
            return False
        try:
            tz = ZoneInfo(self.timezone_region.strip())
        except Exception:
            return False
        now_utc = datetime.now(timezone.utc)
        today_local = now_utc.astimezone(tz).date()

        try:
            start_time = self._parse_work_time(self.work_start_time)
            end_time = self._parse_work_time(self.work_end_time)
        except ValueError:
            return False

        start_dt = datetime.combine(today_local, start_time, tzinfo=tz)
        end_dt = datetime.combine(today_local, end_time, tzinfo=tz)
        if end_time <= start_time:
            end_dt += timedelta(days=1)

        start_utc = start_dt.astimezone(timezone.utc)
        end_utc = end_dt.astimezone(timezone.utc)
        return start_utc <= now_utc <= end_utc
