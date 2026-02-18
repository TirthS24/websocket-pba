from applib.config import config
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

_TEMPLATES_FOLDER: Path = config.APPDATA_FOLDER_PATH / "templates"

class JinjaEnvironments:
    thread = Environment(
        loader=FileSystemLoader(_TEMPLATES_FOLDER / "thread"),
        lstrip_blocks=True,
        trim_blocks=True
    )
    claim = Environment(
        loader=FileSystemLoader(_TEMPLATES_FOLDER / "claim"),
        lstrip_blocks=True,
        trim_blocks=True
    )

