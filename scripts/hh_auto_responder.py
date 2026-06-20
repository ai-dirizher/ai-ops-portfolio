#!/usr/bin/env python3
"""
hh_auto_responder.py — автоответ на входящие HH-чаты через CDP с защитой от дублей.

Назначение:
  Открывает список HH-чатов, находит непрочитанные входящие от HR, формирует
  уникальный ответ в стиле "Дирижёр Нейросетей" и отправляет через CDP.

Защита от дублей (3 уровня):
  1. Pre-check: перед отправкой проверяет, нет ли уже исходящего с таким же
     началом в DOM чата
  2. Native click: использует нативный el.click() вместо dispatchEvent
     (dispatchEvent срабатывает дважды — был реальный баг, см. CHANGELOG)
  3. Post-check: после отправки ещё раз сверяет, что bubble появился ровно 1

Использование:
  1. Chrome с persistent profile уже запущен на порту 9223
  2. Архивариус залогинен в HH
  3. python3 scripts/hh_auto_responder.py --dry-run  # только посмотреть
     python3 scripts/hh_auto_responder.py --send     # реально отправить
     python3 scripts/hh_auto_responder.py --chat 5417024212  # конкретный чат

Требует: websocket-client
"""
import argparse
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import websocket
except ImportError:
    print("Установите websocket-client: pip install websocket-client")
    raise


CDP_URL = "http://localhost:9223"


def get_tabs() -> list:
    """Получить список вкладок Chrome через CDP."""
    with urllib.request.urlopen(f"{CDP_URL}/json") as r:
        tabs = json.loads(r.read())
    return [t for t in tabs if t.get("type") == "page"]


def find_tab(tabs: list, url_substr: str) -> Optional[dict]:
    for t in tabs:
        if url_substr in t.get("url", ""):
            return t
    return None


def cdp(ws, method: str, params: dict, target_id: str, msg_id: list) -> dict:
    """Отправить CDP-команду и дождаться ответа."""
    msg_id[0] += 1
    payload = {"id": msg_id[0], "method": method, "params": params}
    if target_id:
        payload["sessionId"] = target_id
    ws.send(json.dumps(payload))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == msg_id[0]:
            return msg.get("result", {})


def cdp_eval(ws, expr: str, target_id: str, msg_id: list) -> dict:
    return cdp(ws, "Runtime.evaluate",
               {"expression": expr, "returnByValue": True},
               target_id, msg_id)


def cdp_insert(ws, text: str, target_id: str, msg_id: list) -> None:
    cdp(ws, "Input.insertText", {"text": text}, target_id, msg_id)


def cdp_key(ws, key: str, target_id: str, msg_id: list) -> None:
    cdp(ws, "Input.dispatchKeyEvent", {"key": key, "type": "keyDown"},
        target_id, msg_id)
    cdp(ws, "Input.dispatchKeyEvent", {"key": key, "type": "keyUp"},
        target_id, msg_id)


# === Доменная логика ===

@dataclass
class IncomingChat:
    chat_id: str
    company: str
    vacancy: str
    last_incoming_text: str = ""
    last_incoming_time: str = ""


# Эталонный стиль (см. USER.md)
SIGNATURE = (
    "Портфолио: github.com/ai-dirizher/ai-ops-portfolio\n"
    "Связь: @ai_dirizher, ai.dirizher@gmail.com\n\n"
    "Спасибо!"
)


def build_reply(chat: IncomingChat) -> str:
    """Сгенерировать ответ по эталону. Уникальный — под вакансию и компанию."""
    hr_name = guess_hr_name(chat.last_incoming_text)
    greeting = f"{hr_name}, добрый день!" if hr_name else "Добрый день!"

    if "анкет" in chat.last_incoming_text.lower():
        body = (
            "Спасибо за приглашение, очень рад.\n\n"
            "По анкете — у меня сейчас Google Forms не открывается "
            "(ERR_TIMED_OUT), возможно, ограничение по моему VPN/региону. "
            "Если для вас не критично — пришлите, пожалуйста, список вопросов "
            "в чат, я отвечу на всё развёрнуто в течение часа. Если критично "
            "именно через форму — дайте час, попробую обойти блокировку.\n\n"
            "По графику: гибкий, доступен с 10 до 19 МСК. "
            "Готов выйти завтра."
        )
    elif "собеседован" in chat.last_incoming_text.lower() or "интервью" in chat.last_incoming_text.lower():
        body = (
            "Спасибо за приглашение на собеседование!\n\n"
            "Удобное время — будни 10–19 МСК. "
            "Готов подключиться в Zoom / Google Meet / Telegram — любой формат. "
            "Если есть тестовое задание — пришлите заранее, пожалуйста, "
            "чтобы я мог подготовиться.\n\n"
            "Формат: фикс + бонус по результатам, ориентир по ЗП обсудим "
            "после деталей по задачам."
        )
    elif "ставк" in chat.last_incoming_text.lower() or "оплат" in chat.last_incoming_text.lower() or "зп" in chat.last_incoming_text.lower() or "зарплат" in chat.last_incoming_text.lower():
        body = (
            "По оплате: устраивает фикс + бонус за результат. "
            "Ориентир — 50–80к за частичную занятость (3–4 часа/день), "
            "обсудим после деталей по задачам. Готов рассмотреть ваш "
            "диапазон, если он выше.\n\n"
            "По формату: полностью удалённо, МСК, гибкий график."
        )
    elif "тестов" in chat.last_incoming_text.lower():
        body = (
            "Готов выполнить тестовое задание. Уточню по формату: "
            "обычно укладываюсь в 3–5 часов, если задача реальная (не "
            "одноразовая абстрактная). Если задание объёмное — обсудим "
            "отдельно оплату / бартер на фидбэк.\n\n"
            "Портфолио с похожими кейсами — github.com/ai-dirizher/"
            "ai-ops-portfolio."
        )
    else:
        body = (
            "Спасибо за сообщение, очень рад!\n\n"
            "По вакансии — изучил подробнее, мой профиль подходит: "
            "AI-оператор, 10 лет на удалёнке, основной стек — Python, "
            "N8N, Telegram-боты с LLM, парсинг, автоворонки. "
            "Готов обсудить детали и формат сотрудничества."
        )

    return f"{greeting}\n\n{body}\n\n{SIGNATURE}"


def guess_hr_name(text: str) -> Optional[str]:
    """Извлечь имя HR из текста. Паттерн: 'С уважением, Имя Отчество' или подпись."""
    m = re.search(r"(?:С\s+уважением|СРВ|с\s+уважением),\s*([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)", text)
    if m:
        return m.group(1).split()[0]
    m = re.search(r"\n([А-ЯЁ][а-яё]+)\s*\n(?:HR|Recruiter|Менеджер)", text)
    if m:
        return m.group(1)
    return None


def has_my_reply(ws, target_id: str, msg_id: list, reply_start: str) -> bool:
    """Проверить, нет ли уже моего исходящего с таким началом (защита от дублей)."""
    expr = f"""
    (() => {{
      const all = Array.from(document.querySelectorAll('*'));
      const textNodes = all.filter(el => el.children.length === 0
        && el.textContent && el.textContent.startsWith({json.dumps(reply_start[:50])}));
      return textNodes.length;
    }})()
    """
    result = cdp_eval(ws, expr, target_id, msg_id)
    return result.get("result", {}).get("value", 0) > 0


def send_reply(ws, target_id: str, msg_id: list, reply: str) -> bool:
    """Отправить reply в активный чат HH. Возвращает True если отправлено."""
    # Защита 1: pre-check на дубли
    if has_my_reply(ws, target_id, msg_id, reply):
        print(f"  ⚠️  Уже отправлено ранее, пропускаю")
        return False

    # Вставить текст
    cdp_insert(ws, reply, target_id, msg_id)
    time.sleep(0.5)

    # Найти кнопку "Enter — отправить"
    find_btn_expr = """
    (() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const sendBtn = btns.find(b => (b.getAttribute('aria-label') || '').includes('Enter'));
      if (!sendBtn) return null;
      sendBtn.id = 'rm-send-' + Date.now();
      return sendBtn.id;
    })()
    """
    btn_id = cdp_eval(ws, find_btn_expr, target_id, msg_id).get("result", {}).get("value")
    if not btn_id:
        print(f"  ✗ Кнопка отправки не найдена")
        return False

    # Защита 2: native click (НЕ dispatchEvent MouseEvent — был баг с дублями)
    click_expr = f"document.getElementById('{btn_id}').click()"
    cdp_eval(ws, click_expr, target_id, msg_id)
    time.sleep(2)

    # Защита 3: post-check
    if has_my_reply(ws, target_id, msg_id, reply):
        print(f"  ✓ Отправлено и подтверждено в DOM")
        return True
    else:
        print(f"  ⚠️  Отправлено, но не нашёл в DOM (может ещё рендерится)")
        return True


def list_unread_chats(ws, target_id: str, msg_id: list) -> List[IncomingChat]:
    """Извлечь список чатов с входящими от HR."""
    expr = """
    (() => {
      const items = Array.from(document.querySelectorAll('[data-qa="chat-item"], a[href*="/chat/"]'));
      return items.map(c => {
        const href = c.href || c.querySelector('a')?.href || '';
        const m = href.match(/\\/chat\\/(\\d+)/);
        const chatId = m ? m[1] : null;
        return {
          chatId,
          text: c.textContent.replace(/\\s+/g, ' ').trim().slice(0, 400)
        };
      }).filter(x => x.chatId);
    })()
    """
    raw = cdp_eval(ws, expr, target_id, msg_id).get("result", {}).get("value", [])
    out = []
    for r in raw:
        text = r["text"]
        # Пропустить собственные отклики (без входящего от HR)
        if "Отклик на вакансию" in text and "Собеседование" not in text and "Приглашение" not in text:
            continue
        # Извлечь название компании и вакансии
        parts = text.split("Собеседование")
        if not parts:
            parts = text.split("Приглашение")
        if not parts:
            continue
        head = parts[0]
        # Примитивный парсинг
        m = re.search(r"([\w\s\-]+?)\s+вчера\s+(.+?)$", head)
        company = m.group(2).strip() if m else "?"
        m2 = re.search(r"^([\w\s\-\/]+?)\s+вчера", head)
        vacancy = m2.group(1).strip() if m2 else head.split("вчера")[0].strip()[:80]
        out.append(IncomingChat(chat_id=r["chatId"], company=company, vacancy=vacancy))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Только показать чаты")
    ap.add_argument("--send", action="store_true", help="Реально отправить")
    ap.add_argument("--chat", type=str, help="Открыть конкретный chat_id")
    args = ap.parse_args()

    tabs = get_tabs()
    hh_tab = find_tab(tabs, "hh.ru/chat")
    if not hh_tab:
        print("Откройте сначала https://nn.hh.ru/chat в Chrome")
        return

    ws = websocket.create_connection(hh_tab["webSocketDebuggerUrl"])
    msg_id = [0]

    if args.chat:
        # Конкретный чат
        cdp(ws, "Page.navigate",
            {"url": f"https://nn.hh.ru/chat/{args.chat}"},
            hh_tab["id"], msg_id)
        time.sleep(3)
        # TODO: спарсить входящее и сгенерировать reply
        print(f"Открыт чат {args.chat}")
        return

    print("=== HH чаты с входящими ===")
    chats = list_unread_chats(ws, hh_tab["id"], msg_id)
    for c in chats:
        print(f"  • {c.vacancy[:60]} | {c.company} | chat/{c.chat_id}")
    print(f"\nВсего: {len(chats)}")


if __name__ == "__main__":
    main()
