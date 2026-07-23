import base64
import datetime as dt
import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from support_bot.admin_bridge import AdminBridgeSettings, AdminSupportBridge
from support_bot.db import Database


JPEG = b"\xff\xd8\xff\xe0" + b"bridge-photo" + b"\xff\xd9"


class AdminSupportBridgeTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3")
        self.db = Database(self.db_file.name)
        await self.db.connect()
        await self.db.init()
        self.bridge = AdminSupportBridge(
            AdminBridgeSettings(
                base_url="http://127.0.0.1:8080",
                token="x" * 64,
                bot_instance_id="00000000-0000-0000-0000-000000000002",
                operator_group_id=-1001,
            )
        )
        self.bridge._request = AsyncMock(return_value={})

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.db_file.close()

    async def test_user_message_is_persisted_until_backend_accepts_it(self) -> None:
        user = SimpleNamespace(
            id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        message = SimpleNamespace(
            from_user=user,
            chat=SimpleNamespace(id=42),
            message_id=10,
            content_type="text",
            text="Нужна помощь",
            caption=None,
            photo=None,
            document=None,
            video=None,
            audio=None,
            voice=None,
            sticker=None,
            animation=None,
            video_note=None,
            is_topic_message=False,
            date=dt.datetime.now(dt.timezone.utc),
        )

        published = await self.bridge.publish_user_message(
            message,
            topic_id=77,
            db=self.db,
        )

        self.assertTrue(published)
        self.bridge._request.assert_awaited_once()
        method, path = self.bridge._request.await_args.args
        self.assertEqual((method, path), ("POST", "/api/v1/support/bridge/events"))
        self.assertEqual(await self.db.list_admin_bridge_events(), [])

    async def test_outbox_delivery_is_recorded_and_acknowledged(self) -> None:
        await self.db.upsert_user(
            user_id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        bot = SimpleNamespace(
            send_message=AsyncMock(
                side_effect=[
                    SimpleNamespace(message_id=100),
                    SimpleNamespace(message_id=101),
                ]
            )
        )

        await self.bridge._deliver_outbox_item(
            bot,
            self.db,
            {
                "id": 5,
                "telegram_user_id": 42,
                "topic_id": 77,
                "text": "Ответ оператора",
            },
        )

        self.assertEqual(
            await self.db.find_admin_bridge_delivery(5),
            (101, 100),
        )
        self.assertEqual(bot.send_message.await_count, 2)
        ack_call = self.bridge._request.await_args
        self.assertEqual(
            ack_call.args,
            ("POST", "/api/v1/support/bridge/outbox/5/ack"),
        )
        self.assertEqual(ack_call.kwargs["json"]["status"], "sent")

    async def test_user_photo_is_embedded_in_bridge_event(self) -> None:
        async def download_photo(_photo, *, destination) -> None:
            destination.write(JPEG)

        user = SimpleNamespace(
            id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        message = SimpleNamespace(
            from_user=user,
            chat=SimpleNamespace(id=42),
            message_id=11,
            content_type="photo",
            text=None,
            caption="Screenshot",
            photo=[SimpleNamespace(file_id="telegram-photo")],
            is_topic_message=False,
            date=dt.datetime.now(dt.timezone.utc),
            bot=SimpleNamespace(download=AsyncMock(side_effect=download_photo)),
        )

        published = await self.bridge.publish_user_message(
            message,
            topic_id=77,
            db=self.db,
        )

        self.assertTrue(published)
        event = self.bridge._request.await_args.kwargs["json"]
        attachment = event["message"]["attachment"]
        self.assertEqual(attachment["mime_type"], "image/jpeg")
        self.assertEqual(attachment["size_bytes"], len(JPEG))
        self.assertEqual(base64.b64decode(attachment["data_base64"]), JPEG)

    async def test_outbox_photo_is_delivered_to_topic_and_private_chat(self) -> None:
        await self.db.upsert_user(
            user_id=42,
            username="ivan",
            first_name="Иван",
            last_name="Иванов",
        )
        topic_message = SimpleNamespace(
            message_id=100,
            photo=[SimpleNamespace(file_id="topic-photo")],
        )
        private_message = SimpleNamespace(
            message_id=101,
            photo=[SimpleNamespace(file_id="private-photo")],
        )
        bot = SimpleNamespace(
            send_photo=AsyncMock(side_effect=[topic_message, private_message]),
            delete_message=AsyncMock(),
        )
        self.bridge._request_bytes = AsyncMock(return_value=JPEG)

        await self.bridge._deliver_outbox_item(
            bot,
            self.db,
            {
                "id": 6,
                "telegram_user_id": 42,
                "topic_id": 77,
                "caption": "Photo from dashboard",
                "has_attachment": True,
                "attachment_name": "photo.jpg",
            },
        )

        self.assertEqual(await self.db.find_admin_bridge_delivery(6), (101, 100))
        self.assertEqual(bot.send_photo.await_count, 2)
        self.assertEqual(bot.send_photo.await_args_list[0].kwargs["message_thread_id"], 77)
        self.assertEqual(bot.send_photo.await_args_list[1].kwargs["chat_id"], 42)
        self.assertEqual(bot.send_photo.await_args_list[1].kwargs["photo"], "topic-photo")
        self.bridge._request_bytes.assert_awaited_once_with(
            "GET",
            "/api/v1/support/bridge/outbox/6/attachment",
            params={"bot_instance_id": "00000000-0000-0000-0000-000000000002"},
        )
        ack_call = self.bridge._request.await_args
        self.assertEqual(ack_call.kwargs["json"]["status"], "sent")
        bot.delete_message.assert_not_awaited()
