"""Calendar intent and genuine provider deltas, completion, cancellation and redacted errors."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.errors import AppError
from app.services import e_bridge, llm_client, system_answers
from app.services.rate_limit import SlidingWindowRateLimiter


@pytest.fixture(autouse=True)
def isolated_limiter(monkeypatch):
    monkeypatch.setattr('app.api.v1.assistant.chat_limiter', SlidingWindowRateLimiter(20, 60))


def settings():
    return SimpleNamespace(llm_enabled=True, llm_api_key=SecretStr('test-secret'),
        llm_model='test-model', llm_base_url='https://provider.example/v1',
        llm_timeout_seconds=5, llm_max_tokens=1024)


def freeze_clock(monkeypatch):
    # It is already the next day in Beijing, still the previous date in UTC.
    instant = datetime(2026, 10, 17, 16, 1, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(system_answers, 'china_now', lambda: instant.astimezone(system_answers.CHINA_TIME))
    monkeypatch.setattr(e_bridge, 'get_e_service', lambda: pytest.fail('Clock must not retrieve knowledge'))


@pytest.mark.parametrize('question', ['今天日期', '今天几号？', '请问今天星期几', '请告诉我今天是几月几号', '现在的日期', '今日日期是什么'])
def test_beijing_date_never_requires_evidence(question, monkeypatch):
    freeze_clock(monkeypatch)
    answer = e_bridge.answer_message(question, None, None)
    assert answer['status'] == 'answered'
    assert '2026年10月18日，星期日' in answer['answer']
    assert not answer['citations']


def test_time_and_relative_dates_but_not_current_visitor_counts(monkeypatch):
    freeze_clock(monkeypatch)
    assert '00:01:02' in e_bridge.answer_message('现在几点', None, None)['answer']
    assert '2026年10月19日，星期一' in e_bridge.answer_message('明天几号', None, None)['answer']
    assert system_answers.answer_system_question('今天黄山有多少游客？', None) is None
    assert system_answers.answer_system_question('今天日期和黄山客流', None) is None


def login(client):
    response = client.post('/api/v1/auth/login', json={'username': 'admin1', 'password': 'admin-pass-123'})
    return {'X-CSRF-Token': response.json()['data']['csrf_token']}


def events(response):
    return [(frame.split('\n')[0][7:], json.loads(frame.split('\ndata: ', 1)[1]))
            for frame in response.text.strip().split('\n\n')]


def test_stream_date_and_authentication(client, monkeypatch):
    freeze_clock(monkeypatch)
    assert client.post('/api/v1/assistant/chat/stream', json={'message': '今天日期'}).status_code == 401
    headers = login(client)
    assert client.post('/api/v1/assistant/chat/stream', json={'message': '今天日期'}).status_code == 403
    result = client.post('/api/v1/assistant/chat/stream', json={'message': '今天日期'}, headers=headers)
    assert result.status_code == 200
    assert result.headers['content-type'].startswith('text/event-stream')
    assert result.headers['x-accel-buffering'] == 'no'
    frames = events(result)
    assert [name for name, _ in frames] == ['start', 'delta', 'done']
    assert '2026年10月18日' in frames[-1][1]['answer']
    assert frames[-1][1]['status'] == 'answered'


def baseline():
    return {'conversation_id': str(uuid4()), 'answer': '已核验的历史安全提示。', 'status': 'answered',
        'citations': [{'type': 'document', 'id': str(uuid4()), 'title': '历史公告', 'url': None, 'date': '2024-07-25'}],
        'used_metric_queries': [], 'limitations': [], 'metric_result': None}


def test_sse_forwards_each_delta_and_keeps_final_sources(client, monkeypatch):
    original = baseline()
    monkeypatch.setattr(e_bridge, 'prepare_answer', lambda *args: (original, False))
    monkeypatch.setattr('app.api.v1.assistant.get_settings', settings)
    async def provider(*args):
        yield '根据历史公告，'
        yield '请注意防滑。'
    monkeypatch.setattr(llm_client, 'stream_complete', provider)
    response = client.post('/api/v1/assistant/chat/stream', json={'message': '安全提醒'}, headers=login(client))
    frames = events(response)
    assert [name for name, _ in frames] == ['start', 'delta', 'delta', 'done']
    final = frames[-1][1]
    assert final['answer'] == '根据历史公告，请注意防滑。'
    assert final['citations'][0]['id'] == original['citations'][0]['id']
    assert final['conversation_id'] == original['conversation_id']


def test_failure_after_partial_text_never_emits_success(client, monkeypatch):
    monkeypatch.setattr(e_bridge, 'prepare_answer', lambda *args: (baseline(), False))
    monkeypatch.setattr('app.api.v1.assistant.get_settings', settings)
    async def provider(*args):
        yield '尚未完成的内容'
        raise AppError(503, 'dependency_not_available', '连接中断，请重试')
    monkeypatch.setattr(llm_client, 'stream_complete', provider)
    frames = events(client.post('/api/v1/assistant/chat/stream', json={'message': '安全提醒'}, headers=login(client)))
    assert [name for name, _ in frames] == ['start', 'delta', 'error']


def packet(content=None, finish=None, **extra):
    delta = {'content': content, **extra}
    return ('data: ' + json.dumps({'choices': [{'delta': delta, 'finish_reason': finish}]}, ensure_ascii=False) + '\n\n').encode()


def test_provider_is_streaming_before_upstream_finishes_and_closes_on_cancel():
    async def run():
        class SlowStream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield packet('第一段')
                await asyncio.sleep(3600)  # Never reached by the first-delta assertion.
            async def aclose(self):
                self.closed = True
        upstream = SlowStream()
        async def handler(request):
            assert json.loads(request.content)['stream'] is True
            return httpx.Response(200, stream=upstream)
        stream = llm_client.stream_complete(settings(), [], transport=httpx.MockTransport(handler))
        assert await asyncio.wait_for(anext(stream), 1) == '第一段'
        assert not upstream.closed
        await stream.aclose()
        assert upstream.closed
    asyncio.run(run())


def test_provider_handles_fragmented_utf8_role_reasoning_and_usage():
    async def run():
        data = packet(None, role='assistant', reasoning_content='不要展示的推理') + packet('合肥') + packet('机场', 'stop') + b'data: {"choices":[],"usage":{}}\n\ndata: [DONE]\n\n'
        class SplitStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for index in range(0, len(data), 7):
                    yield data[index:index+7]
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=SplitStream()))
        assert [part async for part in llm_client.stream_complete(settings(), [], transport=transport)] == ['合肥', '机场']
    asyncio.run(run())


@pytest.mark.parametrize('data', [packet('未完成'), b'data: test-secret-not-json\n\n', packet('', 'stop'), packet('一半', 'length')])
def test_provider_truncation_or_invalid_stream_is_redacted(data):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=data))
        with pytest.raises(AppError) as error:
            _ = [part async for part in llm_client.stream_complete(settings(), [], transport=transport)]
        assert 'test-secret' not in error.value.message
    asyncio.run(run())
