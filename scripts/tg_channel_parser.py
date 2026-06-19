"""
TG Channel Parser — извлечение вакансий из Telegram-каналов через persistent CDP.

Парсит посты в подписанных TG-каналах, фильтрует по нишевым ключевым словам
(IN/OUT) и возвращает список вакансий с контактами, готовых к отклику.

Использует persistent Chrome profile на порту 9223 (Worker) вместо отдельной
логина-сессии — это решает главную проблему любого TG-парсера: rate-limit
и блокировки при логине через web.telegram.org.

Зависимости:
    pip install websocket-client

Использование:
    python tg_channel_parser.py --channels @channel1 @channel2 --filter auto
    python tg_channel_parser.py --last 24h --keywords AI,маркетолог,воронки
    python tg_channel_parser.py --out vacancies.json

Архитектурное решение: парсер не логинится в TG сам — он берёт уже
авторизованную сессию из persistent Chrome profile. Worker skill
`chrome-cdp-persistent-profile` описывает, как запустить Chrome.

Автор: Дирижёр Нейросетей (@ai_dirizher)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import websocket


# === CDP-обёртка для persistent Chrome ===

def find_chrome_target(cdp_url: str, predicate) -> Optional[dict]:
    """Найти target в persistent Chrome по предикату."""
    import urllib.request
    data = json.load(urllib.request.urlopen(f"{cdp_url}/json"))
    for t in data:
        if t.get("type") == "page" and predicate(t.get("url", "")):
            return t
    return None


class TelegramCDP:
    """Минимальный клиент к Web A через CDP."""

    def __init__(self, ws_url: str, timeout: int = 30):
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self._id = 0

    def send(self, method: str, params: Optional[dict] = None) -> dict:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == self._id:
                return resp

    def eval(self, expression: str) -> any:
        """Безопасный evaluate с обёрткой результата."""
        r = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        result = r.get("result", {}).get("result", {})
        if "value" not in result:
            return None
        return result["value"]

    def navigate(self, url: str, wait_seconds: float = 3.0):
        self.send("Page.navigate", {"url": url})
        time.sleep(wait_seconds)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# === Доменная модель ===

@dataclass
class Vacancy:
    """Нормализованное описание вакансии из поста TG-канала."""
    channel: str
    message_id: str
    text: str
    contact: Optional[str] = None
    contact_kind: Optional[str] = None  # telegram | email | phone | link
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    decision: str = "REVIEW"  # IN | OUT | REVIEW
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# === Нишевой фильтр (IN/OUT) ===

# Ключевые слова, которые однозначно говорят "это наша ниша"
IN_KEYWORDS = [
    "воронок", "воронк", "лид-магнит", "autowebinar",
    "smm", "смм", "контент-план", "копирайт", "копирайтер",
    "email-маркет", "email цепочк", "рассылк",
    "ai", "ии", "нейросет", "claude", "gpt",
    "telegram-бот", "telegram bot", "ai-агент",
    "автоматизац", "автоматизатор",
    "маркетплейс", "карточк товар", " wb ", "ozon",
    "чат-менеджер", "чат-оператор", "без звонков",
    "ассистент", "помощник руководителя",
    "парсинг", "аналитик", "customer support",
    "удалён", "удаленн", "remote",
]

# Ключевые слова, которые сразу = OUT
OUT_KEYWORDS = [
    "reels", "рилс", "рилз", "видео монтаж", "видеомонтаж",
    "motion", "моушн", "анимац", "3d", "after effects",
    "call-центр", "call center", "холодн звонк", "обзвон",
    "sales", "продаж ", "менеджер по продажам", "холодные",
    "ml-разработчик", "machine learning", "data scientist",
    "тестировщик", "qa engineer", "ручное тестирование",
    "без опыта", "от 16 лет", "от 18 лет",
    "оплата за лайк", "за отзыв", "микрозадач",
    "mlm", "сетевой маркетинг", "без вложений",
    "вакансия-бот", "разместить вакансию", "@vacansy_resume",
    "создание картинок", "генерация изображений", "midjourney",
    "выходного", "оплата 350", "оплата 450",  # реклама в ленте
]

# Контактные паттерны
CONTACT_PATTERNS = {
    "telegram": re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,32})"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\+7[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),
    "t.me": re.compile(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,32})"),
}


def extract_contact(text: str) -> tuple[Optional[str], Optional[str]]:
    """Извлекает первый контакт из текста поста. Возвращает (contact, kind)."""
    for kind, pattern in CONTACT_PATTERNS.items():
        m = pattern.search(text)
        if m:
            if kind == "telegram":
                return f"@{m.group(1)}", "telegram"
            elif kind == "t.me":
                return f"@{m.group(1)}", "telegram"
            elif kind == "email":
                return m.group(0), "email"
            elif kind == "phone":
                return m.group(0), "phone"
    return None, None


def classify_vacancy(text: str) -> tuple[float, list[str], list[str]]:
    """Классифицирует вакансию. Возвращает (score, matched_keywords, reasons)."""
    text_lower = text.lower()
    matched = []
    reasons = []

    # OUT-чек
    out_hits = [k for k in OUT_KEYWORDS if k in text_lower]
    if out_hits:
        reasons.append(f"OUT-триггеры: {', '.join(out_hits[:3])}")
        return -1.0, matched, reasons

    # IN-чек
    in_hits = [k for k in IN_KEYWORDS if k in text_lower]
    matched = in_hits

    if not in_hits:
        return 0.0, matched, ["нет нишевых ключевых слов"]

    # Скоринг: больше ключевых = выше score
    score = min(1.0, len(in_hits) * 0.15)
    return score, matched, reasons


# === Парсинг каналов ===

def open_chat(cdp: TelegramCDP, chat_id: str) -> bool:
    """Открыть канал/чат через клик по ListItem (verified pattern)."""
    expr = f"""
    (function(){{
        const a = document.querySelector('a[href="#{chat_id}"]');
        if (!a) return false;
        const r = a.getBoundingClientRect();
        const opts = {{bubbles: true, cancelable: true, view: window,
                      clientX: r.x + 20, clientY: r.y + 20, button: 0}};
        a.dispatchEvent(new MouseEvent('mouseover', opts));
        a.dispatchEvent(new MouseEvent('mousedown', opts));
        a.dispatchEvent(new MouseEvent('mouseup', opts));
        a.dispatchEvent(new MouseEvent('click', opts));
        return true;
    }})()
    """
    return bool(cdp.eval(expr))


def extract_posts(cdp: TelegramCDP, n: int = 30) -> list[dict]:
    """Извлечь последние N уникальных постов в текущем канале."""
    expr = f"""
    (function(){{
        const arr = Array.from(document.querySelectorAll('[data-message-id]'));
        const seen = new Set();
        const out = [];
        for (const m of arr) {{
            const id = m.getAttribute('data-message-id');
            if (seen.has(id)) continue;
            seen.add(id);
            const text = (m.innerText || '').trim();
            if (text.length > 50) out.push({{id, text: text.slice(0, 2500)}});
            if (out.length >= {n}) break;
        }}
        return JSON.stringify(out);
    }})()
    """
    raw = cdp.eval(expr)
    return json.loads(raw) if raw else []


def parse_channel(
    cdp: TelegramCDP,
    channel_id: str,
    channel_name: str,
    last_n: int = 20,
    min_score: float = 0.0,
) -> list[Vacancy]:
    """Парсит один канал и возвращает список вакансий."""
    if not open_chat(cdp, channel_id):
        print(f"  ⚠️ Не удалось открыть канал {channel_name}", file=sys.stderr)
        return []

    time.sleep(3)
    posts = extract_posts(cdp, n=last_n)
    print(f"  📥 {channel_name}: {len(posts)} постов")

    vacancies = []
    for p in posts:
        score, matched, reasons = classify_vacancy(p["text"])
        contact, kind = extract_contact(p["text"])

        if score < min_score:
            continue

        if score > 0:
            decision = "IN"
        elif score < 0:
            decision = "OUT"
        else:
            decision = "REVIEW"

        # Без контакта — отклик невозможен, помечаем
        if decision == "IN" and not contact:
            decision = "REVIEW"
            reasons.append("нет контакта")

        v = Vacancy(
            channel=channel_name,
            message_id=p["id"],
            text=p["text"],
            contact=contact,
            contact_kind=kind,
            score=score,
            matched_keywords=matched[:5],
            decision=decision,
            reasons=reasons,
        )
        vacancies.append(v)

    return vacancies


# === CLI ===

DEFAULT_CHANNELS = [
    # (chat_id, name) — chat_id берётся из URL: t.me/<short_name> → id="-100<id>"
    # Для простоты здесь только chat_id; на практике Worker подставляет
    # актуальные id из списка подписок.
]


def main():
    p = argparse.ArgumentParser(
        description="Парсер вакансий из TG-каналов через persistent CDP"
    )
    p.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9223",
        help="URL Chrome DevTools Protocol (default: http://127.0.0.1:9223)",
    )
    p.add_argument(
        "--channels",
        nargs="+",
        help="Список chat_id каналов (например, -1001485944670)",
    )
    p.add_argument(
        "--last",
        type=int,
        default=20,
        help="Сколько последних постов парсить в каждом канале (default: 20)",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Минимальный score для включения в результат",
    )
    p.add_argument(
        "--out",
        default="-",
        help="Куда сохранить JSON (- = stdout, default)",
    )
    p.add_argument(
        "--filter",
        choices=["all", "in", "out", "review"],
        default="all",
        help="Фильтр по решению классификатора",
    )

    args = p.parse_args()

    if not args.channels:
        print("❌ Нужно указать --channels", file=sys.stderr)
        sys.exit(1)

    # Найти TG-таб в persistent Chrome
    target = find_chrome_target(args.cdp_url, lambda u: "telegram.org" in u)
    if not target:
        print(
            "❌ Не найден открытый Telegram Web таб. "
            "Открой https://web.telegram.org/a/ в persistent Chrome.",
            file=sys.stderr,
        )
        sys.exit(2)

    cdp = TelegramCDP(target["webSocketDebuggerUrl"])
    print(f"🔌 Подключён к TG-табу: {target['id'][:8]}…", file=sys.stderr)

    all_vacancies: list[Vacancy] = []
    for ch_id in args.channels:
        print(f"📡 Парсю {ch_id}…", file=sys.stderr)
        results = parse_channel(cdp, ch_id, ch_id, args.last, args.min_score)
        all_vacancies.extend(results)

    cdp.close()

    # Фильтрация
    if args.filter != "all":
        all_vacancies = [v for v in all_vacancies if v.decision.lower() == args.filter]

    # Сортировка по score
    all_vacancies.sort(key=lambda v: v.score, reverse=True)

    # Вывод
    output = {
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(all_vacancies),
        "by_decision": {
            "IN": sum(1 for v in all_vacancies if v.decision == "IN"),
            "OUT": sum(1 for v in all_vacancies if v.decision == "OUT"),
            "REVIEW": sum(1 for v in all_vacancies if v.decision == "REVIEW"),
        },
        "vacancies": [v.to_dict() for v in all_vacancies],
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"💾 Сохранено в {args.out}: {len(all_vacancies)} вакансий", file=sys.stderr)

    # Краткая сводка
    print("\n=== Сводка ===", file=sys.stderr)
    print(f"IN: {output['by_decision']['IN']} | REVIEW: {output['by_decision']['REVIEW']} | OUT: {output['by_decision']['OUT']}", file=sys.stderr)


if __name__ == "__main__":
    main()
