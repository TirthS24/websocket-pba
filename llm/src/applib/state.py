from applib.models.api import Invoice
from applib.models.claim import Claim
from applib.models.patient import PatientDetails
from applib.models.payment import Payment
from applib.models.practice import PracticeDetails
from applib.types import Channel

from langchain_core.messages import AnyMessage
from pydantic import BaseModel
from typing import Annotated, Optional
from typing_extensions import NotRequired, TypedDict

import operator


class State(TypedDict):
    thread_id: str
    messages: Annotated[list[AnyMessage], operator.add]
    channel: Channel
    invoice: NotRequired[Optional[Invoice]]
    pending_ai_message: NotRequired[Optional[AnyMessage]]
    stripe_link: NotRequired[Optional[str]]
    webapp_link: NotRequired[Optional[str]]
