from dataclasses import dataclass, field
from os import environ
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
FAST = "openai/gpt-oss-20b"
TUTOR = "openai/gpt-oss-120b"


@dataclass(frozen=True)
class Settings:
    database: Path
    api_key: str = field(default="", repr=False)
    router_model: str = FAST
    tutor_model: str = TUTOR
    practice_model: str = FAST
    coach_model: str = TUTOR
    review_model: str = FAST
    safety_model: str = FAST
    origin: str | None = None
    session_days: int = 30
    max_concurrent_turns: int = 8

    @classmethod
    def load(cls) -> "Settings":
        env = {
            **dotenv_values(ROOT / "backend" / ".env"),
            **dotenv_values(ROOT / ".env"),
            **environ,
        }
        origin = (env.get("APP_ORIGIN") or "").rstrip("/") or None
        if origin:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("APP_ORIGIN must be an HTTP(S) origin without a path.")

        database = Path(env.get("DATABASE_PATH") or "data/edubot.sqlite3")
        return cls(
            database=ROOT / database,
            api_key=(env.get("GROQ_API_KEY") or "").strip(),
            router_model=env.get("GROQ_ROUTER_MODEL") or FAST,
            tutor_model=env.get("GROQ_TUTOR_MODEL") or env.get("GROQ_MODEL") or TUTOR,
            practice_model=env.get("GROQ_PRACTICE_MODEL") or FAST,
            coach_model=env.get("GROQ_COACH_MODEL") or TUTOR,
            review_model=env.get("GROQ_REVIEW_MODEL") or FAST,
            safety_model=env.get("GROQ_SAFETY_MODEL") or FAST,
            origin=origin,
        )
