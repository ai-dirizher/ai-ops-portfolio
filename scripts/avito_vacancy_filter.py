#!/usr/bin/env python3
"""
avito_vacancy_filter.py — фильтр вакансий Avito через CDP.

Принимает URL категории Avito (например, /moskva/vakansii?q=менеджер+маркетплейсов),
подключается к persistent Chrome на порту 9223 (Worker profile),
извлекает карточки вакансий, фильтрует по ЗП и нише, выводит в JSON или Markdown.

Ключевые отличия от hh_vacancy_filter.py:
- Avito UI использует другой data-marker селектор (`[data-marker=item]`).
- Цены парсятся из `[data-marker=item-price-value]` в формате «50 000 — 80 000 ₽»
  или «от 30 000 ₽» / «до 45 000 ₽».
- ЗП-фильтр: оставляем вакансии, где нижняя граница >= MIN_SALARY
  (по умолчанию 30 000 ₽) или верхняя граница в диапазоне 30–100к.
- Ниша-фильтр: ключевые слова для нашей ниши (менеджер маркетплейсов,
  копирайтер, SMM, аналитик, чат-менеджер, оператор, помощник).
- OUT-фильтр: «call-центр», «продавец», «оператор ПВЗ», «курьер»,
  «менеджер по продажам», «риелтор», «SMM с Reels», «видеоконтент».

Использование:
    python3 avito_vacancy_filter.py --url "https://www.avito.ru/moskva/vakansii?q=менеджер+маркетплейсов"
    python3 avito_vacancy_filter.py --url "<URL>" --min-salary 30000 --max-salary 100000 --md
    python3 avito_vacancy_filter.py --url "<URL>" --json out.json

Автор: ai-dirizher (Worker profile)
Лицензия: MIT
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from typing import Any

CDP_HTTP = "http://127.0.0.1:9223"

NICHE_KEYWORDS = [
    "менеджер маркетплейс",
    "помощник менеджер",
    "оператор маркетплейс",
    "аналитик маркетплейс",
    "копирайтер",
    "чат-менеджер",
    "чат менеджер",
    "smm",
    "контент-менеджер",
    "контент менеджер",
    "ассистент",
    "помощник руководител",
    "оператор подд",
    "администратор маркетплейс",
    "карточк товар",
    "wildberries",
    "wildberri",
    "вайлдберриз",
    "ozon",
    "маркетплейс",
    "ai-оператор",
    "удалённ",
    "удаленн",
    "удалён",
    "freelance",
    "фриланс",
]

OUT_KEYWORDS = [
    "call-центр",
    "call центр",
    "callцентр",
    "call-centre",
    "продавец",
    "продавщиц",
    "продажа",
    "оператор пвз",
    "пункт выдач",
    "курьер",
    "риелтор",
    "репетитор",
    "хостес",
    "официант",
    "бармен",
    "повар",
    "мастер маникюр",
    "парикмахер",
    "клинер",
    "уборк",
    "разнорабоч",
    "грузчик",
    "такси",
    "водитель",
    "воен",
    "контракт",
    "reels",
    "видеоконтент",
    "фото и видео",
    "видеосъёмк",
    "видеомонтаж",
    "только видео",
    "маникюр",
    "педикюр",
]

def parse_price(text: str) -> tuple[int | None, int | None]:
    """Parse '50 000 ₽', '50 000 — 80 000 ₽', 'от 30 000 ₽' → (min, max)."""
    if not text:
        return None, None
    cleaned = text.replace("\xa0", " ").strip()

    # "X — Y ₽"
    m = re.search(r"(\d[\d\s]*)\s*[—–-]\s*(\d[\d\s]*)\s*₽", cleaned)
    if m:
        lo = int(m.group(1).replace(" ", ""))
        hi = int(m.group(2).replace(" ", ""))
        return lo, hi
    # "от X ₽"
    m = re.search(r"от\s+(\d[\d\s]*)\s*₽", cleaned, re.IGNORECASE)
    if m:
        lo = int(m.group(1).replace(" ", ""))
        return lo, None
    # "до X ₽"
    m = re.search(r"до\s+(\d[\d\s]*)\s*₽", cleaned, re.IGNORECASE)
    if m:
        hi = int(m.group(1).replace(" ", ""))
        return None, hi
    # "X ₽ за смену" / "X ₽/час" — low priority, пропускаем как None
    m = re.search(r"(\d[\d\s]*)\s*₽\s*(?:за\s+смену|в\s+час|сдельно|за\s+час)", cleaned, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(" ", "")), int(m.group(1).replace(" ", ""))
    # просто "X ₽"
    m = re.search(r"(\d[\d\s]*)\s*₽", cleaned)
    if m:
        v = int(m.group(1).replace(" ", ""))
        return v, v
    return None, None


def get_target_id(url: str) -> str | None:
    """Find the target_id of the Avito tab (or open a new one)."""
    data = json.load(urllib.request.urlopen(f"{CDP_HTTP}/json"))
    for t in data:
        if t.get("type") != "page":
            continue
        u = t.get("url", "")
        if "avito.ru" in u:
            return t["id"]
    # Open a new tab if none found
    req = urllib.request.Request(
        f"{CDP_HTTP}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        new = json.loads(resp.read())
        return new["id"]


def eval_cdp(target_id: str, expression: str) -> Any:
    """Run Runtime.evaluate on a target via WebSocket."""
    import websocket  # type: ignore

    ws = websocket.create_connection(
        f"ws://127.0.0.1:9223/devtools/page/{target_id}", timeout=30
    )
    cmd_id = int(__import__("time").time() * 1000)
    ws.send(json.dumps({"id": cmd_id, "method": "Runtime.evaluate", "params": {"expression": expression}}))
    ws.settimeout(30)
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == cmd_id:
            res = r.get("result", {}).get("result", {})
            if res.get("type") == "string":
                return json.loads(res["value"])
            return res.get("value")
    return None


def navigate(target_id: str, url: str) -> None:
    """Navigate target to URL and wait for load."""
    import websocket
    import time

    ws = websocket.create_connection(
        f"ws://127.0.0.1:9223/devtools/page/{target_id}", timeout=30
    )
    cmd_id = int(time.time() * 1000)
    ws.send(json.dumps({"id": cmd_id, "method": "Page.navigate", "params": {"url": url}}))
    ws.close()
    time.sleep(4)  # initial wait


def extract_vacancies(target_id: str) -> list[dict[str, Any]]:
    """Extract all vacancy cards from the current Avito page."""
    js = """
    (function(){
        const items = Array.from(document.querySelectorAll('[data-marker=item]'));
        return items.map(it => {
            const titleEl = it.querySelector('[data-marker=item-title]');
            const priceEl = it.querySelector('[data-marker=item-price-value]');
            const locEl = it.querySelector('[data-marker=item-location]');
            const paramsEl = it.querySelector('[data-marker=item-specific-params]');
            return {
                title: titleEl ? (titleEl.innerText||'').trim() : '',
                price: priceEl ? (priceEl.innerText||'').trim() : '',
                location: locEl ? (locEl.innerText||'').trim() : '',
                params: paramsEl ? (paramsEl.innerText||'').trim() : '',
                href: titleEl ? (titleEl.href || '') : '',
            };
        }).filter(v => v.title);
    })()
    """
    return eval_cdp(target_id, js) or []


def is_niche(v: dict[str, Any]) -> bool:
    """Check if vacancy is in our niche (keyword match)."""
    blob = f"{v.get('title','')} {v.get('params','')}".lower()
    return any(kw in blob for kw in NICHE_KEYWORDS)


def is_out(v: dict[str, Any]) -> bool:
    """Check if vacancy is explicitly OUT (calls, sales, manual labor, etc.)."""
    blob = f"{v.get('title','')} {v.get('params','')}".lower()
    return any(kw in blob for kw in OUT_KEYWORDS)


def in_salary_range(lo: int | None, hi: int | None, min_s: int, max_s: int) -> bool:
    """Salary filter: keep if (lo >= min_s) OR (hi in [min_s, max_s*2]) OR (lo is None AND hi is None)."""
    if lo is None and hi is None:
        return True  # unknown salary — neutral
    if lo is not None and lo >= min_s:
        return True
    if hi is not None and min_s <= hi <= max_s * 2:
        return True
    return False


def filter_vacancies(
    vacancies: list[dict[str, Any]],
    min_salary: int = 30_000,
    max_salary: int = 100_000,
) -> list[dict[str, Any]]:
    """Apply niche + OUT + salary filters."""
    out: list[dict[str, Any]] = []
    for v in vacancies:
        if is_out(v):
            continue
        if not is_niche(v):
            continue
        lo, hi = parse_price(v.get("price", ""))
        if not in_salary_range(lo, hi, min_salary, max_salary):
            continue
        v["salary_min"] = lo
        v["salary_max"] = hi
        out.append(v)
    return out


def to_markdown(vs: list[dict[str, Any]]) -> str:
    if not vs:
        return "_(нет вакансий, прошедших фильтр)_"
    lines = ["| ЗП мин | ЗП макс | Локация | Заголовок | Ссылка |", "|---|---|---|---|---|"]
    for v in vs:
        smin = f"{v.get('salary_min'):,}".replace(",", " ") if v.get('salary_min') else "—"
        smax = f"{v.get('salary_max'):,}".replace(",", " ") if v.get('salary_max') else "—"
        loc = v.get("location", "").replace("|", "/")[:25]
        title = v.get("title", "").replace("|", "/")[:60]
        href = v.get("href", "")
        lines.append(f"| {smin} | {smax} | {loc} | {title} | {href} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Avito vacancy filter via CDP")
    parser.add_argument("--url", required=True, help="Avito category URL with filter")
    parser.add_argument("--min-salary", type=int, default=30_000, help="Minimum salary floor")
    parser.add_argument("--max-salary", type=int, default=100_000, help="Maximum salary ceiling")
    parser.add_argument("--md", action="store_true", help="Output as Markdown table")
    parser.add_argument("--json", help="Write filtered JSON to file")
    args = parser.parse_args()

    print(f"[avito-filter] Подключаюсь к {CDP_HTTP}...")
    target_id = get_target_id(args.url)
    if not target_id:
        print("ERROR: не удалось найти/открыть вкладку Avito", file=sys.stderr)
        return 1

    print(f"[avito-filter] target_id={target_id}")
    print(f"[avito-filter] Перехожу на {args.url}")
    navigate(target_id, args.url)

    print("[avito-filter] Извлекаю вакансии...")
    all_v = extract_vacancies(target_id)
    print(f"[avito-filter] Найдено: {len(all_v)} объявлений")

    filtered = filter_vacancies(all_v, args.min_salary, args.max_salary)
    print(f"[avito-filter] После фильтра: {len(filtered)}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(filtered, f, ensure_ascii=False, indent=2)
        print(f"[avito-filter] Сохранено в {args.json}")

    if args.md:
        print(to_markdown(filtered))
    else:
        for v in filtered:
            smin = f"{v.get('salary_min'):,}".replace(",", " ") if v.get('salary_min') else "—"
            smax = f"{v.get('salary_max'):,}".replace(",", " ") if v.get('salary_max') else "—"
            print(f"  • {v.get('title','')[:60]} | {smin}–{smax} | {v.get('location','')[:30]}")
            print(f"    {v.get('href','')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())