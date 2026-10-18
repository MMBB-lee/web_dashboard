"""General/no-evidence turns reach the model; DB synchronization is incremental and lease protected."""
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
import sqlalchemy as sa
from app.services import assistant_agent as agent,grounded_llm,database_knowledge as db
from app.services.c_store import CStore
from data_foundation import create_store
from data_foundation.db import Base
from data_foundation.db.models import KnowledgeDocument,Source

@pytest.fixture
def settings(): return SimpleNamespace(llm_enabled=True)

def setup_agent(monkeypatch,route='general',**plan):
    monkeypatch.setattr(agent,'entity_catalog',lambda:[])
    monkeypatch.setattr(agent,'complete',lambda *_:json.dumps({'route':route,'question':'独立问题',**plan},ensure_ascii=False))

def test_general_turn_does_not_search_and_uses_generated_response(monkeypatch,settings):
    setup_agent(monkeypatch)
    monkeypatch.setattr('app.core.providers.get_e_service',lambda:pytest.fail('No retrieval for general conversation'))
    monkeypatch.setattr(agent,'database_evidence',lambda *_:pytest.fail('No DB tool for general conversation'))
    baseline=agent.prepare('解释一下什么是Python',None,None,'alice',settings)
    def answer(_,messages):
        assert 'Python' in messages[-1]['content']
        return 'Python是一种编程语言。'
    monkeypatch.setattr(grounded_llm,'complete',answer)
    result=grounded_llm.generate('解释一下什么是Python',baseline,settings=settings)
    assert result['status']=='answered' and result['answer']=='Python是一种编程语言。'
    assert not result['citations']

def test_missing_project_evidence_is_normal_model_turn(monkeypatch,settings):
    setup_agent(monkeypatch,'project')
    monkeypatch.setattr('app.core.providers.get_e_service',lambda:SimpleNamespace(answer_message=lambda *_:{'status':'insufficient_evidence','answer':'模板拒答'}))
    monkeypatch.setattr(agent,'database_evidence',lambda *_:[])
    baseline=agent.prepare('项目还没记录的景点门票是多少？',None,None,'alice',settings)
    prepared,messages=grounded_llm.prepare_generation('问题',baseline,settings=settings)
    assert messages and prepared['status']=='answered'
    payload=json.loads(messages[-1]['content'])
    assert payload['evidence']==[] and '模板拒答' not in messages[-1]['content']
    assert '没有项目资料时坦诚说明' in messages[0]['content']

def test_model_plan_and_context_are_owner_scoped(monkeypatch,settings):
    conv=str(uuid4());agent.remember('alice','先介绍九华山',{'conversation_id':conv,'answer':'九华山位于安徽。'})
    assert agent.history('alice',conv) and not agent.history('bob',conv)
    setup_agent(monkeypatch,'general')
    baseline=agent.prepare('那里有哪些交通方式？',conv,None,'alice',settings)
    assert baseline['_agent']['history'][0]['question']=='先介绍九华山'
    assert agent.prepare('那里有哪些交通方式？',conv,None,'bob',settings)['_agent']['history']==[]

def test_malformed_planner_json_still_reaches_model(monkeypatch,settings):
    monkeypatch.setattr(agent,'complete',lambda *_:'not json')
    plan=agent.model_plan('用户原问题',[],[],None,settings)
    assert plan.question=='用户原问题'

def test_only_used_server_sources_are_attached():
    baseline={'_agent':{},'answer':'这项项目资料目前还没收录。','citations':[{'id':'one','title':'[E1] 无关公告'}]}
    assert agent.finalize(baseline|{'_agent':{'route':'project'}})['citations']==[]
    result=agent.finalize(baseline|{'_agent':{'route':'project'},'answer':'数据库有此记录[E1]。不能伪造引用[E99]。'})
    assert len(result['citations'])==1 and '[E99]' not in result['answer']

@pytest.fixture
def store(tmp_path):
    native=create_store('sqlite+pysqlite:///'+(tmp_path/'rag.sqlite').as_posix(),data_root=tmp_path/'assets',allow_sqlite_for_tests=True)
    Base.metadata.create_all(native.engine)
    yield CStore(native)
    native.dispose()

def test_sync_build_incremental_change_switches_only_after_ready(store,monkeypatch):
    content={'one':{'title':'数据库测试资料','text':'万佛湖；2026年9月；景区接待人次123人次；模拟数据。','records':1}}
    monkeypatch.setattr(db,'export_business_documents',lambda _:dict(content))
    first=db.sync_database(store)
    assert first['created']==1 and first['queued']==1
    again=db.sync_database(store)
    assert again['unchanged']==1 and again['queued']==0
    from app.worker import DWorker
    monkeypatch.setattr(DWorker,'_publish_active_snapshot',lambda _:None)
    db.index_pending_database_documents(store)
    assert db.database_status(store)=={'documents':1,'ready':1,'pending':0,'failed':0,'records':1}
    old=store.ready_document_ids()
    assert len(old)==1
    from intelligent_analysis.knowledge.artifacts import load_verified_knowledge_snapshot
    assert load_verified_knowledge_snapshot(store,old,store.data_root)['documents']
    content['one']['text']='万佛湖；2026年9月；景区接待人次456人次；模拟数据。'
    changed=db.sync_database(store)
    assert changed['updated']==1
    assert store.ready_document_ids()==old
    db.index_pending_database_documents(store)
    new=store.ready_document_ids()
    assert len(new)==1 and new!=old
    snapshot=load_verified_knowledge_snapshot(store,new,store.data_root)
    assert '456' in str(snapshot) and '123人次' not in str(snapshot)
    assert db.sync_database(store)['queued']==0

def test_database_update_requires_admin_and_csrf(client,monkeypatch,store):
    monkeypatch.setattr('app.core.providers.get_c_store',lambda:store)
    assert client.post('/api/v1/knowledge/database/sync',json={}).status_code==401
    response=client.post('/api/v1/auth/login',json={'username':'viewer1','password':'viewer-pass-123'})
    assert client.post('/api/v1/knowledge/database/sync',json={},headers={'X-CSRF-Token':response.json()['data']['csrf_token']}).status_code==403
    client.post('/api/v1/auth/login',json={'username':'admin1','password':'admin-pass-123'})
    assert client.post('/api/v1/knowledge/database/sync',json={}).status_code==403

def test_export_does_not_read_authentication_fields(store):
    exported=db.export_business_documents(store)
    text=str(exported)
    assert 'password_hash' not in text and 'csrf_token' not in text and 'session_token' not in text
