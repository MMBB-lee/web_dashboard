import copy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from data_foundation import create_store
from data_foundation.db import Base
from data_foundation.db.public_data import public_data_facts
from data_foundation.ingestion.public_bridge import load_records, apply_import, preview
from app.core.errors import AppError
from app.services.llm_client import complete, endpoint
from app.services import grounded_llm


@pytest.fixture(scope="module")
def real_records():
    project = Path(__file__).resolve().parents[2]
    return load_records(project / "爬取工具/data/local", project / "data_foundation/real_data/2026-10-12-v2/manifest.json")


def make_store(tmp_path, thread_safe=False):
    url = "sqlite+pysqlite:///" + (tmp_path / "bridge.sqlite3").as_posix() if thread_safe else "sqlite+pysqlite:///:memory:"
    store = create_store(url, data_root=tmp_path, allow_sqlite_for_tests=True)
    Base.metadata.create_all(store.engine)
    return store


def test_all_facts_preserved_candidates_separated_and_repeat_skips(tmp_path, real_records):
    store = make_store(tmp_path)
    first = apply_import(store, real_records)
    assert first["archive"]["inserted"] == len(real_records)
    assert first["business"].get("original_unit_requires_review", 0) == 0
    with store.read() as uow:
        assert uow.session.scalar(sa.select(sa.func.count()).select_from(public_data_facts)) == len(real_records)
        for row in real_records:
            archived = uow.session.execute(sa.select(public_data_facts).where(public_data_facts.c.id == row["id"])).mappings().one()
            assert archived["value"] == row["value"]
            assert archived["grain"] == row["grain"]
            if row["quality_status"] != "verified" or row["grain"] in {"year","quarter","cumulative","half_year"}:
                assert archived["observation_id"] is None
        observation_count = uow.session.scalar(sa.text("SELECT COUNT(*) FROM observations"))
    second = apply_import(store, real_records)
    assert second["archive"] == {"inserted":0,"updated":0,"skipped":len(real_records)}
    with store.read() as uow:
        assert uow.session.scalar(sa.text("SELECT COUNT(*) FROM observations")) == observation_count
    store.dispose()


def test_archive_revisions_preserve_previous_value(tmp_path, real_records):
    row = copy.deepcopy(next(r for r in real_records if r["grain"] == "year"))
    store = make_store(tmp_path)
    apply_import(store,[row])
    previous = row["value"]
    row["value"] += 1
    row["raw_record"]["correction"] = "test revision"
    result = apply_import(store,[row])
    assert result["archive"]["updated"] == 1
    with store.read() as uow:
        saved = uow.session.execute(sa.select(public_data_facts)).mappings().one()
        assert saved["value"] == row["value"]
        assert Decimal(saved["previous_versions"][0]["value"]) == previous
    store.dispose()


def test_database_failure_rolls_back_the_whole_import(tmp_path, real_records, monkeypatch):
    from data_foundation.ingestion import public_bridge
    store = make_store(tmp_path)
    def fail(*args):
        raise RuntimeError("transaction failure")
    monkeypatch.setattr(public_bridge,"promote",fail)
    with pytest.raises(RuntimeError,match="transaction failure"):
        apply_import(store, real_records)
    with store.read() as uow:
        assert uow.session.scalar(sa.text("SELECT COUNT(*) FROM public_data_facts")) == 0
        assert uow.session.scalar(sa.text("SELECT COUNT(*) FROM public_data_imports")) == 0
        assert uow.session.scalar(sa.text("SELECT COUNT(*) FROM observations")) == 0
    store.dispose()


def llm_settings(**overrides):
    values = dict(llm_enabled=True,llm_api_key=SecretStr("test-secret"),llm_model="test-model",
        llm_base_url="https://provider.example/v1",llm_timeout_seconds=5,llm_max_tokens=1024)
    return SimpleNamespace(**(values | overrides))


def test_compatible_transport_and_never_prints_secret():
    def handler(request):
        assert str(request.url) == "https://provider.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(200,json={"choices":[{"message":{"content":"回答"}}]})
    assert complete(llm_settings(),[{"role":"user","content":"问题"}],transport=httpx.MockTransport(handler)) == "回答"
    assert endpoint("https://provider.example/v1/chat/completions") == "https://provider.example/v1/chat/completions"


@pytest.mark.parametrize("status",[401,429,500,302])
def test_upstream_failure_is_redacted(status):
    with pytest.raises(AppError) as error:
        complete(llm_settings(),[],transport=httpx.MockTransport(lambda r:httpx.Response(status,text="test-secret internal body")))
    assert "test-secret" not in error.value.message
    assert "internal body" not in error.value.message


def test_no_key_and_bad_endpoint_fail_before_request():
    with pytest.raises(AppError):
        complete(llm_settings(llm_api_key=SecretStr("")),[])
    with pytest.raises(ValueError):
        endpoint("http://remote.example/v1")


def test_grounded_answer_retains_citations_and_unverified_status(monkeypatch):
    record = dict(entity="黄山",metric="scenic_mountain_entries",scope="进山量",grain="year",
        period_start="2025-01-01",period_end="2025-12-31",value="5168000",unit="人",
        quality_status="verified",qualifier=None,source_title="年度报告",source_url="https://example.org/report",
        published_at="2026-04-18",fact_id="fact-1")
    def fake_complete(settings,messages):
        assert "5168000" in messages[1]["content"]
        assert "不能相加" in messages[0]["content"]
        return "2025年进山量516.8万人[E1]。"
    monkeypatch.setattr(grounded_llm,"complete",fake_complete)
    baseline={"answer":"不足","status":"insufficient_evidence","citations":[],"limitations":[],"conversation_id":"test"}
    result=grounded_llm.generate("黄山2025年客流",baseline,settings=llm_settings(),evidence_loader=lambda *a:[record])
    assert result["status"] == "answered"
    assert result["citations"][0]["url"] == record["source_url"]
    assert result["conversation_id"] == "test"


def test_no_evidence_or_failed_explicit_metric_does_not_call_provider(monkeypatch):
    monkeypatch.setattr(grounded_llm,"complete",lambda *a:pytest.fail("must not invent numerical evidence"))
    baseline={"answer":"不足","status":"metric_tool_unavailable","citations":[]}
    assert grounded_llm.generate("数值",baseline,{"metric":"scenic_visits"},settings=llm_settings()) is baseline
    assert grounded_llm.generate("未知",baseline,settings=llm_settings(),evidence_loader=lambda *a:[])["status"] == "insufficient_evidence"


def test_imported_evidence_reads_exact_period_and_rejects_current_history(tmp_path,real_records,monkeypatch):
    store=make_store(tmp_path)
    apply_import(store,real_records)
    monkeypatch.setattr("app.core.providers.get_c_store",lambda:store)
    rows=grounded_llm.imported_evidence("黄山2025年进山量",None)
    assert rows
    assert all(row["period_start"].startswith("2025") for row in rows)
    assert grounded_llm.imported_evidence("现在黄山实时客流",None) == []
    store.dispose()


def test_chat_http_uses_imported_facts_and_compatible_transport(client,tmp_path,real_records,monkeypatch):
    from uuid import uuid4
    from app.services import e_bridge
    store=make_store(tmp_path,thread_safe=True)
    apply_import(store,real_records)
    monkeypatch.setattr("app.core.providers.get_c_store",lambda:store)
    monkeypatch.setattr(grounded_llm,"get_settings",lambda:llm_settings())
    baseline={"conversation_id":str(uuid4()),"answer":"资料不足","status":"insufficient_evidence",
        "citations":[],"limitations":[],"used_metric_queries":[],"metric_result":None}
    monkeypatch.setattr(e_bridge,"get_e_service",lambda:SimpleNamespace(answer_message=lambda *a:baseline))
    def handler(request):
        assert b"5168000" in request.content
        return httpx.Response(200,json={"choices":[{"message":{"content":"2025年黄山进山量516.8万人[E1]。"}}]})
    monkeypatch.setattr(grounded_llm,"complete",lambda settings,messages:complete(settings,messages,transport=httpx.MockTransport(handler)))
    login=client.post("/api/v1/auth/login",json={"username":"admin1","password":"admin-pass-123"})
    csrf=login.json()["data"]["csrf_token"]
    result=client.post("/api/v1/assistant/chat",json={"message":"黄山2025年进山量是多少？"},headers={"X-CSRF-Token":csrf})
    assert result.status_code == 200, result.text
    body=result.json()["data"]
    assert body["status"] == "answered"
    assert body["citations"]
    assert "test-secret" not in result.text
    store.dispose()
