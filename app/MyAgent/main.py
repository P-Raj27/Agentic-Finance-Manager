from typing import Any
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from collections import OrderedDict
from strands import Agent, tool
import asyncio
from strands.agent.conversation_manager.null_conversation_manager import NullConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from mcp_client.client import get_streamable_http_mcp_client
from memory.session import get_memory_session_manager
from tools.agents import call_intent_agent,call_records_agent,fetch_records_agent
from logger_config import app, log


# Define a Streamable HTTP MCP Client
mcp_clients = [get_streamable_http_mcp_client()]

DEFAULT_SYSTEM_PROMPT = """
You are a helpful finance manager name E.V, your job is to break user statment and analyze the intent to store, fetch and analyze the expense record into dynamodb.
You will be called from Telegram chat, so the response you give back should be short and concise without much details, but you should include details of the Category, Subcategory and Time logged strictly.
Your job is to call the best agent based on the Intent you get.
All transactions are in INR
"""

import os


# Define a collection of tools used by the model
tools = []

_INLINE_FUNCTION_NAMES = set()


tools.append(call_intent_agent)
tools.append(call_records_agent)
tools.append(fetch_records_agent)


# Add MCP client to tools if available
for mcp_client in mcp_clients:
    if mcp_client:
        tools.append(mcp_client)


def _make_conversation_manager():
    return NullConversationManager()

def agent_factory():
    cache = {}
    def get_or_create_agent(session_id, user_id):
        _actor_id = user_id
        key = f"{session_id}/{_actor_id}"
        if key not in cache:
            cache[key] = Agent(
                model=load_model(),
                # session_manager=get_memory_session_manager(session_id, _actor_id),
                conversation_manager=_make_conversation_manager(),
                system_prompt=DEFAULT_SYSTEM_PROMPT,
                tools=tools,
                hooks=[
                ],
            )
        return cache[key]
    return get_or_create_agent
get_or_create_agent = agent_factory()


def _extract_prompt(payload: dict):
    """Accept harness-style messages[], tool_results[], or plain prompt string payloads."""
    if "messages" in payload:
        return payload["messages"]
    if "tool_results" in payload:
        return [{"role": "user", "content": [{"toolResult": {
            "toolUseId": tr["toolUseId"],
            "status": tr.get("status", "success"),
            "content": tr.get("content", []),
        }} for tr in payload["tool_results"]]}]
    return payload.get("prompt", "")


def _has_inline_function_call(messages) -> bool:
    """Return True if messages contains an assistant toolUse for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES or not isinstance(messages, list):
        return False
    for msg in messages:
        if msg.get("role") == "assistant":
            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("toolUse", {}).get("name") in _INLINE_FUNCTION_NAMES:
                    return True
    return False


def _is_inline_function_call(event: dict) -> bool:
    """Check if a contentBlockStart event is for an inline function tool."""
    if not _INLINE_FUNCTION_NAMES:
        return False
    cbs = event.get("contentBlockStart", {})
    start = cbs.get("start", {})
    tool_use = start.get("toolUse") if isinstance(start, dict) else None
    return tool_use is not None and tool_use.get("name") in _INLINE_FUNCTION_NAMES



@app.entrypoint
async def invoke(payload, context):
    log.info("Invoking Agent.....")
    session_id = getattr(context, 'session_id', 'default-session')
    user_id = getattr(context, 'user_id', 'default-user')
    agent = get_or_create_agent(session_id, user_id)

    prompt = _extract_prompt(payload)
    print("Prompt:",prompt)

    import os, sys

    task_dir = "/var/task"
    if os.path.isdir(task_dir):
        print(os.listdir(task_dir))
    else:
        print(f"Skipping /var/task listing (not running in Lambda/AgentCore container)")

    for root, dirs, files in os.walk("/var/task"):
        # skip noisy dependency folders so the output stays readable
        dirs[:] = [d for d in dirs if d not in ("site-packages", "__pycache__", ".git")]
        depth = root.replace("/var/task", "").count(os.sep)
        if depth <= 2:
            print(root, "->", files[:5])

    print("=== sys.path ===")
    print(sys.path)

    result = agent(prompt)


    
    # Return a normal dictionary wrapper
    return {"result": result.message}




if __name__ == "__main__":
    app.run()
