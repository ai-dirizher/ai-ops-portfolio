"""
VK Group Wall Parser — извлечение вакансий из стены группы ВКонтакте через persistent CDP.

Парсит посты в группе VK, фильтрует по нишевым ключевым словам (IN/OUT) и
возвращает список вакансий с контактами, готовых к отклику.

Использует persistent Chrome profile на порту 9223 (Worker) — берёт уже
авторизованную сессию VK, что решает главную проблему любого парсера:
rate-limit и блокировки при логине.

Зависимости:
    pip install websocket-client

Использование:
    python vk_group_parser.py --group distantsiya
    python vk_group_parser.py --group -72829428 --posts 20 --filter in
    python vk_group_parser.py --group distantsiya --out vacancies.json

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


class VKCDP:
    """Минимальный клиент к Web VK через CDP."""

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

    def navigate(self, url: str, wait_seconds: float = 4.0):
        self.send("Page.navigate", {"url": url})
        time.sleep(wait_seconds)

    def scroll_down(self, step: int = 1500, steps: int = 8, delay: float = 0.5):
        """Скроллим страницу вниз, чтобы подгрузить ленивый контент."""
        for _ in range(steps):
            self.eval(f"window.scrollBy(0, {step}); 'ok'")
            time.sleep(delay)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# === Доменная модель ===

@dataclass
class Vacancy:
    """Нормализованное описание вакансии из поста VK-стены."""
    group: str
    post_id: str
    text: str
    contact: Optional[str] = None
    contact_kind: Optional[str] = None  # telegram | max | phone | email | link
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    decision: str = "REVIEW"  # IN | OUT | REVIEW
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# === Нишевой фильтр (IN/OUT) — общий с TG-парсером ===

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
    "ассистент", "помощник руководителя", "бизнес-ассистент",
    "парсинг", "аналитик", "customer support",
    "удалён", "удаленн", "remote", "удалёнка",
]

# Ключевые слова, которые сразу = OUT
OUT_KEYWORDS = [
    "reels", "рилс", "рилз", "видео монтаж", "видеомонтаж",
    "motion", "моушн", "анимац", "3d", "after effects",
    "call-центр", "call center", "холодн звонк", "обзвон",
    "sales", "продаж ", "менеджер по продажам", "холодные",
    "b2b-продаж", "холодные продажи", "активные продажи",
    "ml-разработчик", "machine learning", "data scientist",
    "тестировщик", "qa engineer", "ручное тестирование",
    "без опыта", "от 16 лет", "от 18 лет",
    "оплата за лайк", "за отзыв", "микрозадач",
    "mlm", "сетевой маркетинг", "без вложений",
    "создание картинок", "генерация изображений", "midjourney",
]

# Контактные паттерны
CONTACT_PATTERNS = {
    "telegram": re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,32})"),
    "t.me": re.compile(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,32})"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"\+?\d[\s\-\(\)]?\d{3}[\s\-\(\)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),
    "max": re.compile(r"(?:max|макс)[\s\+]*(\+?\d[\d\s\-\(\)]{8,15}\d)"),
}


def extract_contact(text: str) -> tuple[Optional[str], Optional[str]]:
    """Извлекает первый контакт из текста поста. Возвращает (contact, kind).

    Приоритет: telegram > email > phone. Сначала ищем TG-username (типичный
    способ связи в современных вакансиях), потом email, потом phone.
    """
    for kind in ("telegram", "t.me", "email", "phone", "max"):
        m = CONTACT_PATTERNS[kind].search(text)
        if m:
            if kind in ("telegram", "t.me"):
                return f"@{m.group(1)}", "telegram"
            elif kind == "email":
                return m.group(0), "email"
            elif kind == "phone":
                return m.group(0), "phone"
            elif kind == "max":
                return m.group(0), "max"
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


# === Парсинг стены VK ===

def resolve_group_id(cdp: VKCDP, group_short_name: str) -> str:
    """Резолвит короткое имя группы в числовой id.

    Примеры:
        'distantsiya' -> '72829428'
        '-72829428'   -> '72829428'  (уже id)
        'club123'     -> '123'
    """
    if group_short_name.lstrip("-").isdigit():
        return group_short_name.lstrip("-")
    # Иначе открываем короткую ссылку и извлекаем id
    cdp.navigate(f"https://vk.com/{group_short_name}", wait_seconds=4)
    oid = cdp.eval("window.location.pathname.match(/wall(-?\\d+)/) || document.querySelector('a[href*=\"wall\"]')?.href.match(/wall(-?\\d+)/)")
    if oid and len(oid) > 0:
        return oid.lstrip("-")
    raise ValueError(f"Could not resolve group id from {group_short_name}")


def extract_posts(cdp: VKCDP, n: int = 20) -> list[dict]:
    """Извлечь последние N уникальных постов в текущей группе.

    Скроллим страницу, чтобы подгрузить больше постов через ленивую загрузку.
    """
    cdp.scroll_down(step=1500, steps=8, delay=0.5)
    expr = f"""
    (function(){{
        const posts = document.querySelectorAll('[class*="post" i], [class*="wall_item" i], [data-post-id]');
        const seen = new Set();
        const out = [];
        for (const p of posts) {{
            const text = (p.innerText || '').trim();
            if (text.length < 100) continue;
            const sig = text.slice(0, 80);
            if (seen.has(sig)) continue;
            seen.add(sig);
            // post_id в формате -GROUP_ID_POST_ID
            const id = p.getAttribute('data-post-id') || (p.id || '').replace('post', '');
            out.push({{id, text: text.slice(0, 3000)}});
            if (out.length >= {n}) break;
        }}
        return JSON.stringify(out);
    }})()
    """
    raw = cdp.eval(expr)
    return json.loads(raw) if raw else []


def parse_group(
    cdp: VKCDP,
    group_id: str,
    group_name: str,
    last_n: int = 20,
    min_score: float = 0.0,
) -> list[Vacancy]:
    """Парсит одну группу и возвращает список вакансий."""
    cdp.navigate(f"https://vk.com/wall-{group_id}", wait_seconds=4)
    posts = extract_posts(cdp, n=last_n)
    print(f"  📥 {group_name}: {len(posts)} постов", file=sys.stderr)

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
            group=group_name,
            post_id=p["id"],
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

def main():
    p = argparse.ArgumentParser(
        description="Парсер вакансий из стены группы ВКонтакте через persistent CDP"
    )
    p.add_argument(
        "--cdp-url",
        default="http://127.0.0.1:9223",
        help="URL Chrome DevTools Protocol (default: http://127.0.0.1:9223)",
    )
    p.add_argument(
        "--group",
        required=True,
        help="Короткое имя группы (например, distantsiya) или числовой id (например, 72829428)",
    )
    p.add_argument(
        "--last",
        type=int,
        default=20,
        help="Сколько последних постов парсить (default: 20)",
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

    # Найти VK-таб в persistent Chrome
    target = find_chrome_target(args.cdp_url, lambda u: "vk.com" in u)
    if not target:
        print(
            "❌ Не найден открытый VK таб. Открой https://vk.com в persistent Chrome.",
            file=sys.stderr,
        )
        sys.exit(2)

    cdp = VKCDP(target["webSocketDebuggerUrl"])
    print(f"🔌 Подключён к VK-табу: {target['id'][:8]}…", file=sys.stderr)

    # Резолвим id группы
    print(f"🔍 Резолвлю группу {args.group}…", file=sys.stderr)
    group_id = resolve_group_id(cdp, args.group)
    print(f"✅ group_id = {group_id}", file=sys.stderr)

    # Парсим
    vacancies = parse_group(cdp, group_id, args.group, args.last, args.min_score)
    cdp.close()

    # Фильтрация
    if args.filter != "all":
        vacancies = [v for v in vacancies if v.decision.lower() == args.filter]

    # Сортировка по score
    vacancies.sort(key=lambda v: v.score, reverse=True)

    # Вывод
    output = {
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "group": args.group,
        "group_id": group_id,
        "total": len(vacancies),
        "by_decision": {
            "IN": sum(1 for v in vacancies if v.decision == "IN"),
            "OUT": sum(1 for v in vacancies if v.decision == "OUT"),
            "REVIEW": sum(1 for v in vacancies if v.decision == "REVIEW"),
        },
        "vacancies": [v.to_dict() for v in vacancies],
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"💾 Сохранено в {args.out}: {len(vacancies)} вакансий", file=sys.stderr)

    # Краткая сводка
    print("\n=== Сводка ===", file=sys.stderr)
    print(f"IN: {output['by_decision']['IN']} | REVIEW: {output['by_decision']['REVIEW']} | OUT: {output['by_decision']['OUT']}", file=sys.stderr)


if __name__ == "__main__":
    main()
