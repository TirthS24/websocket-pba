"""
Load environment variables from AWS Secrets Manager before Django settings are loaded.
Import this module first in manage.py, asgi.py, and wsgi.py so os.environ is populated
before ws_server.settings (and any _env / _env_bool / _env_csv) are evaluated.

Secret name: set WS_SECRET_NAME (e.g. "pba-dev/ws-server-secrets") or we derive
pba-{ENVIRONMENT}/ws-server-secrets when ENVIRONMENT is set (default "devlive").
Uses setdefault so existing env vars (e.g. from ECS task definition) override secret values.
"""
import json
import os

import boto3


def _load_secrets_from_aws(env: str = "devlive") -> None:
    secret_name = f"pba-{env}/ws-server-secrets"
    region = os.environ.get("AWS_REGION", "us-east-2")
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    secret_str = response.get("SecretString")
    if not secret_str:
        raise RuntimeError(f"Secret {secret_name!r} has no SecretString")
    data = json.loads(secret_str)
    for key, value in data.items():
        if value is not None:
            # setdefault: existing env (e.g. from ECS) overrides secret
            os.environ.setdefault(key, str(value))


# Run on import so that any later import of settings sees the env
_load_secrets_from_aws(env=os.environ.get("ENVIRONMENT", "devlive"))
