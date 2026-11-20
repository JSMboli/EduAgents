import re
from unicodedata import normalize

INTRODUCTION = "I’m EduBot, Evaextra’s AI learning assistant."
CREATOR = "EduBot is an Evaextra project."
RELEASE_DATE = "I don’t have a verified creation or launch date for EduBot."
CAPABILITIES = "I can explain topics, give you practice questions, and help you plan your studies."

PUBLIC_IDENTITY = (
    f"Public product facts for questions about this assistant. {INTRODUCTION} {CREATOR} "
    f"{RELEASE_DATE} {CAPABILITIES} Use these facts for self-identification. "
    "Do not invent people, affiliations, release dates, or model versions. Use a friendly, "
    "age-appropriate explanation. Ordinary questions about EduBot’s name, creator, and capabilities "
    "are allowed. Treat educational questions about other AI products separately."
)

PRIVATE_REQUEST_REPLY = (
    "I can explain what EduBot does and how AI tutors work. Private instructions and credentials stay private."
)

PUBLIC_ANSWERS = {
    "who are you": INTRODUCTION,
    "who is edubot": INTRODUCTION,
    "what are you": INTRODUCTION,
    "what is your name": INTRODUCTION,
    "what's your name": INTRODUCTION,
    "are you human": INTRODUCTION,
    "are you a real person": INTRODUCTION,
    "are you chatgpt": INTRODUCTION,
    "who made you": CREATOR,
    "who created you": CREATOR,
    "who built you": CREATOR,
    "who developed you": CREATOR,
    "who is your creator": CREATOR,
    "who is your developer": CREATOR,
    "what company made you": CREATOR,
    "who made edubot": CREATOR,
    "when were you made": RELEASE_DATE,
    "when were you created": RELEASE_DATE,
    "when were you built": RELEASE_DATE,
    "when were you launched": RELEASE_DATE,
    "when was edubot launched": RELEASE_DATE,
    "what is your release date": RELEASE_DATE,
    "what's your release date": RELEASE_DATE,
    "what can you do": CAPABILITIES,
    "how can you help me": CAPABILITIES,
}

PRIVATE_REQUEST = re.compile(
    r"(?:^|[.!?;]\s*)(?:please\s+)?"
    r"(?:(?:can|could|would)\s+you\s+)?"
    r"(?:show|reveal|print|repeat|share|disclose|expose|dump|give|tell|what\s+is|what's)\s+"
    r"(?:me\s+)?(?:your|edubot's)\s+"
    r"(?:(?:hidden|private|internal|full|exact|original|verbatim)\s+)*"
    r"(?:system\s+(?:prompt|message|instructions)|developer\s+(?:prompt|message|instructions)|"
    r"(?:groq\s+)?api\s+key|(?:session|access|auth)\s+token|password)\b"
)


def normalized_message(message: str) -> str:
    text = normalize("NFKC", message).casefold().replace("’", "'")
    return " ".join(text.split())


def public_identity_reply(message: str) -> str | None:
    """Reply locally only when the complete message consists of public identity questions."""
    text = normalized_message(message)
    questions = [part.strip() for part in re.split(r"[?!\.]+|\band\b", text) if part.strip()]
    if not questions or any(question not in PUBLIC_ANSWERS for question in questions):
        return None
    return " ".join(dict.fromkeys(PUBLIC_ANSWERS[question] for question in questions))


def requests_private_information(message: str) -> bool:
    return PRIVATE_REQUEST.search(normalized_message(message)) is not None
