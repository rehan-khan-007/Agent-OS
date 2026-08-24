from app.tools.base import BaseTool, ToolResult
from app.database import async_session
from app.retrieval.hybrid import hybrid_search


class RetrieveTool(BaseTool):
    name = "retrieve"
    description = (
        "Search AgentOS's persistent knowledge base — a standing corpus of "
        "documents (research papers, official reference material, and any "
        "files uploaded through the chat interface), not just the current "
        "session's uploads. Use this tool whenever the user's question is "
        "likely to be answered or supported by the stored documents — "
        "especially domain-specific, factual, or reference-style questions "
        "— and prefer retrieved evidence over relying solely on your own "
        "training knowledge when the corpus may contain the answer. Don't "
        "call this for general conversation, pure calculation, or questions "
        "the corpus clearly wouldn't cover. Note: stored documents have a "
        "fixed publication date and may be outdated — if the user is asking "
        "for the latest, current, or most recent information on a topic, "
        "say so explicitly in your answer rather than presenting retrieved "
        "material as necessarily up to date."
    )

    async def run(self, query: str, top_k: int = 3) -> ToolResult:
        try:
            async with async_session() as session:
                chunks = await hybrid_search(query, session, top_k=top_k)

            if not chunks:
                return ToolResult(output="No relevant information found.")

            # Combine the retrieved chunks into a single context string
            # the agent can read directly, with source info for transparency.
            combined = "\n\n".join(
                f"[{c.source}, chunk {c.chunk_index}]: {c.text}" for c in chunks
            )
            return ToolResult(output=combined)
        except Exception as e:
            return ToolResult(output=None, error=str(e))

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The question or topic to search for in the document library",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of relevant chunks to retrieve (default 3)",
                        },
                    },
                    "required": ["query"],
                },
            },
        }
