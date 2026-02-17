from ws_server.applib.models.practice import PracticeDetails
from ws_server.applib.models.patient import PatientDetails
from ws_server.applib.models.claim import Claim
from ws_server.applib.types import Channel
from pydantic import BaseModel
from typing import Literal, Optional
class Invoice(BaseModel):
    """
    Invoice-level data.
    This should represent the rolled-up claims that constitute the single invoice we are asking the user to pay.
    """
    patient: PatientDetails
    practice: PracticeDetails
    claims: list[Claim]
    stripe_link: str  # The stripe payment link that should be given to the user if they indicate a wish to pay the currently viewed invoice
    web_app_link: str  # The link to the webapp DOB screen

class ChatRequest(BaseModel):
    message: str
    thread_id: str
    channel: Channel
    invoice: Optional[Invoice] = None


class SmsChatRequest(BaseModel):
    """Request body for the REST SMS chat endpoint. Channel is always sms."""
    message: str
    thread_id: str
    invoice: Optional[Invoice] = None

class ThreadRequest(BaseModel):
    thread_id: str

class SummarizeRequest(ThreadRequest):
    pass

class ThreadHistoryRequest(ThreadRequest):
    pass

class EscalationEvent(BaseModel):
    """SSE event indicating escalation to human agent"""
    event: Literal['escalation'] = 'escalation'
    should_escalate: bool

class MetadataEvent(BaseModel):
    """SSE metadata event containing FE alert flags"""
    event: Literal['metadata'] = 'metadata'
    should_escalate: bool

class TokenEvent(BaseModel):
    """SSE token event for streaming response content"""
    event: Literal['token'] = 'token'
    content: str

class StaticEvent(BaseModel):
    """SSE static event for static messages (non-streaming)"""
    event: Literal['static'] = 'static'
    content: str


class ReplaceEvent(BaseModel):
    """Replace the last streamed assistant message with this content (e.g. after guardrail rewrite)."""
    event: Literal['replace'] = 'replace'
    content: str


class EndEvent(BaseModel):
    event: Literal['end'] = 'end'


class ErrorEvent(BaseModel):
    """SSE error event"""
    event: Literal['error'] = 'error'
    message: str