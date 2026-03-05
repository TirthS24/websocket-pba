from applib.state import State
from langchain_core.tools import tool

from typing import Callable
from applib.types import Channel



_NO_LINK_MESSAGE = (
    "No payment link is available for this session. "
    "Suggest the user contact the practice or visit their website for payment options."
)


def create_get_payment_link_tool(state: State) -> Callable:
    @tool
    def get_payment_link_tool_web() -> str:
        """Retrieve the payment link to share with the user.

        Use this tool when the user wants to pay their bill.

        Returns:
            A message containing the payment link in <URL> tags, or a fallback if no link is available.
        """
        link = (state.get("stripe_payment_link") or "").strip()
        if not link:
            return _NO_LINK_MESSAGE
        return f"The user can pay their bill at the url between the URL tags: <URL>{link}</URL>"

    @tool
    def get_payment_link_tool_sms() -> str:
        """Retrieve the portal link to share with the user.

        Use this tool when the user wants to view their invoice or pay their bill.

        Returns:
            A message containing the portal link in <URL> tags, or a fallback if no link is available.
        """
        link = (state.get("webapp_link") or "").strip()
        if not link:
            return _NO_LINK_MESSAGE
        return f"The user can visit the web portal and pay their bill at the url between the URL tags: <URL>{link}</URL>"


    if state['channel'] == Channel.WEB:
        return get_payment_link_tool_web
    else:
        return get_payment_link_tool_sms
