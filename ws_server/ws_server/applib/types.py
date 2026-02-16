from enum import Enum
from typing import Literal

class Channel(Enum):
    WEB = 'web'
    SMS = 'sms'

class SmsIntent(Enum):
    IN_SCOPE = 'in_scope'
    ESCALATION = 'escalation'
    OUT_OF_SCOPE = 'out_of_scope'

class WebIntent(Enum):
    IN_SCOPE = 'in_scope'
    ESCALATION = 'escalation'
    OUT_OF_SCOPE = 'out_of_scope'