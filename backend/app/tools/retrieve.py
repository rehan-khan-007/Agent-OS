from app.tools.base import BaseTool, ToolResult
from app.database import async_session
from app.retrieval.hybrid import hybrid_search


class RetrieveTool(BaseTool):
    name = "retrieve"
    description = (
        "Search the user's document library for information relevant to a query. "
        "This includes any files the user has uploaded through the chat interface "
        "(PDF, TXT, or MD), as well as any other documents already ingested into "
        "the system. Always use this tool when the user refers to something they "
        "uploaded, attached, or added, or when you need facts or context from "
        "stored documents to answer their question — you do not have direct file "
        "access, so this tool is the only way to read uploaded content."
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
