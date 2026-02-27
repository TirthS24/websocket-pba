from pathlib import Path
import json
from datetime import datetime, timezone
from typing import Any, Literal
from boto3 import client

def get_utc_now() -> str:
    """Return current UTC datetime in ISO format for message timestamps (sent_at/read_at)."""
    return datetime.now(timezone.utc).isoformat()


def text_from_content_block(block: Any) -> str:
    """Extract plain text from a single content block (dict or str).
    Handles {"type": "text", "text": "..."}, other dict shapes, and raw strings."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if "text" in block:
            return block.get("text") or ""
        for key in ("content", "input", "value"):
            if key in block and isinstance(block[key], str):
                return block[key]
    return ""


def message_content_str(message: Any, list_separator: str = " ") -> str:
    """Extract plain text from a LangChain message or content (for guardrail/LLM responses).
    Handles None, object with .content, string, list of blocks, and dict with 'text'.
    list_separator is used when joining multiple blocks (use '' for guardrail concatenation)."""
    if message is None:
        return ""
    content = getattr(message, "content", message)
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [text_from_content_block(b) for b in content if text_from_content_block(b)]
        return list_separator.join(parts).strip() if parts else ""
    if isinstance(content, dict) and "text" in content:
        return (content.get("text") or "").strip()
    return ""


def load_json(path: str | Path) -> dict:
    with open(path) as f_in:
        j = json.load(f_in)
    return j


def get_postgres_conn_string(user: str, password: str, database_name: str, host: str = None, port: str = None, sslmode: str = 'disable'):
    host = host or 'localhost'
    port = port or '5432'

    return f"postgresql://{user}:{password}@{host}:{port}/{database_name}"


# def format_invoice_for_context(invoice: Invoice) -> str:
#     """Format invoice as readable text for LLM context (e.g. in system prompt)."""
#     lines = [
#         "Practice:",
#         f"  name={invoice.practice.name}, external_id={invoice.practice.external_id}",
#         f"  email={invoice.practice.email_address}, phone={invoice.practice.phone_number}, hours={invoice.practice.hours}",
#         "",
#         "Patient:",
#         f"  name={invoice.patient.first_name} {invoice.patient.last_name}, external_id={invoice.patient.external_id}",
#         f"  dob={invoice.patient.dob}, gender={invoice.patient.gender}",
#         f"  email={invoice.patient.email_address}, phone={invoice.patient.phone_number}",
#         "",
#         "Claims (use for billing questions):",
#     ]
#     for i, c in enumerate(invoice.claims, 1):
#         lines.append(
#             f"  Claim {i}: external_id={c.external_id}, date_of_service={c.date_of_service}, "
#             f"total_due={c.total_due}, total_fee={c.total_fee}, total_paid={c.total_paid}, "
#             f"total_insurance={c.total_insurance}, provider_name={c.provider_name}"
#         )
#     lines.extend([
#         "",
#         f"Stripe payment link (give if they want to pay): {invoice.stripe_link}",
#         f"Web app verification link: {invoice.web_app_link}",
#     ])
#     return "\n".join(lines)

def get_secret_from_arn(secret_arn: str) -> dict[str, str]:
    r = client("secretsmanager").get_secret_value(SecretId=secret_arn)
    return json.loads(r["SecretString"])


def redact_string(s: str, redaction_type: Literal['all', 'start', 'end'] = None) -> str:
    """
    Redacts all or some of the input string s
    Redaction type can be 'all', 'start', 'end';
        all - entire string
        start - beginning of string redacted
        end - end of string redacted
    """
    valid_redaction_types = {'all', 'start', 'end'}
    redaction_type = redaction_type.lower() if redaction_type else 'end'
    redaction_type = redaction_type if redaction_type in valid_redaction_types else 'end'

    if s is None or not isinstance(s, str):
        return s
    elif redaction_type == "all" or len(s) <= 1:
        return "*****"
    elif redaction_type == "start":
        return "****" + s[-1]
    else:  # redaction_type == end
        return s[0] + "****"
