from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text

from support_bot.omnichannel.auth import AuthClaims, AuthError, TokenSigner
from support_bot.omnichannel.enums import (
    Channel,
    ConversationStatus,
    SenderType,
)
from support_bot.omnichannel.files import (
    FileTooLargeError,
    LocalFileStore,
    UnsafeFileTypeError,
)
from support_bot.omnichannel.realtime import (
    PostgresRealtimeListener,
    RealtimeHub,
)
from support_bot.omnichannel.schemas import (
    ConversationPage,
    ConversationPatch,
    ConversationView,
    DeliveryView,
    HealthResponse,
    MessageCreate,
    MessageEdit,
    MessagePage,
    MessageView,
    ReadUpdate,
    ReadState,
    WidgetSessionRequest,
    WidgetSessionResponse,
)
from support_bot.omnichannel.service import SupportService
from support_bot.omnichannel.settings import OmnichannelSettings
from support_bot.omnichannel.storage import OmnichannelStore


log = logging.getLogger(__name__)


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
        )
    return authorization[7:].strip()


def _web_origin(subject: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"{subject}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"web:{digest}"


def create_app(
    settings: OmnichannelSettings | None = None,
    *,
    store: OmnichannelStore | None = None,
    realtime: RealtimeHub | None = None,
) -> FastAPI:
    settings = settings or OmnichannelSettings.from_env()
    owned_store = store is None
    store = store or OmnichannelStore(settings.database_url)
    realtime = realtime or RealtimeHub()
    service = SupportService(store, realtime)
    signer = TokenSigner(settings.auth_secret)
    files = LocalFileStore(
        settings.upload_dir,
        max_bytes=settings.max_upload_bytes,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.environment == "development":
            await store.create_schema()
        stop_event = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []

        async def maintain() -> None:
            while not stop_event.is_set():
                now = dt.datetime.now(dt.timezone.utc)
                try:
                    cleanup_targets = await store.cleanup_expired(
                        realtime_before=now
                        - dt.timedelta(
                            seconds=settings.realtime_retention_seconds
                        ),
                        outbox_before=now
                        - dt.timedelta(
                            seconds=settings.outbox_retention_seconds
                        ),
                        unused_files_before=now
                        - dt.timedelta(
                            seconds=settings.unused_file_retention_seconds
                        ),
                    )
                    deleted_ids: list[str] = []
                    failed_ids: list[str] = []
                    for target in cleanup_targets:
                        try:
                            files.delete(target.storage_key)
                            deleted_ids.append(target.id)
                        except OSError:
                            failed_ids.append(target.id)
                            log.exception(
                                "Failed to delete expired support file %s",
                                target.storage_key,
                            )
                    await store.finish_file_cleanup(deleted_ids)
                    await store.release_file_cleanup(failed_ids)
                except Exception:
                    log.exception("Support maintenance failed")
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=settings.maintenance_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass

        tasks.append(asyncio.create_task(maintain()))
        if store.engine.dialect.name == "postgresql":
            listener = PostgresRealtimeListener(
                settings.database_url,
                realtime,
            )
            tasks.append(asyncio.create_task(listener.run(stop_event)))
        try:
            yield
        finally:
            stop_event.set()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if owned_store:
                await store.close()

    app = FastAPI(
        title="Omnichannel Support API",
        version="1.0.0",
        description=(
            "Headless API for website and Telegram support channels. "
            "All clients render their own UI."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url="/redoc" if settings.expose_docs else None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.signer = signer
    app.state.realtime = realtime
    app.state.files = files

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )
    if settings.trusted_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=list(settings.trusted_hosts),
        )

    def customer_claims(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthClaims:
        try:
            return signer.verify(
                _bearer_token(authorization),
                allowed_roles={"customer"},
            )
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    def operator_claims(
        authorization: Annotated[str | None, Header()] = None,
    ) -> AuthClaims:
        try:
            return signer.verify(
                _bearer_token(authorization),
                allowed_roles={"operator", "admin"},
            )
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    async def require_conversation(
        conversation_id: str,
        claims: AuthClaims,
        *,
        operator: bool,
    ):
        conversation = await store.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if not operator and claims.conversation_id != conversation_id:
            raise HTTPException(status_code=403, detail="Conversation access denied")
        return conversation

    async def resolve_attachments(
        attachment_ids: list[str], *, customer_id: str | None
    ) -> list[dict[str, object]]:
        stored = await store.get_files(
            attachment_ids,
            customer_id=customer_id,
        )
        if len(stored) != len(set(attachment_ids)):
            raise HTTPException(
                status_code=400,
                detail="One or more attachments are unavailable",
            )
        return [
            {
                "id": item.id,
                "name": item.original_name,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in stored
        ]

    async def message_views(
        messages,
        *,
        include_delivery_errors: bool = False,
    ) -> list[MessageView]:
        deliveries = await store.list_deliveries_for_messages(
            [message.id for message in messages]
        )
        by_message: dict[str, list[DeliveryView]] = {}
        for delivery in deliveries:
            by_message.setdefault(delivery.message_id, []).append(
                DeliveryView(
                    id=delivery.id,
                    channel=delivery.channel,
                    target=delivery.target,
                    status=delivery.status,
                    attempts=delivery.attempts,
                    external_message_id=delivery.external_message_id,
                    last_error=(
                        delivery.last_error if include_delivery_errors else None
                    ),
                )
            )
        return [
            MessageView.model_validate(message).model_copy(
                update={
                    "deliveries": by_message.get(message.id, []),
                    "structured_content": message.metadata_json.get(
                        "structured_content"
                    ),
                }
            )
            for message in messages
        ]

    async def conversation_views(conversations) -> list[ConversationView]:
        customers = await store.get_customers(
            [conversation.customer_id for conversation in conversations]
        )
        names = {customer.id: customer.display_name for customer in customers}
        return [
            ConversationView.model_validate(conversation).model_copy(
                update={
                    "customer_display_name": names.get(
                        conversation.customer_id
                    ),
                    "last_sequence": max(0, conversation.next_sequence - 1),
                }
            )
            for conversation in conversations
        ]

    async def save_upload(
        upload: UploadFile,
        *,
        customer_id: str,
    ) -> dict[str, object]:
        async def chunks() -> AsyncIterator[bytes]:
            while chunk := await upload.read(64 * 1024):
                yield chunk

        try:
            saved = await files.save(
                filename=upload.filename,
                content_type=upload.content_type,
                chunks=chunks(),
            )
        except FileTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except UnsafeFileTypeError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        finally:
            await upload.close()
        try:
            stored = await store.create_stored_file(
                customer_id=customer_id,
                original_name=saved.original_name,
                content_type=saved.content_type,
                size_bytes=saved.size_bytes,
                sha256=saved.sha256,
                storage_key=saved.storage_key,
            )
        except BaseException:
            files.delete(saved.storage_key)
            raise
        return {
            "id": stored.id,
            "name": stored.original_name,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        async with store.sessions() as session:
            await session.execute(text("SELECT 1"))
        return HealthResponse(status="ok")

    @app.post(
        "/api/v1/widget/sessions",
        response_model=WidgetSessionResponse,
        status_code=201,
        tags=["customer"],
    )
    async def create_widget_session(
        payload: WidgetSessionRequest,
    ) -> WidgetSessionResponse:
        if payload.identity_token and payload.resume_token:
            raise HTTPException(
                status_code=400,
                detail="Use either identity_token or resume_token",
            )
        if payload.resume_token:
            try:
                previous = signer.verify(
                    payload.resume_token,
                    allowed_roles={"customer"},
                )
            except AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            if previous.conversation_id is None:
                raise HTTPException(status_code=401, detail="Invalid resume token")
            conversation = await store.get_conversation(previous.conversation_id)
            customer = await store.get_customer(previous.subject)
            if (
                conversation is None
                or customer is None
                or conversation.customer_id != customer.id
            ):
                raise HTTPException(status_code=401, detail="Invalid resume token")
            if conversation.status == ConversationStatus.CLOSED.value:
                identity = await store.get_identity(
                    customer.id,
                    Channel.WEB_USER,
                )
                if identity is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Invalid resume token",
                    )
                resumed = await service.create_web_session(
                    external_user_id=identity.external_id,
                    display_name=customer.display_name,
                    metadata=payload.metadata,
                )
                customer = resumed.context.customer
                conversation = resumed.context.conversation
            token = signer.issue(
                subject=customer.id,
                role="customer",
                conversation_id=conversation.id,
                ttl_seconds=settings.token_ttl_seconds,
            )
            return WidgetSessionResponse(
                token=token,
                customer_id=customer.id,
                conversation_id=conversation.id,
                expires_in=settings.token_ttl_seconds,
            )

        external_user_id = None
        if payload.identity_token:
            try:
                identity = signer.verify(
                    payload.identity_token,
                    allowed_roles={"identity"},
                )
            except AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            external_user_id = f"site:{identity.subject}"
        created = await service.create_web_session(
            external_user_id=external_user_id,
            display_name=payload.display_name,
            metadata=payload.metadata,
        )
        context = created.context
        token = signer.issue(
            subject=context.customer.id,
            role="customer",
            conversation_id=context.conversation.id,
            ttl_seconds=settings.token_ttl_seconds,
        )
        return WidgetSessionResponse(
            token=token,
            customer_id=context.customer.id,
            conversation_id=context.conversation.id,
            expires_in=settings.token_ttl_seconds,
        )

    @app.get(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=MessagePage,
        tags=["customer", "operator"],
    )
    async def list_customer_messages(
        conversation_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        claims: AuthClaims = Depends(customer_claims),
    ) -> MessagePage:
        await require_conversation(
            conversation_id, claims, operator=False
        )
        messages = await store.list_messages(
            conversation_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return MessagePage(
            items=await message_views(messages),
            next_after_sequence=messages[-1].sequence if messages else after_sequence,
        )

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=MessageView,
        status_code=201,
        tags=["customer"],
    )
    async def send_customer_message(
        conversation_id: str,
        payload: MessageCreate,
        claims: AuthClaims = Depends(customer_claims),
    ) -> MessageView:
        conversation = await require_conversation(
            conversation_id, claims, operator=False
        )
        if conversation.status == ConversationStatus.CLOSED.value:
            raise HTTPException(
                status_code=409,
                detail="Conversation is closed; create or resume a session",
            )
        if not payload.text and not payload.attachment_ids:
            raise HTTPException(status_code=400, detail="Message is empty")
        attachments = await resolve_attachments(
            payload.attachment_ids,
            customer_id=conversation.customer_id,
        )
        try:
            message, _ = await service.create_message(
                conversation=conversation,
                sender_type=SenderType.CUSTOMER,
                sender_id=claims.subject,
                origin_channel=Channel.WEB_USER,
                origin_external_id=_web_origin(
                    claims.subject,
                    payload.idempotency_key,
                ),
                text=payload.text,
                reply_to_message_id=payload.reply_to_message_id,
                attachments=attachments,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return (await message_views([message]))[0]

    @app.patch(
        "/api/v1/conversations/{conversation_id}/messages/{message_id}",
        response_model=MessageView,
        tags=["customer"],
    )
    async def edit_customer_message(
        conversation_id: str,
        message_id: str,
        payload: MessageEdit,
        claims: AuthClaims = Depends(customer_claims),
    ) -> MessageView:
        await require_conversation(conversation_id, claims, operator=False)
        message = await store.get_message(message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.sender_type != SenderType.CUSTOMER.value
            or message.sender_id != claims.subject
            or message.origin_channel != Channel.WEB_USER.value
        ):
            raise HTTPException(status_code=404, detail="Message not found")
        updated = await store.update_message_text(
            message_id,
            text_value=payload.text,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Message not found")
        await realtime.publish(
            {
                "operators",
                f"conversation:{conversation_id}",
                f"customer:{claims.subject}",
            },
            {"type": "wake"},
        )
        return (await message_views([updated]))[0]

    @app.post(
        "/api/v1/conversations/{conversation_id}/read",
        status_code=204,
        tags=["customer"],
    )
    async def mark_customer_read(
        conversation_id: str,
        payload: ReadUpdate,
        claims: AuthClaims = Depends(customer_claims),
    ) -> None:
        conversation = await require_conversation(
            conversation_id,
            claims,
            operator=False,
        )
        if payload.last_sequence >= conversation.next_sequence:
            raise HTTPException(
                status_code=400,
                detail="Read sequence is beyond conversation history",
            )
        await store.mark_read(
            conversation_id,
            f"customer:{claims.subject}",
            payload.last_sequence,
        )
        await realtime.publish(
            {"operators", f"conversation:{conversation_id}"},
            {"type": "wake"},
        )

    @app.get(
        "/api/v1/conversations/{conversation_id}/read",
        response_model=ReadState,
        tags=["customer"],
    )
    async def get_customer_read_state(
        conversation_id: str,
        claims: AuthClaims = Depends(customer_claims),
    ) -> ReadState:
        await require_conversation(conversation_id, claims, operator=False)
        customer, operator = await store.get_read_state(conversation_id)
        return ReadState(
            customer_last_sequence=customer,
            operator_last_sequence=operator,
        )

    @app.post(
        "/api/v1/files",
        status_code=201,
        tags=["customer"],
    )
    async def upload_file(
        upload: Annotated[UploadFile, File()],
        claims: AuthClaims = Depends(customer_claims),
    ) -> dict[str, object]:
        if claims.conversation_id is None:
            raise HTTPException(status_code=403, detail="Conversation access denied")
        conversation = await require_conversation(
            claims.conversation_id, claims, operator=False
        )

        return await save_upload(
            upload,
            customer_id=conversation.customer_id,
        )

    @app.get("/api/v1/files/{file_id}", tags=["customer", "operator"])
    async def download_file(
        file_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> FileResponse:
        token = _bearer_token(authorization)
        try:
            claims = signer.verify(
                token, allowed_roles={"customer", "operator", "admin"}
            )
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        stored_items = await store.get_files([file_id])
        if not stored_items:
            raise HTTPException(status_code=404, detail="File not found")
        stored = stored_items[0]
        if claims.role == "customer" and stored.customer_id != claims.subject:
            raise HTTPException(status_code=403, detail="File access denied")
        return FileResponse(
            files.path_for(stored.storage_key),
            filename=stored.original_name,
            media_type=stored.content_type,
        )

    @app.get(
        "/api/v1/operator/conversations",
        response_model=ConversationPage,
        tags=["operator"],
    )
    async def list_operator_conversations(
        conversation_status: ConversationStatus | None = Query(
            default=None, alias="status"
        ),
        search: str | None = Query(default=None, max_length=255),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        claims: AuthClaims = Depends(operator_claims),
    ) -> ConversationPage:
        del claims
        conversations = await store.list_conversations(
            status=conversation_status,
            search=search,
            limit=limit + 1,
            offset=offset,
        )
        has_more = len(conversations) > limit
        return ConversationPage(
            items=await conversation_views(conversations[:limit]),
            next_offset=(offset + limit if has_more else None),
        )

    @app.get(
        "/api/v1/operator/conversations/{conversation_id}",
        response_model=ConversationView,
        tags=["operator"],
    )
    async def get_operator_conversation(
        conversation_id: str,
        claims: AuthClaims = Depends(operator_claims),
    ) -> ConversationView:
        conversation = await require_conversation(
            conversation_id,
            claims,
            operator=True,
        )
        return (await conversation_views([conversation]))[0]

    @app.get(
        "/api/v1/operator/conversations/{conversation_id}/messages",
        response_model=MessagePage,
        tags=["operator"],
    )
    async def list_operator_messages(
        conversation_id: str,
        after_sequence: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=200),
        claims: AuthClaims = Depends(operator_claims),
    ) -> MessagePage:
        await require_conversation(conversation_id, claims, operator=True)
        messages = await store.list_messages(
            conversation_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return MessagePage(
            items=await message_views(
                messages,
                include_delivery_errors=True,
            ),
            next_after_sequence=messages[-1].sequence if messages else after_sequence,
        )

    @app.post(
        "/api/v1/operator/conversations/{conversation_id}/messages",
        response_model=MessageView,
        status_code=201,
        tags=["operator"],
    )
    async def send_operator_message(
        conversation_id: str,
        payload: MessageCreate,
        claims: AuthClaims = Depends(operator_claims),
    ) -> MessageView:
        conversation = await require_conversation(
            conversation_id, claims, operator=True
        )
        if conversation.status == ConversationStatus.CLOSED.value:
            raise HTTPException(
                status_code=409,
                detail="Conversation is closed; reopen it before replying",
            )
        if not payload.text and not payload.attachment_ids:
            raise HTTPException(status_code=400, detail="Message is empty")
        attachments = await resolve_attachments(
            payload.attachment_ids,
            customer_id=conversation.customer_id,
        )
        try:
            message, _ = await service.create_message(
                conversation=conversation,
                sender_type=SenderType.OPERATOR,
                sender_id=claims.subject,
                origin_channel=Channel.WEB_OPERATOR,
                origin_external_id=_web_origin(
                    claims.subject,
                    payload.idempotency_key,
                ),
                text=payload.text,
                reply_to_message_id=payload.reply_to_message_id,
                attachments=attachments,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return (
            await message_views([message], include_delivery_errors=True)
        )[0]

    @app.patch(
        "/api/v1/operator/conversations/{conversation_id}/messages/{message_id}",
        response_model=MessageView,
        tags=["operator"],
    )
    async def edit_operator_message(
        conversation_id: str,
        message_id: str,
        payload: MessageEdit,
        claims: AuthClaims = Depends(operator_claims),
    ) -> MessageView:
        await require_conversation(conversation_id, claims, operator=True)
        message = await store.get_message(message_id)
        if (
            message is None
            or message.conversation_id != conversation_id
            or message.sender_type != SenderType.OPERATOR.value
            or message.sender_id != claims.subject
            or message.origin_channel != Channel.WEB_OPERATOR.value
        ):
            raise HTTPException(status_code=404, detail="Message not found")
        updated = await store.update_message_text(
            message_id,
            text_value=payload.text,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Message not found")
        await realtime.publish(
            {"operators", f"conversation:{conversation_id}"},
            {"type": "wake"},
        )
        return (
            await message_views([updated], include_delivery_errors=True)
        )[0]

    @app.post(
        "/api/v1/operator/conversations/{conversation_id}/files",
        status_code=201,
        tags=["operator"],
    )
    async def upload_operator_file(
        conversation_id: str,
        upload: Annotated[UploadFile, File()],
        claims: AuthClaims = Depends(operator_claims),
    ) -> dict[str, object]:
        conversation = await require_conversation(
            conversation_id,
            claims,
            operator=True,
        )
        return await save_upload(
            upload,
            customer_id=conversation.customer_id,
        )

    @app.patch(
        "/api/v1/operator/conversations/{conversation_id}",
        response_model=ConversationView,
        tags=["operator"],
    )
    async def patch_conversation(
        conversation_id: str,
        payload: ConversationPatch,
        claims: AuthClaims = Depends(operator_claims),
    ) -> ConversationView:
        await require_conversation(conversation_id, claims, operator=True)
        conversation = await store.update_conversation(
            conversation_id,
            status=payload.status,
            assigned_operator_id=payload.assigned_operator_id,
            update_assignment="assigned_operator_id" in payload.model_fields_set,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        await realtime.publish(
            {"operators", f"conversation:{conversation_id}"},
            {
                "type": "conversation.updated",
                "conversation_id": conversation_id,
            },
        )
        return (await conversation_views([conversation]))[0]

    @app.post(
        "/api/v1/operator/conversations/{conversation_id}/read",
        status_code=204,
        tags=["operator"],
    )
    async def mark_operator_read(
        conversation_id: str,
        payload: ReadUpdate,
        claims: AuthClaims = Depends(operator_claims),
    ) -> None:
        conversation = await require_conversation(
            conversation_id,
            claims,
            operator=True,
        )
        if payload.last_sequence >= conversation.next_sequence:
            raise HTTPException(
                status_code=400,
                detail="Read sequence is beyond conversation history",
            )
        await store.mark_read(
            conversation_id,
            f"operator:{claims.subject}",
            payload.last_sequence,
        )
        await realtime.publish(
            {"operators", f"conversation:{conversation_id}"},
            {"type": "wake"},
        )

    @app.post(
        "/api/v1/operator/deliveries/{delivery_id}/retry",
        response_model=DeliveryView,
        tags=["operator"],
    )
    async def retry_delivery(
        delivery_id: str,
        claims: AuthClaims = Depends(operator_claims),
    ) -> DeliveryView:
        del claims
        try:
            delivery = await store.retry_delivery(delivery_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Delivery not found"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return DeliveryView(
            id=delivery.id,
            channel=delivery.channel,
            target=delivery.target,
            status=delivery.status,
            attempts=delivery.attempts,
            external_message_id=delivery.external_message_id,
            last_error=delivery.last_error,
        )

    @app.get(
        "/api/v1/operator/conversations/{conversation_id}/read",
        response_model=ReadState,
        tags=["operator"],
    )
    async def get_operator_read_state(
        conversation_id: str,
        claims: AuthClaims = Depends(operator_claims),
    ) -> ReadState:
        await require_conversation(conversation_id, claims, operator=True)
        customer, operator = await store.get_read_state(conversation_id)
        return ReadState(
            customer_last_sequence=customer,
            operator_last_sequence=operator,
        )

    @app.websocket("/api/v1/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        after_event_id: int | None = Query(default=None, ge=0),
    ) -> None:
        origin = websocket.headers.get("origin")
        if (
            settings.allowed_origins
            and origin is not None
            and origin not in settings.allowed_origins
        ):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=5,
            )
            if (
                not isinstance(auth_message, dict)
                or auth_message.get("type") != "auth"
            ):
                raise AuthError("WebSocket auth message required")
            token = str(auth_message.get("token", ""))
            claims = signer.verify(
                token,
                allowed_roles={"customer", "operator", "admin"},
            )
        except (AuthError, asyncio.TimeoutError, ValueError, TypeError):
            await websocket.close(code=4401)
            return
        topics = {"operators"} if claims.role in {"operator", "admin"} else set()
        if claims.conversation_id:
            topics.add(f"conversation:{claims.conversation_id}")
        topics.add(f"customer:{claims.subject}")
        cursor = (
            await store.latest_realtime_event_id()
            if after_event_id is None
            else after_event_id
        )
        await websocket.send_json({"type": "ready", "event_id": cursor})
        last_client_activity = time.monotonic()
        active_wait_tasks: set[asyncio.Task] = set()
        try:
            async with realtime.subscribe(topics | {"*"}) as signals:
                needs_fetch = True
                while True:
                    if int(time.time()) >= claims.expires_at:
                        await websocket.close(code=4401)
                        return
                    idle_left = settings.websocket_idle_seconds - (
                        time.monotonic() - last_client_activity
                    )
                    token_left = claims.expires_at - int(time.time())
                    if idle_left <= 0:
                        await websocket.close(code=1000)
                        return
                    if token_left <= 0:
                        await websocket.close(code=4401)
                        return

                    if needs_fetch:
                        while True:
                            events = await store.list_realtime_events(
                                after_id=cursor,
                            )
                            for event in events:
                                cursor = max(cursor, event.id)
                                if topics.intersection(event.topics_json):
                                    await websocket.send_json(
                                        {
                                            **event.payload_json,
                                            "event_id": event.id,
                                        }
                                    )
                            if len(events) < 100:
                                break
                        needs_fetch = False

                    receive_task = asyncio.create_task(
                        websocket.receive_text()
                    )
                    signal_task = asyncio.create_task(signals.get())
                    wait_tasks = {receive_task, signal_task}
                    active_wait_tasks = wait_tasks
                    try:
                        done, pending = await asyncio.wait(
                            wait_tasks,
                            timeout=min(idle_left, token_left),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except BaseException:
                        for task in wait_tasks:
                            task.cancel()
                        await asyncio.gather(
                            *wait_tasks,
                            return_exceptions=True,
                        )
                        raise
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(
                            *pending,
                            return_exceptions=True,
                        )
                    if not done:
                        continue
                    if signal_task in done:
                        signal_task.result()
                        needs_fetch = True
                    if receive_task in done:
                        value = receive_task.result()
                        if value == "ping":
                            last_client_activity = time.monotonic()
                            await websocket.send_text("pong")
                    active_wait_tasks = set()
        except (WebSocketDisconnect, asyncio.CancelledError):
            return
        finally:
            for task in active_wait_tasks:
                task.cancel()
            if active_wait_tasks:
                await asyncio.gather(
                    *active_wait_tasks,
                    return_exceptions=True,
                )

    return app
