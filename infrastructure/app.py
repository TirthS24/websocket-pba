#!/usr/bin/env python3
"""
AWS CDK App entry point for ECS Fargate deployment.

Usage:
    cdk deploy --all
    cdk deploy WebSocketPbaStack-stage
    cdk deploy WebSocketPbaStack-live
"""

import os
from aws_cdk import App, Environment
from dotenv import load_dotenv
from stack import WebSocketPbaStack

# Load environment variables from .env file
env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_file):
    load_dotenv(env_file)
else:
    # Try loading from parent directory
    parent_env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(parent_env):
        load_dotenv(parent_env)

# Get environment configuration
environment = os.getenv("ENVIRONMENT", "devlive")
aws_account = os.getenv("AWS_ACCOUNT_ID")
aws_region = os.getenv("AWS_BEDROCK_REGION", "us-east-2")

if not aws_account:
    raise ValueError(
        "AWS_ACCOUNT_ID must be set in .env file or environment variables"
    )

if not environment:
    raise ValueError(
        "ENVIRONMENT must be set in .env file or environment variables"
    )

# Create CDK app
app = App()

# Create CDK environment object
cdk_env = Environment(account=aws_account, region=aws_region)

# Create stack
stack = WebSocketPbaStack(
    app,
    f"WebSocketPbaStack-{environment}",
    env=cdk_env,
    description=f"ECS Fargate deployment for WebSocket PBA server ({environment})",
)

app.synth()
