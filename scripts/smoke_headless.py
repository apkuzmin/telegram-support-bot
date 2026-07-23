from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from websockets.asyncio.client import connect

from support_bot.omnichannel.auth import TokenSigner


async def run(base_url: str, secret: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        health = await client.get("/health")
        health.raise_for_status()
        assert health.json() == {"status": "ok"}

        session_response = await client.post(
            "/api/v1/widget/sessions",
            json={"display_name": "Smoke customer"},
        )
        session_response.raise_for_status()
        session = session_response.json()
        customer_headers = {
            "Authorization": f"Bearer {session['token']}"
        }

        ws_url = (
            base_url.replace("http://", "ws://").replace("https://", "wss://")
            + "/api/v1/ws"
        )
        async with connect(ws_url) as websocket:
            await websocket.send(
                json.dumps({"type": "auth", "token": session["token"]})
            )
            ready = json.loads(await websocket.recv())
            assert ready["type"] == "ready"
            customer_message = await client.post(
                f"/api/v1/conversations/{session['conversation_id']}/messages",
                headers=customer_headers,
                json={
                    "text": "Smoke message",
                    "idempotency_key": "smoke-customer-message-1",
                },
            )
            customer_message.raise_for_status()
            event = json.loads(await asyncio.wait_for(websocket.recv(), 5))
            assert event["type"] == "message.created"

        operator_token = TokenSigner(secret).issue(
            subject="smoke-operator",
            role="operator",
            ttl_seconds=300,
        )
        operator_headers = {
            "Authorization": f"Bearer {operator_token}"
        }
        conversations = await client.get(
            "/api/v1/operator/conversations",
            headers=operator_headers,
        )
        conversations.raise_for_status()
        assert any(
            item["id"] == session["conversation_id"]
            for item in conversations.json()["items"]
        )

        operator_message = await client.post(
            (
                "/api/v1/operator/conversations/"
                f"{session['conversation_id']}/messages"
            ),
            headers=operator_headers,
            json={
                "text": "Smoke reply",
                "reply_to_message_id": customer_message.json()["id"],
                "idempotency_key": "smoke-operator-message-1",
            },
        )
        operator_message.raise_for_status()

        history = await client.get(
            f"/api/v1/conversations/{session['conversation_id']}/messages",
            headers=customer_headers,
        )
        history.raise_for_status()
        assert [item["text"] for item in history.json()["items"]] == [
            "Smoke message",
            "Smoke reply",
        ]
        print(
            json.dumps(
                {
                    "status": "ok",
                    "conversation_id": session["conversation_id"],
                    "messages": 2,
                    "websocket": True,
                },
                sort_keys=True,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--secret", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.base_url.rstrip("/"), args.secret))


if __name__ == "__main__":
    main()
