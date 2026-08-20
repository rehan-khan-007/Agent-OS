"""
Short-term memory: keeps conversation history per session, in-process.

This is intentionally NOT persistent — it resets when the server
restarts. That's fine for now; it establishes the interface that
long_term.py will later back with a real database instead of a dict.
"""

from collections import defaultdict


class ShortTermMemory:
    def __init__(self):
        # session_id -> list of message dicts
        self._sessions: dict[str, list[dict]] = defaultdict(list)

    def get_history(self, session_id: str) -> list[dict]:
        """Returns all messages for a session, oldest first."""
        return self._sessions[session_id]

    def append(self, session_id: str, message: dict) -> None:
        """Adds a single message to a session's history."""
        self._sessions[session_id].append(message)

    def extend(self, session_id: str, messages: list[dict]) -> None:
        """Adds multiple messages at once (e.g. a full turn's worth)."""
        self._sessions[session_id].extend(messages)

    def clear(self, session_id: str) -> None:
        """Wipes a session's history (e.g. user starts a new chat)."""
        self._sessions.pop(session_id, None)


# Singleton — one shared memory store for the whole app process
memory = ShortTermMemory()