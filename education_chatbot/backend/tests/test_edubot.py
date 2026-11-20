import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.config import FAST, TUTOR, Settings
from backend.main import COOKIE, create_app
from backend.services import Draft, ProviderUnavailable, Review, Route, SafetyDecision, calculate


class ScriptedProvider:
    def __init__(self):
        self.calls = []
        self.revision = False
        self.output_safety = "allow"
        self.fail_primary = False
        self.fail_safety = False
        self.delay = 0

    async def complete(self, model, schema, system, payload, tokens):
        self.calls.append((model, schema.__name__, payload))
        if self.delay:
            await asyncio.sleep(self.delay)
        if schema is SafetyDecision:
            if self.fail_safety:
                raise ProviderUnavailable
            if "reply" in payload:
                decision = self.output_safety
            elif payload["latest_question"] == "private content rejected by the provider":
                decision = "decline"
            else:
                decision = "allow"
            return SafetyDecision(decision=decision)
        if schema is Route:
            mode = payload["selected_mode"]
            return Route(mode="explain" if mode == "auto" else mode, calculation="2 + 3")
        if schema is Draft:
            if self.fail_primary and model == TUTOR:
                raise ProviderUnavailable
            return Draft(
                reply="Try this **example**. $2 + 3 = 5$",
                question="What is 2 + 3?",
                options=["4", "5", "6"],
                correct_index=1,
                explanation="Adding three to two gives five.",
            )
        if schema is Review:
            return Review(decision="revise" if self.revision else "approve", feedback="Check the example.")
        raise AssertionError(f"Unexpected role: {schema}")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(database=Path(self.directory.name) / "test.sqlite3")
        self.provider = ScriptedProvider()
        self.app = create_app(self.settings, provider=self.provider)
        self.client = self.enterContext(TestClient(self.app, base_url="http://localhost"))

    def age(self, age=11):
        response = self.client.put("/api/session", json={"age": age})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def new_chat(self):
        response = self.client.post("/api/chats", json={})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def send(self, chat_id, request_id=None, message="What is 2 + 3?", mode="auto"):
        return self.client.post("/api/chat", json={
            "chat_id": chat_id, "request_id": request_id or str(uuid4()),
            "message": message, "mode": mode,
        })

    def test_age_gate_validation_persistence_and_privacy(self):
        self.assertIsNone(self.client.get("/api/session").json()["age_band"])
        denied = self.send(str(uuid4()))
        self.assertEqual(denied.status_code, 428)
        self.assertEqual(self.client.post("/api/chats", json={}).status_code, 428)
        self.assertEqual(self.client.put("/api/session", json={"age": "11"}).status_code, 422)
        self.assertEqual(self.client.put("/api/session", json={"age": 4}).status_code, 422)
        response = self.age(11)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        self.assertEqual(response.json()["age_band"], "9–12")
        token = self.client.cookies.get(COOKIE)
        with sqlite3.connect(self.settings.database) as database:
            profile_id, band = database.execute("SELECT id, age_band FROM profiles").fetchone()
        self.assertNotEqual(profile_id, token)
        self.assertEqual(band, "9–12")
        with TestClient(create_app(self.settings, provider=self.provider), base_url="http://localhost") as restarted:
            restarted.cookies.set(COOKIE, token)
            self.assertEqual(restarted.get("/api/session").json()["age_band"], "9–12")
        self.assertFalse(self.provider.calls)

    def test_routed_models_calculator_context_and_idempotency(self):
        self.age(8)
        chat = self.new_chat()
        request_id = str(uuid4())
        first = self.send(chat, request_id)
        self.assertEqual(first.status_code, 200, first.text)
        data = first.json()
        self.assertIn("Arithmetic calculated", data["steps"])
        self.assertEqual(data["mode"], "explain")
        self.assertIsNone(data["quiz"])
        self.assertEqual([model for model, _, _ in self.provider.calls], [FAST, FAST, TUTOR, FAST, FAST])
        specialist = self.provider.calls[2][2]
        self.assertEqual(specialist["age_group"], "5–8")
        self.assertEqual(specialist["calculator"]["result"], "5")
        self.assertEqual(self.send(chat, request_id).json(), data)
        self.assertEqual(len(self.provider.calls), 5)
        self.age(18)
        follow_up = self.send(chat, message="Can you show again?")
        self.assertEqual(follow_up.status_code, 200, follow_up.text)
        self.assertEqual(self.provider.calls[7][2]["age_group"], "18+")
        self.assertEqual(self.provider.calls[7][2]["earlier_turns"][0]["question"], "What is 2 + 3?")
        self.assertEqual(len(self.client.get(f"/api/chats/{chat}").json()["turns"]), 2)

    def test_quiz_answer_stays_private_until_graded(self):
        self.age()
        chat = self.new_chat()
        response = self.send(chat, mode="practice")
        self.assertEqual(response.status_code, 200, response.text)
        quiz = response.json()["quiz"]
        self.assertNotIn("correct_index", quiz)
        self.assertIsNone(quiz["result"])
        self.assertEqual(self.provider.calls[2][0], FAST)
        endpoint = f"/api/chats/{chat}/quiz/{response.json()['id']}"
        self.assertEqual(self.client.post(endpoint, json={"option": 3}).status_code, 422)
        grade = self.client.post(endpoint, json={"option": 0}).json()["quiz"]["result"]
        self.assertEqual((grade["selected"], grade["correct"]), (0, 1))
        self.assertEqual(self.client.post(endpoint, json={"option": 1}).json()["quiz"]["result"], grade)
        stored = self.client.get(f"/api/chats/{chat}").json()["turns"][0]["quiz"]["result"]
        self.assertEqual(stored, grade)

    def test_unsafe_input_skips_specialists_and_is_not_stored(self):
        self.age()
        chat = self.new_chat()
        response = self.send(chat, message="private content rejected by the provider")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["blocked"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertNotIn("private content", self.client.get(f"/api/chats/{chat}").text)

    def test_unsafe_output_is_withheld(self):
        self.age()
        chat = self.new_chat()
        self.provider.output_safety = "decline"
        response = self.send(chat, mode="practice")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["quiz"])
        self.assertIn("Draft withheld", response.json()["steps"])
        self.assertNotIn("Try this", response.text)

    def test_review_revision_is_bounded(self):
        self.age()
        chat = self.new_chat()
        self.provider.revision = True
        result = self.send(chat, mode="plan")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("Draft withheld", result.json()["steps"])
        drafts = [payload for _, role, payload in self.provider.calls if role == "Draft"]
        self.assertEqual(len(drafts), 2)
        self.assertIn("review_feedback", drafts[1])

    def test_generation_fallback_and_safety_failure(self):
        self.age()
        chat = self.new_chat()
        self.provider.fail_primary = True
        result = self.send(chat)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn("Backup model used", result.json()["steps"])
        self.provider.fail_safety = True
        result = self.send(chat, message="Can you try another example?")
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(len(self.client.get(f"/api/chats/{chat}").json()["turns"]), 1)

    def test_workspace_isolation_cross_origin_and_deletion(self):
        self.age()
        chat = self.new_chat()
        self.assertEqual(self.send(chat).status_code, 200)
        self.assertEqual(self.client.delete("/api/session", headers={"Origin": "https://untrusted.example"}).status_code, 403)
        with TestClient(create_app(self.settings, provider=self.provider), base_url="http://localhost") as other:
            other.put("/api/session", json={"age": 17})
            self.assertEqual(other.get(f"/api/chats/{chat}").status_code, 404)
        self.assertEqual(self.client.delete("/api/session").status_code, 204)
        self.assertEqual(self.client.get("/api/session").json()["chats"], [])
        with sqlite3.connect(self.settings.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)

    def test_security_headers_and_input_size(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        oversized = self.client.put("/api/session", json={"age": "x" * 21_000})
        self.assertEqual(oversized.status_code, 413)

    def test_calculator_bounds(self):
        self.assertEqual(calculate("(2 + .5) / 3"), "5/6")
        self.assertEqual(calculate("2^5"), "32")
        for invalid in ("4 / 0", "2**100", "2 // 1", "id(5)", "[" * 100, "8**.5"):
            with self.subTest(expression=invalid):
                self.assertIsNone(calculate(invalid))


if __name__ == "__main__":
    unittest.main()
