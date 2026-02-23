from applib.config import config
from boto3 import client
from botocore.config import Config
from langchain_aws import ChatBedrockConverse

bedrock_rt_client = client(
    'bedrock-runtime',
    region_name=config.AWS_BEDROCK_REGION,
    config=Config(connect_timeout=30, read_timeout=120, retries={'max_attempts': 0})
)

def get_bedrock_converse_model(**kwargs) -> ChatBedrockConverse:
    return ChatBedrockConverse(
        client=bedrock_rt_client, 
        provider="anthropic",
        region_name=config.AWS_BEDROCK_REGION,
        **kwargs)
