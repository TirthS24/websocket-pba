from ws_server.applib.textcontent import structured_outputs
from ws_server.applib.types import SmsIntent, WebIntent
from pydantic import BaseModel, Field
from typing import Optional

class SmsIntentClassification(BaseModel):
    intent: SmsIntent = Field(description=structured_outputs.intent_router.sms.field_descriptions.intent)

class WebIntentClassification(BaseModel):
    intent: WebIntent = Field(description=structured_outputs.intent_router.web.field_descriptions.intent)

class GuardrailEvaluation(BaseModel):
    """
    Structured output for the guardrail evaluation.
    """
    is_appropriate: bool = Field(
        description="Whether the response is appropriate and safe"
    )
    is_helpful: bool = Field(
        description="Whether the response actually answers the user's question"
    )
    is_concise: bool = Field(
        description="Whether the response is reasonably concise"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="List of specific issues found, if any"
    )
    passes: bool = Field(
        description="Overall pass/fail decision"
    )
    suggested_fix: Optional[str] = Field(
        default=None,
        description="If the response fails, provide a corrected version"
    )

