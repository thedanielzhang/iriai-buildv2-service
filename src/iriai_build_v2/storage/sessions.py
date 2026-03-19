from __future__ import annotations

import json

import asyncpg

from iriai_compose import AgentSession, SessionStore


class PostgresSessionStore(SessionStore):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def load(self, session_key: str) -> AgentSession | None:
        row = await self._pool.fetchrow(
            "SELECT session_key, session_id, metadata FROM sessions WHERE session_key = $1",
            session_key,
        )
        if row is None:
            return None
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return AgentSession(
            session_key=row["session_key"],
            session_id=row["session_id"],
            metadata=metadata,
        )

    async def delete(self, session_key: str) -> None:
        """Remove a session, forcing the next invoke to start fresh."""
        await self._pool.execute(
            "DELETE FROM sessions WHERE session_key = $1", session_key,
        )

    async def save(self, session: AgentSession) -> None:
        metadata_json = json.dumps(session.metadata)
        await self._pool.execute(
            """
            INSERT INTO sessions (session_key, session_id, metadata, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW())
            ON CONFLICT (session_key) DO UPDATE
            SET session_id = EXCLUDED.session_id,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            session.session_key,
            session.session_id,
            metadata_json,
        )
