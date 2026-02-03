from pathlib import Path
import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Try to load from .env file in multiple locations
        # Priority: environment variables > .env file
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=True,
    )
    
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
    # Optional: if not set, boto3 will fall back to its standard credential chain
    # (e.g. IAM role in ECS/EC2, ~/.aws/credentials if mounted, etc.)
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    BEDROCK_MODEL_ID_BILLING_AGENT: str
    BEDROCK_MODEL_ID_CLAIM_AGENT: str
    BEDROCK_MODEL_ID_ESCALATION_DETECTION: str
    BEDROCK_MODEL_ID_INTENT_DETECTION: str
    BEDROCK_MODEL_ID_SMS_ROUTER: str
    BEDROCK_MODEL_ID_THREAD_SUMMARIZE: str
    BEDROCK_MODEL_ID_SMS_RESPOND: str
    BEDROCK_MODEL_ID_WEB_RESPOND: str
    MAXIMUM_GUARDRAIL_REWRITES: int
    APPDATA_FOLDER_PATH: Path
    AUTH_API_KEY: str

# Try to load .env file before creating Settings instance
# This ensures pydantic-settings can find the .env file
try:
    from dotenv import load_dotenv
    # Try multiple locations for .env file
    current_dir = Path(__file__).resolve().parent
    env_paths = [
        current_dir.parent.parent.parent / ".env",  # Project root: /app/.env (Docker)
        current_dir.parent.parent / ".env",         # ws_server/.env
        current_dir.parent / ".env",                 # ws_server/ws_server/.env
        Path(".env"),                               # Current working directory
        Path(os.getcwd()) / ".env",                 # Explicit current directory
    ]
    
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break
    else:
        # Try default location
        load_dotenv(override=False)
except Exception:
    pass

config = Settings()
