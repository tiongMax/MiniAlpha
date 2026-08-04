"""Interactive runner that exposes every graph transition."""

import asyncio
import json

from app.api.dependencies import create_research_service
from app.services.research_agent import ResearchAgentService


async def run_once(
    user_input: str,
    service: ResearchAgentService | None = None,
) -> str:
    """Run one user request through the shared research service.

    Args:
        user_input: Natural-language question entered in the CLI.
        service: Optional application-scoped service. A production service is
            composed when this function is called independently.

    Returns:
        Final model answer.

    Raises:
        RuntimeError: If required Gemini configuration is missing.
        Exception: Provider, model, or graph failures not converted into tool
            error results.
    """
    active_service = service or create_research_service()
    result = await active_service.research(user_input)

    for call in result.tool_calls:
        args = json.dumps(call.arguments, ensure_ascii=False)
        print("[model] requested tool call:")
        print(f"  - {call.name}({args})")

    for tool_result in result.tool_results:
        print(f"[tools] tool result ({tool_result.name}):")
        print(tool_result.content)
        if tool_result.artifact is not None:
            artifact_type = tool_result.artifact.get("artifact_type", "unknown")
            status = tool_result.artifact.get("status", "unknown")
            print(f"[tools] artifact: {artifact_type} ({status})")

    print("[model] final answer:")
    print(result.answer)
    return result.answer


async def main() -> None:
    """Run the interactive MiniAlpha command-line session until exit."""
    service = create_research_service()
    print("MiniAlpha Phase 3")
    print("Company data is retrieved from Yahoo Finance. Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"quit", "exit"}:
            return
        if not user_input:
            continue

        try:
            await run_once(user_input, service)
        except Exception as error:
            print(f"[error] {error}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
