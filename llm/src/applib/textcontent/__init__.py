from applib.config import config
from .textstore import TextStore

static_messages = TextStore(config.APPDATA_FOLDER_PATH / "static_messages")
structured_outputs = TextStore(config.APPDATA_FOLDER_PATH / "prompts" / "structured_outputs")