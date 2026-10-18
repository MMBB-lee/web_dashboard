"""Send one explicit connectivity test; reads server .env, never prints the key."""
from app.core.config import get_settings
from app.core.errors import AppError
from app.services.llm_client import complete


def main():
    settings = get_settings()
    try:
        answer = complete(settings, [{"role":"user","content":"请只回复：API连接成功"}])
    except AppError as exc:
        print(exc.message)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
