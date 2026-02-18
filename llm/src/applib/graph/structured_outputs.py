from applib.textcontent import structured_outputs
from applib.types import SmsIntent, WebIntent
from pydantic import BaseModel, Field

class SmsIntentClassification(BaseModel):
    intent: SmsIntent = Field(description=structured_outputs.intent_router.sms.field_descriptions.intent)

class WebIntentClassification(BaseModel):
    intent: WebIntent = Field(description=structured_outputs.intent_router.web.field_descriptions.intent)

class GuardrailEvaluation(BaseModel):
    """
    Structured output for guardrail evaluation. Only metrics; no passes or issues.
    If any metric is false, the response is rewritten (issues for rewrite are derived from metrics in code).
    """
    is_english: bool = Field(
        description="True if the response's primary language is English and grammatically correct."
    )
    no_markdown: bool = Field(
        description="True if the response uses plain text only (no markdown formatting)."
    )
    is_concise: bool = Field(
        description="True if the response is concise and appropriately short for the channel."
    )
    no_pii: bool = Field(
        description="True if the response contains no PII (no full names, SSN, DOB, full account numbers, specific addresses)."
    )
    no_payment_promises: bool = Field(
        description="True if the response makes no promises about payment plans, due dates, or amounts."
    )
    is_appropriate: bool = Field(
        description="True if the response is appropriate, safe, on-topic, and answers the user's question with a helpful tone."
    )

