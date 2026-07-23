import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from support_bot.omnichannel.api import _web_origin, create_app
from support_bot.omnichannel.enums import Channel, SenderType
from support_bot.omnichannel.settings import OmnichannelSettings
from support_bot.omnichannel.storage import OmnichannelStore


class OmnichannelApiTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "support.sqlite3"
        settings = OmnichannelSettings(
            database_url=f"sqlite+aiosqlite:///{db_path}",
            auth_secret="0123456789abcdef0123456789abcdef",
            upload_dir=str(Path(self.temp_dir.name) / "uploads"),
        )
        self.store = OmnichannelStore(settings.database_url)
        self.app = create_app(settings, store=self.store)
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _session(self, **payload):
        response = self.client.post("/api/v1/widget/sessions", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _operator_token(self) -> str:
        return self.app.state.signer.issue(
            subject="operator-1",
            role="operator",
            ttl_seconds=3600,
        )

    def test_customer_and_operator_round_trip_with_idempotency(self) -> None:
        session = self._session(display_name="Иван")
        customer_headers = {"Authorization": f"Bearer {session['token']}"}
        conversation_id = session["conversation_id"]
        payload = {
            "text": "Не работает вход",
            "idempotency_key": "customer-request-0001",
        }
        first = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=customer_headers,
            json=payload,
        )
        duplicate = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=customer_headers,
            json=payload,
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["id"], duplicate.json()["id"])
        edited = self.client.patch(
            (
                f"/api/v1/conversations/{conversation_id}/messages/"
                f"{first.json()['id']}"
            ),
            headers=customer_headers,
            json={"text": "Не работает вход в кабинет"},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertIsNotNone(edited.json()["edited_at"])

        operator_headers = {
            "Authorization": f"Bearer {self._operator_token()}"
        }
        conversations = self.client.get(
            "/api/v1/operator/conversations",
            headers=operator_headers,
        )
        self.assertEqual(conversations.status_code, 200)
        self.assertEqual(conversations.json()["items"][0]["id"], conversation_id)

        reply = self.client.post(
            f"/api/v1/operator/conversations/{conversation_id}/messages",
            headers=operator_headers,
            json={
                "text": "Проверяем",
                "reply_to_message_id": first.json()["id"],
                "idempotency_key": "operator-request-0001",
            },
        )
        self.assertEqual(reply.status_code, 201, reply.text)
        self.assertEqual(reply.json()["sequence"], 2)

        history = self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=customer_headers,
        )
        self.assertEqual(history.status_code, 200)
        self.assertEqual(
            [item["text"] for item in history.json()["items"]],
            ["Не работает вход в кабинет", "Проверяем"],
        )

    def test_read_state_and_operator_conversation_update(self) -> None:
        session = self._session(display_name="Иван")
        conversation_id = session["conversation_id"]
        customer_headers = {
            "Authorization": f"Bearer {session['token']}"
        }
        sent = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=customer_headers,
            json={
                "text": "Вопрос",
                "idempotency_key": "customer-read-state-1",
            },
        )
        self.assertEqual(sent.status_code, 201, sent.text)
        marked = self.client.post(
            f"/api/v1/conversations/{conversation_id}/read",
            headers=customer_headers,
            json={"last_sequence": 1},
        )
        self.assertEqual(marked.status_code, 204, marked.text)
        beyond_history = self.client.post(
            f"/api/v1/conversations/{conversation_id}/read",
            headers=customer_headers,
            json={"last_sequence": 999},
        )
        self.assertEqual(beyond_history.status_code, 400)

        operator_headers = {
            "Authorization": f"Bearer {self._operator_token()}"
        }
        operator_marked = self.client.post(
            f"/api/v1/operator/conversations/{conversation_id}/read",
            headers=operator_headers,
            json={"last_sequence": 1},
        )
        self.assertEqual(operator_marked.status_code, 204)
        read_state = self.client.get(
            f"/api/v1/operator/conversations/{conversation_id}/read",
            headers=operator_headers,
        )
        self.assertEqual(
            read_state.json(),
            {
                "customer_last_sequence": 1,
                "operator_last_sequence": 1,
            },
        )

        updated = self.client.patch(
            f"/api/v1/operator/conversations/{conversation_id}",
            headers=operator_headers,
            json={
                "status": "closed",
                "assigned_operator_id": "operator-1",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["status"], "closed")
        self.assertEqual(
            updated.json()["assigned_operator_id"],
            "operator-1",
        )

    def test_customer_cannot_access_another_conversation(self) -> None:
        one = self._session(display_name="Первый")
        two = self._session(display_name="Второй")
        response = self.client.get(
            f"/api/v1/conversations/{two['conversation_id']}/messages",
            headers={"Authorization": f"Bearer {one['token']}"},
        )
        self.assertEqual(response.status_code, 403)
        page = self.client.get(
            "/api/v1/operator/conversations?limit=1&offset=0",
            headers={
                "Authorization": f"Bearer {self._operator_token()}"
            },
        )
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(len(page.json()["items"]), 1)
        self.assertEqual(page.json()["next_offset"], 1)

    def test_signed_site_identity_reuses_conversation(self) -> None:
        identity = self.app.state.signer.issue(
            subject="site-account-42",
            role="identity",
            ttl_seconds=3600,
        )
        first = self._session(identity_token=identity, display_name="Иван")
        second = self._session(identity_token=identity, display_name="Иван")
        self.assertEqual(first["customer_id"], second["customer_id"])
        self.assertEqual(first["conversation_id"], second["conversation_id"])

    def test_resume_token_reissues_access_to_same_conversation(self) -> None:
        first = self._session(display_name="Гость")
        resumed = self._session(resume_token=first["token"])
        self.assertEqual(first["customer_id"], resumed["customer_id"])
        self.assertEqual(first["conversation_id"], resumed["conversation_id"])

    def test_resume_closed_conversation_creates_a_new_dialog(self) -> None:
        first = self._session(display_name="Гость")
        operator_headers = {
            "Authorization": f"Bearer {self._operator_token()}"
        }
        closed = self.client.patch(
            f"/api/v1/operator/conversations/{first['conversation_id']}",
            headers=operator_headers,
            json={"status": "closed"},
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        rejected = self.client.post(
            f"/api/v1/conversations/{first['conversation_id']}/messages",
            headers={"Authorization": f"Bearer {first['token']}"},
            json={
                "text": "Новый вопрос",
                "idempotency_key": "closed-conversation-message",
            },
        )
        self.assertEqual(rejected.status_code, 409, rejected.text)

        resumed = self._session(resume_token=first["token"])
        self.assertEqual(first["customer_id"], resumed["customer_id"])
        self.assertNotEqual(
            first["conversation_id"],
            resumed["conversation_id"],
        )

    def test_file_upload_and_download_are_scoped_to_customer(self) -> None:
        session = self._session(display_name="Иван")
        headers = {"Authorization": f"Bearer {session['token']}"}
        upload = self.client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": ("problem.txt", b"details", "text/plain")},
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        file_id = upload.json()["id"]
        download = self.client.get(
            f"/api/v1/files/{file_id}",
            headers=headers,
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"details")

        other = self._session(display_name="Другой")
        denied = self.client.get(
            f"/api/v1/files/{file_id}",
            headers={"Authorization": f"Bearer {other['token']}"},
        )
        self.assertEqual(denied.status_code, 403)

        unsafe = self.client.post(
            "/api/v1/files",
            headers=headers,
            files={"upload": ("page.html", b"<html></html>", "text/html")},
        )
        self.assertEqual(unsafe.status_code, 415)
        disguised = self.client.post(
            "/api/v1/files",
            headers=headers,
            files={
                "upload": (
                    "page.txt",
                    b" \n<!doctype html><html></html>",
                    "text/plain",
                )
            },
        )
        self.assertEqual(disguised.status_code, 415)

    def test_failed_file_metadata_write_removes_physical_file(self) -> None:
        session = self._session(display_name="Иван")
        self.app.state.store.create_stored_file = AsyncMock(
            side_effect=RuntimeError("database unavailable")
        )
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            self.client.post(
                "/api/v1/files",
                headers={
                    "Authorization": f"Bearer {session['token']}"
                },
                files={"upload": ("problem.txt", b"details", "text/plain")},
            )
        upload_dir = Path(self.temp_dir.name) / "uploads"
        self.assertEqual(list(upload_dir.iterdir()), [])

    def test_write_models_reject_unknown_fields(self) -> None:
        response = self.client.post(
            "/api/v1/widget/sessions",
            json={"display_name": "Иван", "is_admin": True},
        )
        self.assertEqual(response.status_code, 422)
        oversized = self.client.post(
            "/api/v1/widget/sessions",
            json={"metadata": {"value": "x" * (8 * 1024)}},
        )
        self.assertEqual(oversized.status_code, 422)

    def test_web_origin_is_bounded_for_maximum_input(self) -> None:
        origin = _web_origin("operator-" + ("x" * 1000), "k" * 255)
        self.assertLessEqual(len(origin), 255)
        self.assertEqual(origin, _web_origin("operator-" + ("x" * 1000), "k" * 255))
        self.assertNotEqual(origin, _web_origin("other", "k" * 255))

    def test_structured_telegram_content_is_returned_by_rest_api(self) -> None:
        session = self._session(display_name="Иван")

        async def seed_structured_message() -> None:
            conversation = await self.store.get_conversation(
                session["conversation_id"]
            )
            await self.app.state.service.create_message(
                conversation=conversation,
                sender_type=SenderType.OPERATOR,
                sender_id="telegram-operator",
                origin_channel=Channel.TELEGRAM_OPERATOR,
                origin_external_id="-1001:44",
                text=None,
                metadata={
                    "structured_content": {
                        "type": "location",
                        "data": {
                            "latitude": 55.75,
                            "longitude": 37.61,
                        },
                    }
                },
            )

        self.client.portal.call(seed_structured_message)
        history = self.client.get(
            f"/api/v1/conversations/{session['conversation_id']}/messages",
            headers={"Authorization": f"Bearer {session['token']}"},
        )
        self.assertEqual(history.status_code, 200, history.text)
        message = history.json()["items"][0]
        self.assertEqual(message["kind"], "structured")
        self.assertEqual(
            message["structured_content"]["data"]["longitude"],
            37.61,
        )

    def test_websocket_rejects_non_object_auth_frame(self) -> None:
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect("/api/v1/ws") as websocket:
                websocket.send_json([])
                websocket.receive_json()
        self.assertEqual(caught.exception.code, 4401)

    def test_websocket_receives_new_message_signal(self) -> None:
        session = self._session(display_name="Иван")
        with self.client.websocket_connect(
            "/api/v1/ws"
        ) as websocket:
            websocket.send_json(
                {"type": "auth", "token": session["token"]}
            )
            ready = websocket.receive_json()
            self.assertEqual(ready["type"], "ready")
            self.assertEqual(ready["event_id"], 0)
            response = self.client.post(
                f"/api/v1/conversations/{session['conversation_id']}/messages",
                headers={"Authorization": f"Bearer {session['token']}"},
                json={
                    "text": "Онлайн",
                    "idempotency_key": "customer-request-ws-1",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            event = websocket.receive_json()
            self.assertEqual(event["type"], "message.created")
            self.assertEqual(event["sequence"], 1)

    def test_websocket_reconnect_replays_events_after_cursor(self) -> None:
        session = self._session(display_name="Иван")
        cursor = 0

        response = self.client.post(
            f"/api/v1/conversations/{session['conversation_id']}/messages",
            headers={"Authorization": f"Bearer {session['token']}"},
            json={
                "text": "Сообщение во время разрыва",
                "idempotency_key": "customer-reconnect-event-1",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

        with self.client.websocket_connect(
            f"/api/v1/ws?after_event_id={cursor}"
        ) as websocket:
            websocket.send_json(
                {"type": "auth", "token": session["token"]}
            )
            ready = websocket.receive_json()
            self.assertEqual(ready["event_id"], cursor)
            replayed = websocket.receive_json()
            self.assertEqual(replayed["type"], "message.created")
            self.assertEqual(
                replayed["message_id"],
                response.json()["id"],
            )
            self.assertGreater(replayed["event_id"], cursor)

    def test_idle_websocket_does_not_poll_database(self) -> None:
        session = self._session(display_name="Иван")
        original = self.store.list_realtime_events
        self.store.list_realtime_events = AsyncMock(wraps=original)
        with self.client.websocket_connect("/api/v1/ws") as websocket:
            websocket.send_json(
                {"type": "auth", "token": session["token"]}
            )
            self.assertEqual(websocket.receive_json()["type"], "ready")
            time.sleep(1.1)
            websocket.send_text("ping")
            self.assertEqual(websocket.receive_text(), "pong")
            self.assertLessEqual(
                self.store.list_realtime_events.await_count,
                2,
            )
