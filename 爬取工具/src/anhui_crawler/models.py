from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Meta(Base):
    __tablename__ = "ac_meta"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class Entity(Base):
    __tablename__ = "ac_entities"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON)


class Source(Base):
    __tablename__ = "ac_sources"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str] = mapped_column(String(255))
    details: Mapped[dict] = mapped_column(JSON)


class Asset(Base):
    __tablename__ = "ac_assets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("ac_sources.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer)
    relative_path: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(80))
    retrieved_at: Mapped[str] = mapped_column(String(50))
    published_at: Mapped[str | None] = mapped_column(String(50))
    details: Mapped[dict] = mapped_column(JSON)


class Run(Base):
    __tablename__ = "ac_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[str] = mapped_column(String(50))
    finished_at: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class Fact(Base):
    __tablename__ = "ac_facts"
    # Business-key hash: entity, metric, scope, grain, exact period, source.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("ac_entities.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("ac_sources.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("ac_assets.id"))
    metric: Mapped[str] = mapped_column(String(100), index=True)
    scope: Mapped[str] = mapped_column(String(100))
    grain: Mapped[str] = mapped_column(String(30))
    period_start: Mapped[object] = mapped_column(Date)
    period_end: Mapped[object] = mapped_column(Date)
    value: Mapped[object] = mapped_column(Numeric(24, 4))
    unit: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(30))
    qualifier: Mapped[str | None] = mapped_column(String(50))
    original_value: Mapped[str] = mapped_column(String(100))
    locator: Mapped[dict] = mapped_column(JSON)
    parser_version: Mapped[str] = mapped_column(String(80))
    revision_no: Mapped[int] = mapped_column(Integer, default=1)


class Revision(Base):
    __tablename__ = "ac_revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fact_id: Mapped[str] = mapped_column(ForeignKey("ac_facts.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ac_runs.id"))
    previous: Mapped[dict] = mapped_column(JSON)
    replacement: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[str] = mapped_column(String(50))


class Issue(Base):
    __tablename__ = "ac_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("ac_runs.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(50))


class SourceState(Base):
    __tablename__ = "ac_source_state"
    source_id: Mapped[str] = mapped_column(ForeignKey("ac_sources.id"), primary_key=True)
    last_checked: Mapped[str | None] = mapped_column(String(50))
    last_success: Mapped[str | None] = mapped_column(String(50))
    next_due: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(40), default="pending")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class FetchState(Base):
    __tablename__ = "ac_fetch_state"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("ac_sources.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("ac_assets.id"))
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[str] = mapped_column(String(50))
    retry_at: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
