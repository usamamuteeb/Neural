"""
Cheap-tier helper for narrow, non-agentic background tasks (classify a
snippet, summarize a block of text, decide if a wake-word capture is a
real command vs. noise). Runs on the Haiku utility model via a one-off
query() call — never the persistent Sonnet-backed ClaudeSDKClient that
runs the main agent loop, and never given any tools. Keep it that way:
the point is these calls stay cheap and stay out of the decision path
that matters. See the plan doc's Core Engine section.
"""

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

from . import config


async def quick_task(prompt: str) -> str:
    options = ClaudeAgentOptions(
        allowed_tools=[],
        env={"ANTHROPIC_MODEL": config.UTILITY_MODEL},
    )
    text_parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return "".join(text_parts)
