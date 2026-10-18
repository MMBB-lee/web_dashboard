"""Lossless imported public facts, separate from comparable business series."""
import sqlalchemy as sa
from .base import Base, PRECISE_DATETIME


public_data_imports = sa.Table(
    "public_data_imports", Base.metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("created_at", PRECISE_DATETIME, nullable=False),
    sa.Column("summary", sa.JSON, nullable=False),
)

public_data_facts = sa.Table(
    "public_data_facts", Base.metadata,
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
    sa.Column("published_at", PRECISE_DATETIME),
    sa.Column("provenance", sa.JSON, nullable=False),
    sa.Column("raw_record", sa.JSON, nullable=False),
    sa.Column("previous_versions", sa.JSON, nullable=False),
    sa.Column("content_sha256", sa.String(64), nullable=False),
    sa.Column("observation_id", sa.String(36), sa.ForeignKey("observations.id")),
    sa.Column("import_id", sa.String(36), sa.ForeignKey("public_data_imports.id"), nullable=False),
    sa.Column("imported_at", PRECISE_DATETIME, nullable=False),
    sa.Index("ix_public_data_entity_period", "entity_name", "period_start"),
    sa.Index("ix_public_data_quality_metric", "quality_status", "metric_code"),
)
