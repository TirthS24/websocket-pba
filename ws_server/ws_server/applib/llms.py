from ws_server.applib.config import config
from boto3 import client
from botocore.config import Config
from langchain_aws import ChatBedrockConverse

bedrock_rt_client = client(
    "bedrock-runtime",
    region_name=config.AWS_BEDROCK_REGION,
    aws_access_key_id=config.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
)


def get_bedrock_converse_model(**kwargs) -> ChatBedrockConverse:
    return ChatBedrockConverse(client=bedrock_rt_client, **kwargs)
