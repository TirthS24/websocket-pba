"""
Load the three ws_server secrets from AWS Secrets Manager before Django settings are loaded.
Import this module first in manage.py, asgi.py, and wsgi.py so os.environ is populated
before ws_server.settings (and any _env / _env_bool / _env_csv) are evaluated.

Only these keys are fetched from Secrets Manager (not from ECS task environment):
- DJANGO_SECRET_KEY
- AUTH_API_KEY
- LLM_SERVICE_AUTH

Secret name: pba-{ENVIRONMENT}/ws-server-secrets (ENVIRONMENT defaults to "devlive").
Uses setdefault so existing env vars (e.g. local dev) override secret values.
"""
import json
import os
from pathlib import Path
import boto3

# Keys to load from Secrets Manager only (must match infrastructure/stack.py WS_SECRET_KEYS)
_WS_SECRET_KEYS = {"DJANGO_SECRET_KEY", "AUTH_API_KEY", "LLM_SERVICE_AUTH"}


def _load_secrets_from_aws(env: str = "devlive") -> None:
    secret_name = f"pba-{env}/ws-server-secrets"
    region = os.environ.get("AWS_REGION", "us-east-2")
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    secret_str = response.get("SecretString")
    if not secret_str:
        raise RuntimeError(f"Secret {secret_name!r} has no SecretString")
    data = json.loads(secret_str)
    for key in _WS_SECRET_KEYS:
        value = data.get(key)
        if value is not None:
            os.environ.setdefault(key, str(value))


# Load .env first when present so ENVIRONMENT and secrets can come from file
def _load_dotenv_if_present() -> None:
    root = Path(__file__).resolve().parent.parent.parent  # ws_server/ws_server -> ws_server
    for candidate in (root.parent / ".env", root / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            from dotenv import load_dotenv
            load_dotenv(candidate)
            return


_load_dotenv_if_present()

# Skip AWS when ENVIRONMENT is not set or is "local"; use .env values instead
_env = os.environ.get("ENVIRONMENT", "").strip().lower()
if _env and _env != "local":
    _load_secrets_from_aws(env=_env)
