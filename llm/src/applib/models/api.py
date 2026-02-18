from applib.models.invoice import Invoice
from applib.types import Channel
from pydantic import BaseModel, ValidationError, model_validator
from typing import Literal, Optional

class ChatRequest(BaseModel):
    message: str
    thread_id: str
    channel: Channel
    invoice: Optional[Invoice] = None
    stripe_link: Optional[str] = None
    webapp_link: Optional[str] = None

    @model_validator(mode="after")
    def validate_conditional_fields(self) -> "ChatRequest":
        if self.invoice is not None and self.stripe_link is None:
            raise ValidationError("stripe_link is required when invoice is provided")
        if self.channel == Channel.SMS and self.webapp_link is None:
            raise ValidationError("webapp_link is required when channel is 'sms'")
        return self

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

class SessionConnectRequest(BaseModel):
    """Request body for POST /session/connect (trigger LLM WebSocket connection for a thread)."""
    thread_id: str


class SmsChatRequest(BaseModel):
    """Request body for POST /chat/sms. Invoice is optional."""
    message: str
    thread_id: str
    invoice: Optional[Invoice] = None

class TokenEvent(BaseModel):
    """SSE token event for streaming response content"""
    event: Literal['token'] = 'token'
    content: str

class EndEvent(BaseModel):
    event: Literal['end'] = 'end'

class ErrorEvent(BaseModel):
    """SSE error event"""
    event: Literal['error'] = 'error'
    message: str