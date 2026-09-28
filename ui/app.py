"""Thin Streamlit chat UI for the copilot API.

Run: uv run streamlit run ui/app.py   (start the API first: uv run python -m copilot.api)

The UI keeps no business logic: it sends each message to POST /chat and shows
what comes back. The API address is built from COPILOT_API_HOST and
COPILOT_API_PORT in .env; the connect timeout is INFRA_CHECK_TIMEOUT_SECONDS.
There is no read timeout because one answer can take several model calls. The
conversation itself is stored by the API; this page only keeps what it needs to
redraw the chat, so reloading the browser tab starts a new conversation.
"""

import sys
import uuid
from pathlib import Path

import httpx
import streamlit as st

# Streamlit only puts ui/ on sys.path, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.settings import SettingsError, load_settings

# A wildcard listen address is not something a client can connect to.
LOCAL_HOSTS = {"0.0.0.0": "127.0.0.1"}

st.set_page_config(page_title="ShopEase support")


def new_conversation_id() -> str:
    return f"ui-{uuid.uuid4().hex[:8]}"


def error_text(response: httpx.Response) -> str:
    """The API's own error text, whichever shape it came back in."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:300] or "empty response"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(err.get("msg")) for err in detail)
    if isinstance(body, dict) and body.get("error"):
        return f"{body['error']}: {detail}"
    return str(detail or body)[:300]


def ask(question: str, customer_id: int | None) -> dict:
    """Send one message to POST /chat; return a message dict to show."""
    try:
        settings = load_settings()
    except SettingsError as exc:
        return {"role": "assistant", "error": f"The app is not configured: {exc}"}
    host = LOCAL_HOSTS.get(settings.copilot_api_host, settings.copilot_api_host)
    url = f"http://{host}:{settings.copilot_api_port}/chat"

    body: dict[str, object] = {
        "conversation_id": st.session_state.conversation_id,
        "message": question,
    }
    if customer_id is not None:
        body["customer_id"] = customer_id
    try:
        response = httpx.post(
            url,
            json=body,
            timeout=httpx.Timeout(None, connect=settings.infra_check_timeout_seconds),
        )
    except httpx.ConnectError:
        return {
            "role": "assistant",
            "error": "The support assistant is not reachable right now. Please make "
            "sure the copilot API is running (`uv run python -m copilot.api`) and "
            "try again.",
        }
    except httpx.HTTPError as exc:
        return {
            "role": "assistant",
            "error": f"Sorry, the request to the support assistant failed ({exc}).",
        }

    if response.status_code != 200:
        return {
            "role": "assistant",
            "error": "Sorry, I could not get an answer. "
            f"({response.status_code} {error_text(response)})",
        }
    try:
        data = response.json()
        return {
            "role": "assistant",
            "content": data["reply"],
            "sources": data["sources"],
            "tools_called": data["tools_called"],
            "escalated": data["escalated"],
        }
    except (ValueError, KeyError, TypeError):
        return {
            "role": "assistant",
            "error": "Sorry, the support assistant sent a reply I could not read.",
        }


def render(message: dict) -> None:
    with st.chat_message(message["role"]):
        if message.get("error"):
            st.error(message["error"])
            return
        st.markdown(message["content"])
        if message["role"] != "assistant":
            return
        if message["escalated"]:
            st.badge(
                "Escalated to human", icon=":material/support_agent:", color="orange"
            )
        with st.expander("Sources and tools"):
            st.markdown("**Sources cited**")
            for source in message["sources"] or ["none"]:
                st.markdown(f"- `{source}`" if message["sources"] else f"- {source}")
            st.markdown("**Tools called**")
            for tool in message["tools_called"] or ["none"]:
                st.markdown(f"- `{tool}`" if message["tools_called"] else f"- {tool}")


if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = new_conversation_id()
    st.session_state.messages = []

with st.sidebar:
    st.header("Conversation")
    customer_id = st.number_input(
        "Customer id (optional)", min_value=1, step=1, value=None, format="%d"
    )
    st.caption(f"Conversation id: {st.session_state.conversation_id}")
    if st.button("New conversation"):
        st.session_state.conversation_id = new_conversation_id()
        st.session_state.messages = []
        st.rerun()

st.title("ShopEase support")

for message in st.session_state.messages:
    render(message)

if question := st.chat_input("Ask about a policy or an order"):
    user_message = {"role": "user", "content": question}
    st.session_state.messages.append(user_message)
    render(user_message)
    with st.spinner("Thinking..."):
        answer = ask(question, int(customer_id) if customer_id else None)
    st.session_state.messages.append(answer)
    render(answer)
