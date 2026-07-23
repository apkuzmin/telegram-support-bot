"""Create the headless omnichannel support schema.

Revision ID: 0001_omnichannel
Revises:
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

from support_bot.omnichannel.models import Base


revision = "0001_omnichannel"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
