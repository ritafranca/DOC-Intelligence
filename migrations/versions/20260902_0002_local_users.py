"""Add local users and RBAC roles.

Revision ID: 20260902_0002
Revises: 20260901_0001
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260902_0002"
down_revision: Union[str, None] = "20260901_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

user_role = sa.Enum(
    "ADMIN",
    "OPERATOR",
    name="userrole",
    native_enum=False,
    length=20,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email_unique", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email_unique", table_name="users")
    op.drop_table("users")
