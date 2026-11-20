import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

import aiosqlite
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from groq import AsyncGroq
from langsmith import tracing_context
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import ROOT, Settings
from .schemas import (
    AgeBand, AgeRequest, AnswerRequest, ChatRequest, ChatSummary, ChatView,
    Mode, StoredTurn, TurnView, WorkspaceView,
)
from .services import (
    CompletionProvider, GroqProvider, ProviderUnavailable, bounded_history, build_graph,
)
from .storage import MAX_TURNS, Conflict, LimitReached, Missing, Profile, TurnRateExceeded, WorkspaceStore

COOKIE = "edubot_workspace"
FRONTEND = ROOT / "frontend" / "public"
MAX_BODY_BYTES = 20_000
log = logging.getLogger(__name__)


async def purge_loop(store: WorkspaceStore) -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            await store.purge_expired()
        except aiosqlite.Error:
            log.warning("expired_workspace_cleanup_failed")


async def get_profile(request: Request) -> Profile:
    profile = await request.app.state.store.profile(request.cookies.get(COOKIE))
    if profile is None:
        raise HTTPException(428, "Enter your age to start a learning workspace.")
    return profile


def create_app(settings: Settings | None = None, *, provider: CompletionProvider | None = None) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        async with aiosqlite.connect(settings.database) as connection:
            store = WorkspaceStore(connection)
            await store.setup()
            app.state.store = store
            client = None
            selected = provider
            if selected is None and settings.api_key:
                client = AsyncGroq(api_key=settings.api_key, timeout=8.0, max_retries=1)
                selected = GroqProvider(client)
            app.state.graph = build_graph(selected, settings) if selected is not None else None
            app.state.active = set()
            cleanup = asyncio.create_task(purge_loop(store))
            try:
                yield
            finally:
                cleanup.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup
                if client is not None:
                    await client.close()

    app = FastAPI(title="Evaextra EduBot", version="2.0.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    hosts = [urlsplit(settings.origin).hostname] if settings.origin else ["localhost", "127.0.0.1"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts, www_redirect=False)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        api = request.url.path.startswith("/api/")
        error = None
        if api and request.method in {"POST", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = settings.origin or f"{request.url.scheme}://{request.url.netloc}"
            if origin is not None and origin != allowed:
                error = JSONResponse({"detail": "This request must come from EduBot."}, status_code=403)
            elif request.method in {"POST", "PUT"}:
                length = request.headers.get("content-length")
                media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
                if media_type != "application/json":
                    error = JSONResponse({"detail": "Use a JSON request body."}, status_code=415)
                elif length is None or not length.isascii() or not length.isdecimal():
                    error = JSONResponse({"detail": "Content-Length is required."}, status_code=411)
                elif len(length) > 5 or int(length) > MAX_BODY_BYTES:
                    error = JSONResponse({"detail": "Request is too large."}, status_code=413)
        response = error if error is not None else await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "object-src 'none'; form-action 'self'"
        )
        if api or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        return JSONResponse({"detail": "Check the submitted fields and try again."}, status_code=422)

    @app.exception_handler(Missing)
    async def missing(_request: Request, _error: Missing):
        return JSONResponse({"detail": "That conversation or question is unavailable."}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(_request: Request, _error: Conflict):
        return JSONResponse({"detail": "The workspace changed. Please try your message again."}, status_code=409)

    @app.exception_handler(aiosqlite.Error)
    async def database_error(_request: Request, error: aiosqlite.Error):
        log.error("workspace_database_error type=%s", type(error).__name__)
        return JSONResponse({"detail": "Workspace storage is temporarily unavailable."}, status_code=503)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(request: Request):
        store = request.app.state.store
        async with store.lock:
            await store.one("SELECT 1")
        if request.app.state.graph is None:
            raise HTTPException(503, "Groq configuration is required.")
        return {"status": "ready"}

    @app.get("/api/session", response_model=WorkspaceView)
    async def workspace(request: Request):
        store = request.app.state.store
        profile = await store.profile(request.cookies.get(COOKIE))
        return WorkspaceView(
            age_band=profile.age_band if profile else None,
            chats=await store.chats(profile) if profile else [],
            tutor_configured=request.app.state.graph is not None,
        )

    @app.put("/api/session", response_model=WorkspaceView)
    async def set_age(body: AgeRequest, request: Request, response: Response):
        store = request.app.state.store
        token = await store.set_age(
            request.cookies.get(COOKIE), AgeBand.for_age(body.age), settings.session_days
        )
        response.set_cookie(
            COOKIE,
            token,
            max_age=settings.session_days * 86400,
            httponly=True,
            secure=(settings.origin or request.url.scheme).startswith("https"),
            samesite="strict",
        )
        profile = await store.profile(token)
        if profile is None:
            raise Conflict
        return await workspace_view(request, profile)

    async def workspace_view(request: Request, profile: Profile) -> WorkspaceView:
        return WorkspaceView(
            age_band=profile.age_band,
            chats=await request.app.state.store.chats(profile),
            tutor_configured=request.app.state.graph is not None,
        )

    @app.delete("/api/session", status_code=204)
    async def delete_session(request: Request):
        await request.app.state.store.delete_profile(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(
            COOKIE, httponly=True, samesite="strict",
            secure=(settings.origin or request.url.scheme).startswith("https"),
        )
        return response

    @app.post("/api/chats", response_model=ChatSummary, status_code=201)
    async def new_chat(request: Request, profile: Profile = Depends(get_profile)):
        try:
            return await request.app.state.store.create_chat(profile)
        except LimitReached:
            raise HTTPException(409, "Your workspace has 30 conversations. Delete one to make room.") from None

    @app.get("/api/chats/{chat_id}", response_model=ChatView)
    async def get_chat(chat_id: UUID, request: Request, profile: Profile = Depends(get_profile)):
        chat, _, turns = await request.app.state.store.chat(profile, str(chat_id))
        return ChatView(**chat.model_dump(), turns=[turn.public() for turn in turns])

    @app.delete("/api/chats/{chat_id}", status_code=204)
    async def delete_chat(chat_id: UUID, request: Request, profile: Profile = Depends(get_profile)):
        await request.app.state.store.delete_chat(profile, str(chat_id))
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id}/quiz/{turn_id}", response_model=TurnView)
    async def answer_quiz(
        chat_id: UUID, turn_id: UUID, body: AnswerRequest,
        request: Request, profile: Profile = Depends(get_profile),
    ):
        try:
            turn = await request.app.state.store.answer(profile, str(chat_id), str(turn_id), body.option)
        except ValueError:
            raise HTTPException(422, "Choose one of the displayed answers.") from None
        return turn.public()

    @app.post("/api/chat", response_model=TurnView)
    async def chat(body: ChatRequest, request: Request, profile: Profile = Depends(get_profile)):
        store = request.app.state.store
        chat_id = str(body.chat_id)
        request_id = str(body.request_id)
        _, version, turns = await store.chat(profile, chat_id)
        previous = next((turn for turn in turns if turn.id == request_id), None)
        if previous is not None:
            return previous.public()
        if len(turns) >= MAX_TURNS:
            raise HTTPException(409, "This conversation has reached 100 messages. Start a new conversation.")
        graph = request.app.state.graph
        if graph is None:
            raise HTTPException(503, "The tutor needs a Groq API key on the server.")
        active = request.app.state.active
        if profile.id in active:
            raise HTTPException(409, "A message is already being processed in this workspace.")
        if len(active) >= settings.max_concurrent_turns:
            raise HTTPException(503, "The tutor is busy. Give it a moment and try again.")
        active.add(profile.id)
        started = monotonic()
        try:
            await store.consume_turn(profile)
            with tracing_context(enabled=False):
                async with asyncio.timeout(55):
                    result = await graph.ainvoke(
                        {
                            "age_band": profile.age_band, "message": body.message,
                            "requested_mode": body.mode, "mode": Mode.EXPLAIN,
                            "history": bounded_history(turns), "revisions": 0,
                        },
                        config={"recursion_limit": 16},
                    )
            blocked = result.get("blocked", False)
            turn = StoredTurn(
                id=request_id,
                message="Message screened" if blocked else body.message,
                reply=result["reply"],
                mode=result["mode"],
                steps=result["steps"],
                quiz=result.get("quiz"),
                blocked=blocked,
            )
            saved = await store.save_turn(profile, chat_id, version, turn)
            log.info("tutor_turn mode=%s blocked=%s duration_ms=%d", saved.mode, saved.blocked, (monotonic() - started) * 1000)
            return saved.public()
        except TurnRateExceeded:
            raise HTTPException(429, "You’ve sent several messages recently. Try again in a minute.",
                                headers={"Retry-After": "60"}) from None
        except (ProviderUnavailable, TimeoutError):
            raise HTTPException(503, "The tutor couldn’t complete the checks. Your draft is ready to retry.") from None
        finally:
            active.discard(profile.id)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND / "index.html")

    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")
    return app


app = create_app()
