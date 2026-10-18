"""Answer calendar/clock questions from the server clock, without RAG or LLM."""
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

CHINA_TIME = timezone(timedelta(hours=8))


def china_now():
    return datetime.now(CHINA_TIME)


def answer_system_question(message: str, conversation_id: str | None) -> dict | None:
    text = re.sub(r"[\s，,。.!！?？、：:]", "", message)
    text = re.sub(r"^(?:请问一下|请问|麻烦|请)?(?:能不能告诉我|能告诉我|你知道|告诉我|帮我查一下|帮我查询|查询一下)?", "", text)
    text = re.sub(r"[呢呀啊吗]$", "", text)
    calendar = re.fullmatch(
        r"(今天|今日|现在|当前|昨天|明天|前天|后天)(?:是)?(?:的)?"
        r"(?:日期(?:是什么|是多少)?|什么日期|什么日子|哪一天|几月几[日号]|几[日号]|多少号|星期几|周几|礼拜几)", text)
    clock = re.fullmatch(r"(?:现在|当前|北京)(?:的)?(?:时间(?:是什么)?|是?几点(?:了)?|是?几点几分)", text)
    if not calendar and not clock:
        return None
    now = china_now()
    if calendar:
        label = calendar[1]
        day = now.date() + timedelta(days={"昨天": -1, "明天": 1, "前天": -2, "后天": 2}.get(label, 0))
        weekday = "一二三四五六日"[day.weekday()]
        label = "今天" if label in {"今日", "现在", "当前"} else label
        answer = f"{label}是{day.year}年{day.month}月{day.day}日，星期{weekday}（北京时间，UTC+8）。"
    else:
        answer = f"当前北京时间是{now.year}年{now.month}月{now.day}日 {now:%H:%M:%S}（UTC+8）。"
    return {"conversation_id": conversation_id or str(uuid4()), "answer": answer, "status": "answered",
            "citations": [], "used_metric_queries": [], "limitations": [], "metric_result": None}
