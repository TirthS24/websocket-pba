from enum import Enum
from typing import Literal

class Channel(Enum):
    WEB = 'web'
    SMS = 'sms'

class SmsIntent(Enum):
    CHAT = 'chat'
    ESCALATION = 'escalation'
    UNCLEAR = 'unclear'

class WebIntent(Enum):
    CHAT = 'chat'
    ESCALATION = 'escalation'
    UNCLEAR = 'unclear'