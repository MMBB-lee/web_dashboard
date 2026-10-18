"""Prevent acknowledgements from retrieving articles and raw excerpts from becoming answers."""
import pytest
from app.services import e_bridge, grounded_llm


@pytest.mark.parametrize('message', ['好的', '好的！', '嗯嗯', '收到', '谢谢你', 'OK'])
def test_acknowledgements_never_search_or_call_model(message, monkeypatch):
    monkeypatch.setattr(e_bridge, 'get_e_service', lambda: pytest.fail('No RAG for acknowledgements'))
    monkeypatch.setattr(grounded_llm, 'complete', lambda *args: pytest.fail('No LLM for acknowledgements'))
    answer = e_bridge.answer_message(message, None, None)
    assert answer['status'] == 'answered'
    assert len(answer['answer']) < 100
    assert answer['citations'] == []


def test_article_answer_is_relevant_and_bounded():
    baseline = {'status': 'answered', 'answer': '根据带日期的来源资料，可参考以下信息：\n'
                '- 标题：历史公告\n来源：测试单位\n网址：https://example.test\n'
                '黄山台风期间注意防雨防滑，遵守景区管理。\n' + '无关的市场宣传内容。' * 200,
                'citations': [], 'limitations': []}
    result = grounded_llm.compact_answer('黄山台风安全提醒是什么？', baseline)
    assert result['status'] == 'answered'
    assert '注意防雨防滑' in result['answer']
    assert '市场宣传' not in result['answer']
    assert len(result['answer']) < 300
    unrelated = grounded_llm.compact_answer('如何查询火车时刻？', baseline)
    assert unrelated['status'] == 'insufficient_evidence'
    assert not unrelated['citations']
