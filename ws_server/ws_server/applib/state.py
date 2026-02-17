from ws_server.applib.models.api import Invoice
from ws_server.applib.models.claim import Claim
from ws_server.applib.models.patient import Patient, PatientDetails
from ws_server.applib.models.payment import Payment
from ws_server.applib.models.practice import Practice, PracticeDetails
from ws_server.applib.types import Channel

from langchain_core.messages import AnyMessage
from pydantic import BaseModel
from typing import Annotated, Optional
from typing_extensions import NotRequired, TypedDict

import operator

class StateContext(BaseModel):
    current_practice: Optional[PracticeDetails] = None
    current_patient: Optional[PatientDetails] = None
    current_claims: Optional[list[Claim]] = None
    current_payments: Optional[list[Payment]] = None


class State(TypedDict):
    thread_id: str
    messages: Annotated[list[AnyMessage], operator.add]
    channel: Channel
    context: Optional[StateContext]
    task: Optional[str]
    should_escalate: Optional[bool]
    invoice: NotRequired[Optional[Invoice]]
    pending_ai_message: NotRequired[Optional[AnyMessage]]
