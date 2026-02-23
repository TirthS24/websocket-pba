"""
Load environment variables from AWS Secrets Manager before any other application imports.
Import this module first so os.environ is populated before config and other modules load.

Expects LLM_SECRET_NAME (e.g. "pba-dev/llm-server-secrets") to be set in the environment.
Uses setdefault so existing env vars (e.g. from ECS task definition) override secret values.
"""
import json
import os

import boto3


def _load_secrets_from_aws(env: str = "devlive") -> None:
    secret_name = f"pba-{env}/llm-server-secrets"
    if not secret_name:
        raise RuntimeError(
            "LLM_SECRET_NAME environment variable is required to load secrets from AWS Secrets Manager"
        )
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


# Run on import so that any later import of config etc. sees the env
_load_secrets_from_aws(env=os.environ.get("ENVIRONMENT", "devlive"))
