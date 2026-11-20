from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class AgeBand(StrEnum):
    EARLY = "5–8"
    PRIMARY = "9–12"
    YOUNG_TEEN = "13–15"
    TEEN = "16–17"
    ADULT = "18+"

    @classmethod
    def for_age(cls, age: int) -> "AgeBand":
        for upper, band in zip((8, 12, 15, 17, 120), cls):
            if age <= upper:
                return band
        raise ValueError("Invalid age")


class Mode(StrEnum):
    AUTO = "auto"
    EXPLAIN = "explain"
    PRACTICE = "practice"
    PLAN = "plan"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgeRequest(Input):
    age: Annotated[int, Field(strict=True, ge=5, le=120)]


class ChatRequest(Input):
    chat_id: UUID
    request_id: UUID
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2500)]
    mode: Mode = Mode.AUTO


class AnswerRequest(Input):
    option: Annotated[int, Field(strict=True, ge=0, le=3)]


class Quiz(BaseModel):
    question: str
    options: list[str]
    correct_index: int
    explanation: str


class QuizResult(BaseModel):
    selected: int
    correct: int
    explanation: str


class QuizView(BaseModel):
    question: str
    options: list[str]
    result: QuizResult | None


class TurnView(BaseModel):
    id: str
    message: str
    reply: str
    mode: Mode
    steps: list[str]
    quiz: QuizView | None
    blocked: bool


class StoredTurn(BaseModel):
    id: str
    message: str
    reply: str
    mode: Mode
    steps: list[str]
    quiz: Quiz | None = None
    blocked: bool = False
    selected: int | None = None

    def public(self) -> TurnView:
        quiz_view = None
        if self.quiz:
            result = None
            if self.selected is not None:
                result = QuizResult(
                    selected=self.selected,
                    correct=self.quiz.correct_index,
                    explanation=self.quiz.explanation,
                )
            quiz_view = QuizView(question=self.quiz.question, options=self.quiz.options, result=result)
        return TurnView(
            id=self.id,
            message=self.message,
            reply=self.reply,
            mode=self.mode,
            steps=self.steps,
            quiz=quiz_view,
            blocked=self.blocked,
        )


class ChatSummary(BaseModel):
    id: str
    title: str
    updated_at: str


class ChatView(ChatSummary):
    turns: list[TurnView]


class WorkspaceView(BaseModel):
    age_band: AgeBand | None
    chats: list[ChatSummary]
    tutor_configured: bool

