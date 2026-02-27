from applib.helpers import get_secret_from_arn
from pathlib import Path
from pydantic_settings import BaseSettings

import os

class AppSettings(BaseSettings):
    pass

class DevSettings(AppSettings):
    PSQL_BOT_USERNAME: str
    PSQL_BOT_PASSWORD: str
    PSQL_FE_USERNAME: str
    PSQL_FE_PASSWORD: str
    PSQL_HOST: str
    PSQL_PORT: str
    PSQL_STATE_DATABASE: str
    PSQL_DATA_DATABASE: str
    PSQL_SSLMODE: str
    AWS_BEDROCK_REGION: str
    BEDROCK_MODEL_ID_BILLING_AGENT: str
    BEDROCK_MODEL_ID_CLAIM_AGENT: str
    BEDROCK_MODEL_ID_ESCALATION_DETECTION: str
    BEDROCK_MODEL_ID_INTENT_DETECTION: str
    BEDROCK_MODEL_ID_SMS_ROUTER: str
    BEDROCK_MODEL_ID_WEB_ROUTER: str
    BEDROCK_MODEL_ID_SMS_RESPOND: str
    BEDROCK_MODEL_ID_WEB_RESPOND: str
    BEDROCK_MODEL_ID_SMS_GUARDRAIL_EVALUATE: str
    BEDROCK_MODEL_ID_WEB_GUARDRAIL_EVALUATE: str
    BEDROCK_MODEL_ID_SMS_GUARDRAIL_REWRITE: str
    BEDROCK_MODEL_ID_WEB_GUARDRAIL_REWRITE: str
    BEDROCK_MODEL_ID_THREAD_SUMMARIZE: str
    MAXIMUM_GUARDRAIL_REWRITES: int
    APPDATA_FOLDER_PATH: Path
    AUTH_API_KEY: str
    WS_SERVER_URL: str
    WS_SERVER_ORIGIN: str
    LANGSMITH_API_KEY: str
    LANGSMITH_PROJECT: str
    LANGSMITH_TRACING: str
    LANGSMITH_ENDPOINT: str

class ProdSettings(AppSettings):
    PSQL_BOT_USERNAME: str                        # SECRET
    PSQL_BOT_PASSWORD: str                        # SECRET
    PSQL_HOST: str                                # SECRET
    PSQL_PORT: str                                # SECRET
    PSQL_STATE_DATABASE: str                      # SECRET
    PSQL_SSLMODE: str                             # SECRET
    AWS_BEDROCK_REGION: str
    BEDROCK_MODEL_ID_BILLING_AGENT: str
    BEDROCK_MODEL_ID_CLAIM_AGENT: str
    BEDROCK_MODEL_ID_ESCALATION_DETECTION: str
    BEDROCK_MODEL_ID_INTENT_DETECTION: str
    BEDROCK_MODEL_ID_SMS_ROUTER: str              # CDK ENV VAR
    BEDROCK_MODEL_ID_WEB_ROUTER: str              # CDK ENV VAR
    BEDROCK_MODEL_ID_SMS_RESPOND: str             # CDK ENV VAR
    BEDROCK_MODEL_ID_WEB_RESPOND: str             # CDK ENV VAR
    BEDROCK_MODEL_ID_SMS_GUARDRAIL_EVALUATE: str  # CDK ENV VAR
    BEDROCK_MODEL_ID_WEB_GUARDRAIL_EVALUATE: str  # CDK ENV VAR
    BEDROCK_MODEL_ID_SMS_GUARDRAIL_REWRITE: str   # CDK ENV VAR
    BEDROCK_MODEL_ID_WEB_GUARDRAIL_REWRITE: str   # CDK ENV VAR
    BEDROCK_MODEL_ID_THREAD_SUMMARIZE: str        # CDK ENV VAR
    MAXIMUM_GUARDRAIL_REWRITES: int               # CDK ENV VAR
    APPDATA_FOLDER_PATH: Path                     # DOCKERFILE
    AUTH_API_KEY: str                             # SECRET
    WS_SERVER_URL: str
    WS_SERVER_ORIGIN: str
    LANGSMITH_API_KEY: str
    LANGSMITH_PROJECT: str
    LANGSMITH_TRACING: str
    LANGSMITH_ENDPOINT: str

    # AWS_BEDROCK_REGION SET AUTOMATICALLY BY AWS IN PROD

DEV = True # SET TRUE FOR DEV, PROVIDE .ENV FILE at /src/.env

if DEV:
    config = DevSettings()
else:

    """
    In prod, set DATABASE_SECRET_ARN & API_SECRET_ARN in CDK deployment 
    """

    config = ProdSettings(
        **get_secret_from_arn(os.environ["LLM_SERVER_SECRET_ARN"]),
    )
