from enum import Enum
from typing import Literal

class Channel(Enum):
    WEB = 'web'
    SMS = 'sms'

class SmsIntent(Enum):
    ESCALATION = 'escalation'
    IN_SCOPE = 'in_scope'
    OUT_OF_SCOPE = 'out_of_scope'

class WebIntent(Enum):
    ESCALATION = 'escalation'
    IN_SCOPE = 'in_scope'
    OUT_OF_SCOPE = 'out_of_scope'