"""Interactive Phase 1 runner that exposes every graph transition."""

import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent.graph import build_graph
from app.config import create_model


def _text_content(content: Any) -> str:
    """Extract display text while ignoring provider metadata content blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _print_message(node: str, message: BaseMessage) -> str | None:
    if isinstance(message, HumanMessage):
        print(f"[{node}] user: {_text_content(message.content)}")
        return None

    if isinstance(message, ToolMessage):
        print(f"[{node}] tool result ({message.name or 'unknown'}):")
        print(_text_content(message.content))
        return None

    if isinstance(message, AIMessage) and message.tool_calls:
        print(f"[{node}] model requested {len(message.tool_calls)} tool call(s):")
        for call in message.tool_calls:
            args = json.dumps(call.get("args", {}), ensure_ascii=False)
            print(f"  - {call.get('name')}({args})")
        return None

    if isinstance(message, AIMessage):
        content = _text_content(message.content)
        print(f"[{node}] final answer:")
        print(content)
        return content

    print(f"[{node}] {message.type}: {_text_content(message.content)}")
    return None


async def run_once(user_input: str) -> str | None:
    graph = build_graph(create_model())
    final_answer = None

    async for update in graph.astream(
        {"messages": [HumanMessage(content=user_input)]},
        stream_mode="updates",
        config={"recursion_limit": 12},
    ):
        for node, delta in update.items():
            if not isinstance(delta, dict):
                print(f"[{node}] {delta}")
                continue

            messages: list[Any] = delta.get("messages", [])
            for message in messages:
                answer = _print_message(node, message)
                if answer is not None:
                    final_answer = answer

    return final_answer


async def main() -> None:
    print("MiniAlpha Phase 1")
    print("The fake provider contains AAPL and MSFT. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"quit", "exit"}:
            return
        if not user_input:
            continue

        try:
            await run_once(user_input)
        except Exception as error:
            print(f"[error] {error}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
