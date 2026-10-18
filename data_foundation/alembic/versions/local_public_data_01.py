"""Add public-fact preservation tables; this is NOT upstream video migration 0007."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import DATETIME

revision = "local_public_data_01"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    dt = sa.DateTime().with_variant(DATETIME(fsp=6), "mysql", "mariadb")
    op.create_table("public_data_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("summary", sa.JSON, nullable=False))
    op.create_table("public_data_facts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("origin_key", sa.String(128), nullable=False),
        sa.Column("entity_key", sa.String(100), nullable=False),
        sa.Column("entity_name", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(40), nullable=False),
        sa.Column("metric_code", sa.String(100), nullable=False),
        sa.Column("scope", sa.String(200), nullable=False),
        sa.Column("grain", sa.String(32), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("value", sa.Numeric(24, 4), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("quality_status", sa.String(40), nullable=False),
        sa.Column("qualifier", sa.Text),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("source_title", sa.Text, nullable=False),
        sa.Column("published_at", dt),
        sa.Column("provenance", sa.JSON, nullable=False),
        sa.Column("raw_record", sa.JSON, nullable=False),
        sa.Column("previous_versions", sa.JSON, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("observation_id", sa.String(36), sa.ForeignKey("observations.id")),
        sa.Column("import_id", sa.String(36), sa.ForeignKey("public_data_imports.id"), nullable=False),
        sa.Column("imported_at", dt, nullable=False))
    op.create_index("ix_public_data_entity_period", "public_data_facts", ["entity_name", "period_start"])
    op.create_index("ix_public_data_quality_metric", "public_data_facts", ["quality_status", "metric_code"])


def downgrade():
    op.drop_table("public_data_facts")
    op.drop_table("public_data_imports")
