import pytest

from intelligent_analysis import ServiceBindings, configure_services
from intelligent_analysis.knowledge.index import InMemoryKnowledgeIndex


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    configure_services(ServiceBindings())
    monkeypatch.setattr("intelligent_analysis.service._global_index", InMemoryKnowledgeIndex())
    yield
    configure_services(ServiceBindings())


@pytest.fixture
def config():
    return {"count_model": "p2pnet", "weights_version": "test-p2pnet",
            "roi": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 0},
                               {"x": 1, "y": 1}, {"x": 0, "y": 1}]},
            "sample_interval_seconds": 10, "aggregation_minutes": 5,
            "inference_engine": "pytorch", "device": "CPU"}
