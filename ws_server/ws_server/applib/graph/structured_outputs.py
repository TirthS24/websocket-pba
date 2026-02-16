from ws_server.applib.textcontent import structured_outputs
from ws_server.applib.types import SmsIntent, WebIntent
from pydantic import BaseModel, Field

class SmsIntentClassification(BaseModel):
    intent: SmsIntent = Field(description=structured_outputs.intent_router.sms.field_descriptions.intent)

class WebIntentClassification(BaseModel):
    intent: WebIntent = Field(description=structured_outputs.intent_router.web.field_descriptions.intent)

class GuardrailEvaluation(BaseModel):
    """
    Minimal structured output for guardrail: single pass/fail decision and issues.
    Decision is made by the model using the graph rules; no separate params (helpful, concise, etc.).
    """
    passes: bool = Field(
        description="True if the response is acceptable; false if it needs to be rewritten."
    )
    issues: list[str] = Field(
        default_factory=list,
        description="List of specific issues found when passes is false."
    )

