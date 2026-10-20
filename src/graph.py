from langgraph.graph import END
from datetime import datetime
import pytz
from langchain_core.messages import ToolMessage, HumanMessage, AnyMessage, SystemMessage
from typing import Annotated
from langgraph.graph.message import add_messages
from typing import TypedDict
from .tool import calendar_tools,create_event_tool,list_events_tool,delete_event_tool,postpone_event_tool
from .utilities import MANDATORY_ATTENDEE_EMAIL
from langgraph.graph import StateGraph,MessagesState, START
from langchain_groq import ChatGroq
from dotenv import load_dotenv
load_dotenv()  # this will load variables from .env into environment

import os
import re
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
llm = ChatGroq(
    model=GROQ_MODEL,
    groq_api_key=GROQ_API_KEY
)



import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

llm 

model_with_tools = llm.bind_tools(calendar_tools)


BOOKING_POLICY_CONTEXT = f"""
You are assisting with synagogue/event-hall bookings.

Available tools:
- create_event_tool: create/book reservation.
- list_events_tool: availability checks and schedule listing.
- postpone_event_tool: reschedule existing event.
- delete_event_tool: cancel existing event.

Never claim a creation/edit tool is unavailable. If the user asks to book/create/reschedule/cancel, use the matching tool.

Before proposing or creating events, respect these core constraints:
1) Bar Mitzvah/Groom bookings are allowed only on Monday, Thursday, and Saturday at 07:00 or 08:00.
   - In a shared weekday slot, only COHEN+ISRAEL or LEVI+ISRAEL are allowed.
   - COHEN+COHEN, LEVI+LEVI, ISRAEL+ISRAEL, and COHEN+LEVI are blocked.
2) Shabbat bookings are Saturday morning only, with quotas: up to 2 BAR_MITZVAH and up to 1 GROOM (max 3 total).
   - Shabbat priestly quotas: at most 1 COHEN and at most 1 LEVI.
   - Multiple ISRAEL celebrants are allowed within the total quota.
3) Azkara (post-Arvit, any day except Saturday):
    - CATERING is exclusive (max 1 and blocks others).
    - BLESSINGS_ONLY allows up to 3 and cannot coexist with CATERING.
4) Every created event must include {MANDATORY_ATTENDEE_EMAIL} as an attendee.
    - Additional attendee emails are optional.
5) If any required booking field is missing (date/time, category, status, slot, event/catering type), ask for it before tool execution.

If user details are incomplete, ask concise follow-up questions before creating an event.
""".strip()


FINAL_RESPONSE_FORM = """
Respond in this compact structure:
- Outcome: success or blocked.
- Rule check: what passed/failed.
- Next step: exact action user should take.
""".strip()


class GraphState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _is_action_intent(text: str) -> bool:
    return bool(re.search(r"\b(book|create|set|schedule|reserve|reschedule|postpone|delete|cancel)\b", text, flags=re.IGNORECASE))

def should_continue(state):
    try:
        messages = state["messages"]
        last_message = messages[-1]
        if getattr(last_message, "tool_calls", None):
            logger.info("Tool calls detected, continuing to tools node.")
            return "tools"
        logger.info("No tool calls detected, ending graph.")
        return END
    except Exception as e:
        logger.error(f"Error in should_continue: {e}")
        return END


def call_model(state):
    try:
        messages = state["messages"]
        logger.info(f"Received {len(messages)} messages.")

        # Find the latest ToolMessage
        latest_tool_message = next((msg for msg in reversed(messages) if isinstance(msg, ToolMessage)), None)
        latest_human_message = next((msg for msg in reversed(messages) if isinstance(msg, HumanMessage)), None)

        if latest_tool_message:
            logger.info("Latest message is a ToolMessage, invoking LLM for final response.")
            prompt = (
                f"Give final response based on this tool message only: {latest_tool_message}. "
                "Use only facts that appear in the tool message. "
                "Do not invent or infer names, counts, causes, or links that are not explicitly present in the tool message. "
                "If tool output is missing a detail, say it is not provided. "
                f"{FINAL_RESPONSE_FORM}"
            )
            response = llm.invoke(prompt)
            logger.info("LLM response received for tool message.")
            return {"messages": [response]}

        # Add current time information for the LLM
        timezone = pytz.timezone('Asia/Kolkata')
        current_time = datetime.now(timezone)
        current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S %Z')
        time_prompt = (
            f"The current date and time is {current_time_str}. "
            "Please consider this information when generating your response."
        )

        if messages:
            last_message = messages[-1]
            if hasattr(last_message, "content") and isinstance(last_message.content, str):
                logger.info("Prepending current time info to the last user message.")
                last_message.content = f"{time_prompt}\n\n{last_message.content}"

        model_messages = [SystemMessage(content=BOOKING_POLICY_CONTEXT)] + messages
        response = model_with_tools.invoke(model_messages)

        # Fallback: if this is an action intent but no tool was called, retry once with stricter instruction.
        user_text = latest_human_message.content if latest_human_message and isinstance(latest_human_message.content, str) else ""
        if _is_action_intent(user_text) and not getattr(response, "tool_calls", None):
            logger.info("Action intent detected without tool call. Retrying with strict tool-use instruction.")
            strict_instruction = SystemMessage(
                content=(
                    "This user request requires calendar action. "
                    "You must either call one of the available tools now or ask only for the minimum missing fields needed for that tool call. "
                    "Do not say tools are unavailable."
                )
            )
            response = model_with_tools.invoke([SystemMessage(content=BOOKING_POLICY_CONTEXT), strict_instruction] + messages)

        logger.info("Model with tools invoked successfully.")
        return {"messages": [response]}

    except Exception as e:
        logger.error(f"Error in call_model: {e}")
        error_message = {
            "role": "assistant",
            "content": "Sorry, an error occurred while processing your request. Please try again later."
        }
        return {"messages": [error_message]}


def tool_dispatch_node(state):
    try:
        messages = state["messages"]
        last_message = messages[-1]
        tool_calls = getattr(last_message, "tool_calls", [])
        new_messages = []

        tool_map = {
            "create_event_tool": create_event_tool,
            "list_events_tool": list_events_tool,
            "postpone_event_tool": postpone_event_tool,
            "delete_event_tool": delete_event_tool,
        }

        for call in tool_calls:
            tool_name = call["name"]
            args = call["args"]
            logger.info(f"Invoking tool: {tool_name} with args: {args}")
            tool_func = tool_map.get(tool_name)
            if not tool_func:
                logger.warning(f"Tool {tool_name} not found in tool_map.")
                continue
            try:
                result = tool_func.invoke(args)
                new_messages.append(
                    ToolMessage(content=result, name=tool_name, tool_call_id=call["id"])
                )
                logger.info(f"Tool {tool_name} executed successfully.")
            except Exception as tool_error:
                logger.error(f"Error executing tool {tool_name}: {tool_error}")
                error_msg = ToolMessage(
                    content=f"Error executing tool {tool_name}: {tool_error}",
                    name=tool_name,
                    tool_call_id=call["id"]
                )
                new_messages.append(error_msg)

        logger.info(f"Tool dispatch node returning {len(new_messages)} messages.")
        return {"messages": new_messages}

    except Exception as e:
        logger.error(f"Error in tool_dispatch_node: {e}")
        error_msg = ToolMessage(
            content="An error occurred while dispatching tools.",
            name="tool_dispatch_node",
            tool_call_id=None
        )
        return {"messages": [error_msg]}




builder = StateGraph(MessagesState)
builder.add_node("call_model", call_model)
builder.add_node("tools", tool_dispatch_node)
builder.add_edge(START, "call_model")
builder.add_conditional_edges("call_model", should_continue, ["tools", END])
builder.add_edge("tools", "call_model")
graph = builder.compile()


if __name__ == "__main__":
    try:
        logger.info("Starting graph invocation with test message.")
        result = graph.invoke({
            "messages": [
                {"role": "user", "content": "book a meeting tommorow 9 am"}
            ]
        })
        logger.info(f"Graph invocation result: {result}")
        print(result)
    except Exception as e:
        logger.error(f"Error during graph invocation: {e}")
        print(f"Error during graph invocation: {e}")
