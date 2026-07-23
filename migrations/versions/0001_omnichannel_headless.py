"""Create the headless omnichannel support schema.

Revision ID: 0001_omnichannel
Revises:
Create Date: 2026-07-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0001_omnichannel"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_customers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("display_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "support_channel_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("support_customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "channel",
            "external_id",
            name="uq_support_identity",
        ),
    )
    op.create_table(
        "support_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("support_customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("customer_channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assigned_operator_id", sa.String(255)),
        sa.Column("telegram_topic_id", sa.BigInteger()),
        sa.Column("next_sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_support_conversations_status_updated",
        "support_conversations",
        ["status", "updated_at"],
    )
    op.create_table(
        "support_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("support_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sender_type", sa.String(16), nullable=False),
        sa.Column("sender_id", sa.String(255)),
        sa.Column("origin_channel", sa.String(32), nullable=False),
        sa.Column("origin_external_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column(
            "reply_to_message_id",
            sa.String(36),
            sa.ForeignKey("support_messages.id", ondelete="SET NULL"),
        ),
        sa.Column("attachments_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "conversation_id",
            "origin_channel",
            "origin_external_id",
            name="uq_support_message_origin",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_support_message_sequence",
        ),
    )
    op.create_index(
        "ix_support_messages_conversation_sequence",
        "support_messages",
        ["conversation_id", "sequence"],
    )
    op.create_table(
        "support_message_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("support_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_chat_id", sa.String(255)),
        sa.Column("external_message_id", sa.String(255)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "message_id",
            "channel",
            "target",
            name="uq_support_delivery",
        ),
        sa.UniqueConstraint(
            "channel",
            "external_chat_id",
            "external_message_id",
            name="uq_support_external_delivery",
        ),
    )
    op.create_index(
        "ix_support_delivery_due",
        "support_message_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_table(
        "support_outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_support_outbox_due",
        "support_outbox",
        ["status", "available_at"],
    )
    op.create_table(
        "support_conversation_reads",
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("support_conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("actor_key", sa.String(255), primary_key=True),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "support_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(36),
            sa.ForeignKey("support_customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False, unique=True),
        sa.Column("attached_at", sa.DateTime(timezone=True)),
        sa.Column("cleanup_claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "support_realtime_events",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("topics_json", sa.JSON(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION support_notify_realtime_event()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              PERFORM pg_notify('support_realtime', NEW.id::text);
              RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER support_realtime_event_notify
            AFTER INSERT ON support_realtime_events
            FOR EACH ROW
            EXECUTE FUNCTION support_notify_realtime_event()
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS support_realtime_event_notify "
            "ON support_realtime_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS support_notify_realtime_event()"
        )

    op.drop_table("support_realtime_events")
    op.drop_table("support_files")
    op.drop_table("support_conversation_reads")
    op.drop_index("ix_support_outbox_due", table_name="support_outbox")
    op.drop_table("support_outbox")
    op.drop_index(
        "ix_support_delivery_due",
        table_name="support_message_deliveries",
    )
    op.drop_table("support_message_deliveries")
    op.drop_index(
        "ix_support_messages_conversation_sequence",
        table_name="support_messages",
    )
    op.drop_table("support_messages")
    op.drop_index(
        "ix_support_conversations_status_updated",
        table_name="support_conversations",
    )
    op.drop_table("support_conversations")
    op.drop_table("support_channel_identities")
    op.drop_table("support_customers")
