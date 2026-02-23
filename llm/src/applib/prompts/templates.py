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
    invoice = Environment(
        loader=FileSystemLoader(_TEMPLATES_FOLDER / "invoice"),
        lstrip_blocks=True,
        trim_blocks=True
    )
    invoice_xml = Environment(
        loader=FileSystemLoader(_TEMPLATES_FOLDER / "invoice_xml"),
        lstrip_blocks=True,
        trim_blocks=True
    )

