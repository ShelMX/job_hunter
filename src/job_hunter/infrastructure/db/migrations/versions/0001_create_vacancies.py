"""initial schema: vacancies

Revision ID: 0001
Revises:
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vacancies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("employer_name", sa.String(length=256), nullable=False),
        sa.Column("employer_url", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(length=8), nullable=True),
        sa.Column("skills_json", sa.Text(), nullable=False),
        sa.Column("locations_json", sa.Text(), nullable=False),
        sa.Column("remote", sa.String(length=16), nullable=False),
        sa.Column("employment", sa.String(length=16), nullable=False),
        sa.Column("seniority", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_vacancy_source_ext"),
    )
    op.create_index("ix_vacancies_source", "vacancies", ["source"])
    op.create_index("ix_vacancies_published_at", "vacancies", ["published_at"])


def downgrade() -> None:
    op.drop_table("vacancies")
