from applib.config import config
from applib.textcontent.textstore import TextStore

prompts = TextStore(config.APPDATA_FOLDER_PATH / "prompts")