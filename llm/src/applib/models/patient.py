from applib.helpers import redact_string
from pydantic import BaseModel, AfterValidator
from functools import partial
from typing import Annotated, List


class PatientDetails(BaseModel):
    external_id: Annotated[str, AfterValidator(lambda x: redact_string(x, redaction_type="none"))]
    first_name: Annotated[str, AfterValidator(lambda x: redact_string(x, redaction_type="none"))]
    last_name: Annotated[str, AfterValidator(lambda x: redact_string(x, redaction_type="none"))]