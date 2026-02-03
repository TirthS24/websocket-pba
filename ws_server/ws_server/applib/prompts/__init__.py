from ws_server.applib.config import config
from ws_server.applib.textcontent.textstore import TextStore

prompts = TextStore(config.APPDATA_FOLDER_PATH / "prompts")