"""create local uninfo tables

迁移 ID: fxbot_uninfo_tables
父迁移:
创建时间: 2026-05-22 13:50:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "fxbot_uninfo_tables"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("fxbot_uninfo_tables",)
depends_on: str | Sequence[str] | None = None


def upgrade(name: str = "") -> None:
    if name:
        return

    existing = set(inspect(op.get_bind()).get_table_names())

    if "nonebot_plugin_uninfo_botmodel" not in existing:
        op.create_table(
            "nonebot_plugin_uninfo_botmodel",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("self_id", sa.String(length=64), nullable=False),
            sa.Column("adapter", sa.String(length=32), nullable=False),
            sa.Column("scope", sa.String(length=32), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_nonebot_plugin_uninfo_botmodel")),
            sa.UniqueConstraint("self_id", "adapter", name="nonebot_plugin_uninfo_unique_bot"),
        )
    if "nonebot_plugin_uninfo_scenemodel" not in existing:
        op.create_table(
            "nonebot_plugin_uninfo_scenemodel",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("bot_persist_id", sa.Integer(), nullable=False),
            sa.Column("parent_scene_persist_id", sa.Integer(), nullable=True),
            sa.Column("scene_id", sa.String(length=64), nullable=False),
            sa.Column("scene_type", sa.Integer(), nullable=False),
            sa.Column("scene_data", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_nonebot_plugin_uninfo_scenemodel")),
            sa.UniqueConstraint(
                "bot_persist_id",
                "scene_id",
                "scene_type",
                name="nonebot_plugin_uninfo_unique_scene",
            ),
        )
    if "nonebot_plugin_uninfo_usermodel" not in existing:
        op.create_table(
            "nonebot_plugin_uninfo_usermodel",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("bot_persist_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("user_data", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_nonebot_plugin_uninfo_usermodel")),
            sa.UniqueConstraint("bot_persist_id", "user_id", name="nonebot_plugin_uninfo_unique_user"),
        )
    if "nonebot_plugin_uninfo_sessionmodel" not in existing:
        op.create_table(
            "nonebot_plugin_uninfo_sessionmodel",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("bot_persist_id", sa.Integer(), nullable=False),
            sa.Column("scene_persist_id", sa.Integer(), nullable=False),
            sa.Column("user_persist_id", sa.Integer(), nullable=False),
            sa.Column("member_data", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_nonebot_plugin_uninfo_sessionmodel")),
            sa.UniqueConstraint(
                "bot_persist_id",
                "scene_persist_id",
                "user_persist_id",
                name="nonebot_plugin_uninfo_unique_session",
            ),
        )


def downgrade(name: str = "") -> None:
    if name:
        return

    existing = set(inspect(op.get_bind()).get_table_names())
    if "nonebot_plugin_uninfo_sessionmodel" in existing:
        op.drop_table("nonebot_plugin_uninfo_sessionmodel")
    if "nonebot_plugin_uninfo_usermodel" in existing:
        op.drop_table("nonebot_plugin_uninfo_usermodel")
    if "nonebot_plugin_uninfo_scenemodel" in existing:
        op.drop_table("nonebot_plugin_uninfo_scenemodel")
    if "nonebot_plugin_uninfo_botmodel" in existing:
        op.drop_table("nonebot_plugin_uninfo_botmodel")
