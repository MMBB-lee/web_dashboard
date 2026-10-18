"""E-side intelligent analysis services."""

from .integration import ServiceBindings, configure_services
from .service import (
    analyze_video,
    answer_message,
    forecast_from_count_history,
    forecast_short_term,
    get_camera_counts,
    health,
    index_document,
    load_knowledge_index,
    process_document_index,
    reindex_documents,
    save_knowledge_index,
)

__all__ = [
    "ServiceBindings",
    "analyze_video",
    "answer_message",
    "configure_services",
    "forecast_from_count_history",
    "forecast_short_term",
    "get_camera_counts",
    "health",
    "index_document",
    "load_knowledge_index",
    "process_document_index",
    "reindex_documents",
    "save_knowledge_index",
]
