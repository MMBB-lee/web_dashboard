"""OpenAI-compatible completion transport. Secrets/upstream bodies never reach HTTP errors."""
from __future__ import annotations

from urllib.parse import urlsplit
import json
import httpx

from app.core.errors import AppError, CODE_DEPENDENCY_NOT_AVAILABLE


def endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and local)) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LLM_BASE_URL须为HTTPS地址，本机接口可使用HTTP；不要包含凭据或查询参数")
    return base if parsed.path.endswith("/chat/completions") else base + "/chat/completions"


def complete(settings, messages: list[dict[str, str]], *, transport=None) -> str:
    key = settings.llm_api_key.get_secret_value().strip()
    if not key or not settings.llm_model.strip():
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "大模型配置不完整：请填写LLM_API_KEY和LLM_MODEL")
    try:
        url = endpoint(settings.llm_base_url)
    except ValueError as exc:
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, str(exc)) from None
    try:
        with httpx.Client(timeout=max(1, min(settings.llm_timeout_seconds, 180)),
                          follow_redirects=False, transport=transport) as client:
            response = client.post(url, headers={"Authorization": "Bearer " + key}, json={
                "model": settings.llm_model, "messages": messages, "stream": False,
                "max_tokens": max(128, min(settings.llm_max_tokens, 8192)),
            })
        if response.status_code != 200:
            raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE,
                f"大模型接口调用失败（HTTP {response.status_code}），请检查密钥、模型、额度和服务地址")
        value = response.json()["choices"][0]["message"]["content"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empty model response")
        return value.strip()
    except AppError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "大模型接口超时、连接失败或返回内容无效") from None


async def stream_complete(settings, messages: list[dict[str, str]], *, transport=None):
    """Forward actual provider content deltas; never expose reasoning or raw errors."""
    key = settings.llm_api_key.get_secret_value().strip()
    if not key or not settings.llm_model.strip():
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "大模型配置不完整：请填写LLM_API_KEY和LLM_MODEL")
    try:
        url = endpoint(settings.llm_base_url)
    except ValueError as exc:
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, str(exc)) from None
    received = False
    finished = False
    try:
        async with httpx.AsyncClient(timeout=max(1, min(settings.llm_timeout_seconds, 180)),
                                     follow_redirects=False, transport=transport) as client:
            async with client.stream("POST", url, headers={"Authorization": "Bearer " + key}, json={
                "model": settings.llm_model, "messages": messages, "stream": True,
                "max_tokens": max(128, min(settings.llm_max_tokens, 8192)),
            }) as response:
                if response.status_code != 200:
                    raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE,
                        f"大模型接口调用失败（HTTP {response.status_code}），请检查密钥、模型、额度和服务地址")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        finished = True
                        break
                    value = json.loads(data)
                    if value.get("error"):
                        raise ValueError("upstream stream failed")
                    choices = value.get("choices") or []
                    if not choices:
                        continue  # Some providers send a final usage-only event.
                    choice = choices[0]
                    content = (choice.get("delta") or {}).get("content")
                    if content is not None and not isinstance(content, str):
                        raise ValueError("invalid content delta")
                    if content:
                        received = True
                        yield content
                    if choice.get("finish_reason") == "length":
                        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "回答达到模型输出长度限制，未完整生成，请缩小问题范围后重试")
                    if choice.get("finish_reason") == "content_filter":
                        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "模型未完成本次回答，请调整问题后重试")
                    if choice.get("finish_reason") == "stop":
                        finished = True
        if not received or not finished:
            raise ValueError("empty or interrupted stream")
    except AppError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise AppError(503, CODE_DEPENDENCY_NOT_AVAILABLE, "大模型流式连接中断、超时或返回内容无效，请重试") from None
