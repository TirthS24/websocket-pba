from ws_server.applib.models.practice import Practice
from ws_server.applib.state import StateContext
from ws_server.applib.types import Channel
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None  # Optional: if not provided, a new thread_id will be generated
    channel: Channel
    data: Optional[list[Practice]] = Field(default=None)
    context: Optional[StateContext] = Field(default=None)
    task: Optional[str] = Field(default=None)

    @model_validator(mode='after')
    def validate_data_for_web_channel(self):
        """Ensure data is provided when channel is WEB."""
        if self.channel == Channel.WEB and self.data is None:
            raise ValueError('data field is required when channel is web')
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

class TokenEvent(BaseModel):
    """SSE token event for streaming response content"""
    event: Literal['token'] = 'token'
    content: str

class StaticEvent(BaseModel):
    """SSE static event for static messages (non-streaming)"""
    event: Literal['static'] = 'static'
    content: str


class EndEvent(BaseModel):
    event: Literal['end'] = 'end'


class ErrorEvent(BaseModel):
    """SSE error event"""
    event: Literal['error'] = 'error'
    message: str