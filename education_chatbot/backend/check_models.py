"""Report account-specific availability without generating any completions."""
import asyncio
import sys

from groq import AsyncGroq

from .config import Settings
from .services import GroqProvider, ProviderUnavailable


async def main() -> int:
    settings = Settings.load()
    if not settings.api_key:
        print("Set GROQ_API_KEY to check your project’s available models.", file=sys.stderr)
        return 1
    try:
        async with AsyncGroq(api_key=settings.api_key, timeout=8, max_retries=1) as client:
            available = await GroqProvider(client).available_models()
    except ProviderUnavailable:
        print("Groq model catalog is unavailable. Check the key and connectivity.", file=sys.stderr)
        return 1
    missing = False
    for role in ("router", "tutor", "practice", "coach", "review", "safety"):
        model = getattr(settings, f"{role}_model")
        exists = model in available
        print(f"{role:9} {model:34} {'available' if exists else 'unavailable'}")
        missing |= not exists
    return int(missing)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
