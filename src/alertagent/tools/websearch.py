from typing import List

from mcp import Client
from mcp.types import ContentBlock
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

_ENDPOINT = "https://mcp.exa.ai/mcp"


def _result_text(content: list[ContentBlock]) -> str:
    return "\n".join(block.text for block in content if block.type == "text")


async def web_search(query: str, num_results: int = 5) -> List[str]:
    """Search the web for documentation, known issues, and guidance about an alert.

    query is a semantically rich description of the ideal page, not just keywords.
    Returns a list of results.
    """
    async with Client(_ENDPOINT) as client:
        result = await client.call_tool(
            "web_search_exa", {"query": query, "numResults": num_results}
        )
    if result.is_error:
        raise ToolFailed(_result_text(result.content))

    return [block.text for block in result.content if block.type == "text"]


# No web_fetch on purpose: alert annotations are untrusted input, and fetching an
# arbitrary URL would let them steer cluster data (pod env vars, configmaps) to an
# attacker-controlled host. Search keeps the documentation value without the leak.
websearch_tools = FunctionToolset(tools=[web_search]).with_metadata(code_mode=True)
