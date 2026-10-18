"""智能体问答接口（03-接口总览 §7：19）。

回答由 E 的 intelligent_analysis 生成，数值经 D 的受约束查询，
引用必须可追溯；不显示未经验证的官方预警。
"""

import json
from contextlib import aclosing
from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.core.errors import CODE_RATE_LIMITED, CODE_INTERNAL_ERROR, AppError
from app.core.config import get_settings
from app.core.permissions import require_csrf
from app.core.providers import require_intelligent_analysis
from app.schemas.assistant import ChatRequest, ChatResponse
from app.schemas.common import Envelope, envelope
from app.services import e_bridge
from app.services.rate_limit import chat_limiter

router = APIRouter(prefix="/assistant", tags=["智能体问答"])


@router.post("/chat", response_model=Envelope[ChatResponse])
async def chat(
    body: ChatRequest,
    request: Request,
    session=Depends(require_csrf),
    _dep=Depends(require_intelligent_analysis),
) -> dict:
    """Model-directed general/project conversation with server-selected RAG and read-only data tools.

    Limit: 20 requests/minute/user, 1–1000 characters. When LLM is disabled,
    retain the legacy E-only path. JSON and SSE share preparation and provenance.
    """
    if not chat_limiter.allow(session.user_id):
        raise AppError(429, CODE_RATE_LIMITED, "请求过于频繁，请稍后再试")
    result = await run_in_threadpool(e_bridge.answer_message,
        body.message,
        body.conversation_id,
        body.context.model_dump() if body.context else None,
        session.user_id,
    )
    return envelope(request, _chat_payload(result))


def _chat_payload(result: dict) -> dict:
    payload = ChatResponse.model_validate(result).model_dump(mode="json")
    if payload.get("status") == "metric_tool_unavailable":
        # E 的 metric 分支返回固定话术且 limitations 为空，D 在这里把「为什么
        # 没数」补上，避免前端/用户误以为接口坏了或以为是检索到的结论。
        limitations = list(payload.get("limitations") or [])
        limitations.append(
            "所选对象、时间或粒度没有足够的已核验数据；请查看原始统计资料和覆盖时间，历史值不能替代实时人数。"
        )
        payload["limitations"] = limitations
    return payload


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def stream_chat(
    body: ChatRequest, request: Request, session=Depends(require_csrf),
    _dep=Depends(require_intelligent_analysis),
):
    """Authenticated SSE: start, actual provider deltas, then verified final metadata."""
    if not chat_limiter.allow(session.user_id):
        raise AppError(429, CODE_RATE_LIMITED, "请求过于频繁，请稍后再试")
    context = body.context.model_dump() if body.context else None
    baseline, direct = await run_in_threadpool(e_bridge.prepare_answer, body.message, body.conversation_id, context, session.user_id)
    settings = get_settings()
    messages = None
    if not direct:
        from app.services.grounded_llm import prepare_generation
        baseline, messages = await run_in_threadpool(prepare_generation, body.message, baseline, context, settings=settings)
    payload = _chat_payload(baseline)

    async def events():
        yield _event("start", {"conversation_id": payload["conversation_id"]})
        try:
            if messages is None:
                yield _event("delta", {"text": payload["answer"]})
            else:
                from app.services.llm_client import stream_complete
                parts = []
                async with aclosing(stream_complete(settings, messages)) as provider_stream:
                    async for delta in provider_stream:
                        if await request.is_disconnected():
                            return
                        parts.append(delta)
                        yield _event("delta", {"text": delta})
                payload["answer"] = "".join(parts)
            from app.services.assistant_agent import remember, finalize
            payload.update(finalize(payload,baseline))
            remember(session.user_id,body.message,payload)
            yield _event("done", payload)
        except AppError as exc:
            yield _event("error", {"code": exc.code, "message": exc.message})
        except Exception:
            yield _event("error", {"code": CODE_INTERNAL_ERROR, "message": "回答生成中断，请重试"})

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
