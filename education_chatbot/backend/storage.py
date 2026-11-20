import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from secrets import token_urlsafe
from time import time
from uuid import uuid4

import aiosqlite

from .schemas import AgeBand, ChatSummary, StoredTurn

MAX_CHATS = 30
MAX_TURNS = 100
TURN_LIMIT = 12

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA secure_delete=ON;

CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    age_band TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    turn_window INTEGER NOT NULL DEFAULT 0,
    turns_used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS profiles_expiry ON profiles(expires_at);

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chats_profile ON chats(profile_id, updated_at);

CREATE TABLE IF NOT EXISTS turns (
    seq INTEGER PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(chat_id, request_id)
);
CREATE INDEX IF NOT EXISTS turns_chat ON turns(chat_id, seq);
"""


class Missing(Exception):
    pass


class Conflict(Exception):
    pass


class LimitReached(Exception):
    pass


class TurnRateExceeded(Exception):
    pass


@dataclass(frozen=True)
class Profile:
    id: str
    age_band: AgeBand


def token_hash(token: str | None) -> str | None:
    if token is None or len(token) != 43:
        return None
    return sha256(token.encode()).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class WorkspaceStore:
    def __init__(self, connection: aiosqlite.Connection):
        self.db = connection
        self.db.row_factory = aiosqlite.Row
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            try:
                await self.db.execute("BEGIN IMMEDIATE")
                yield
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def one(self, query, params=()):
        async with self.db.execute(query, params) as cursor:
            return await cursor.fetchone()

    async def rows(self, query, params=()):
        async with self.db.execute(query, params) as cursor:
            return await cursor.fetchall()

    async def setup(self) -> None:
        await self.db.executescript(SCHEMA)
        await self.purge_expired()

    async def purge_expired(self) -> None:
        async with self.transaction():
            await self.db.execute("DELETE FROM profiles WHERE expires_at <= ?", (int(time()),))

    async def profile(self, token: str | None) -> Profile | None:
        async with self.lock:
            row = await self.one(
                "SELECT id, age_band FROM profiles WHERE id = ? AND expires_at > ?",
                (token_hash(token), int(time())),
            )
        return Profile(row["id"], AgeBand(row["age_band"])) if row else None

    async def set_age(self, token: str | None, age_band: AgeBand, days: int) -> str:
        now = int(time())
        expires = now + days * 86400
        async with self.transaction():
            await self.db.execute("DELETE FROM profiles WHERE expires_at <= ?", (now,))
            result = await self.db.execute(
                "UPDATE profiles SET age_band = ?, expires_at = ? WHERE id = ?",
                (age_band.value, expires, token_hash(token)),
            )
            if result.rowcount and token is not None:
                return token
            token = token_urlsafe(32)
            await self.db.execute(
                "INSERT INTO profiles(id, age_band, expires_at) VALUES (?, ?, ?)",
                (token_hash(token), age_band.value, expires),
            )
        return token

    async def chats(self, profile: Profile) -> list[ChatSummary]:
        async with self.lock:
            rows = await self.rows(
                "SELECT id, title, updated_at FROM chats WHERE profile_id = ? ORDER BY updated_at DESC",
                (profile.id,),
            )
        return [ChatSummary.model_validate(dict(row)) for row in rows]

    async def create_chat(self, profile: Profile) -> ChatSummary:
        chat = ChatSummary(id=str(uuid4()), title="New conversation", updated_at=timestamp())
        async with self.transaction():
            owner = await self.one(
                "SELECT id FROM profiles WHERE id = ? AND expires_at > ?", (profile.id, int(time()))
            )
            if owner is None:
                raise Missing
            count = await self.one("SELECT COUNT(*) FROM chats WHERE profile_id = ?", (profile.id,))
            if count[0] >= MAX_CHATS:
                raise LimitReached
            await self.db.execute(
                "INSERT INTO chats(id, profile_id, title, updated_at) VALUES (?, ?, ?, ?)",
                (chat.id, profile.id, chat.title, chat.updated_at),
            )
        return chat

    async def chat_row(self, profile: Profile, chat_id: str):
        row = await self.one(
            """SELECT chats.id, title, updated_at, version, age_band FROM chats
               JOIN profiles ON profiles.id = chats.profile_id
               WHERE chats.id = ? AND profile_id = ? AND expires_at > ?""",
            (chat_id, profile.id, int(time())),
        )
        if row is None:
            raise Missing
        return row

    async def chat(self, profile: Profile, chat_id: str) -> tuple[ChatSummary, int, list[StoredTurn]]:
        async with self.lock:
            row = await self.chat_row(profile, chat_id)
            turns = await self.rows("SELECT payload FROM turns WHERE chat_id = ? ORDER BY seq", (chat_id,))
        return (
            ChatSummary.model_validate(dict(row)),
            row["version"],
            [StoredTurn.model_validate_json(turn["payload"]) for turn in turns],
        )

    async def consume_turn(self, profile: Profile) -> None:
        now = int(time())
        async with self.transaction():
            row = await self.one(
                "SELECT turn_window, turns_used FROM profiles WHERE id = ? AND expires_at > ?",
                (profile.id, now),
            )
            if row is None:
                raise Missing
            window = now // 60
            used = row["turns_used"] if row["turn_window"] == window else 0
            if used >= TURN_LIMIT:
                raise TurnRateExceeded
            await self.db.execute(
                "UPDATE profiles SET turn_window = ?, turns_used = ? WHERE id = ?",
                (window, used + 1, profile.id),
            )

    async def save_turn(
        self, profile: Profile, chat_id: str, version: int, turn: StoredTurn
    ) -> StoredTurn:
        async with self.transaction():
            row = await self.chat_row(profile, chat_id)
            previous = await self.one(
                "SELECT payload FROM turns WHERE chat_id = ? AND request_id = ?", (chat_id, turn.id)
            )
            if previous:
                return StoredTurn.model_validate_json(previous["payload"])
            if row["version"] != version or row["age_band"] != profile.age_band:
                raise Conflict
            title = row["title"]
            if title == "New conversation" and not turn.blocked:
                title = " ".join(turn.message.split())
                if len(title) > 58:
                    title = title[:55].rstrip() + "…"
            await self.db.execute(
                "INSERT INTO turns(chat_id, request_id, payload) VALUES (?, ?, ?)",
                (chat_id, turn.id, turn.model_dump_json()),
            )
            await self.db.execute(
                "UPDATE chats SET title = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (title, timestamp(), chat_id),
            )
        return turn

    async def answer(self, profile: Profile, chat_id: str, turn_id: str, option: int) -> StoredTurn:
        async with self.transaction():
            await self.chat_row(profile, chat_id)
            row = await self.one(
                "SELECT payload FROM turns WHERE chat_id = ? AND request_id = ?", (chat_id, turn_id)
            )
            if row is None:
                raise Missing
            turn = StoredTurn.model_validate_json(row["payload"])
            if turn.quiz is None:
                raise Missing
            if option >= len(turn.quiz.options):
                raise ValueError("Invalid quiz option")
            if turn.selected is None:
                turn.selected = option
                await self.db.execute(
                    "UPDATE turns SET payload = ? WHERE chat_id = ? AND request_id = ?",
                    (turn.model_dump_json(), chat_id, turn_id),
                )
        return turn

    async def delete_chat(self, profile: Profile, chat_id: str) -> None:
        async with self.transaction():
            result = await self.db.execute(
                "DELETE FROM chats WHERE profile_id = ? AND id = ?", (profile.id, chat_id)
            )
            if result.rowcount == 0:
                raise Missing

    async def delete_profile(self, token: str | None) -> None:
        async with self.transaction():
            await self.db.execute("DELETE FROM profiles WHERE id = ?", (token_hash(token),))
