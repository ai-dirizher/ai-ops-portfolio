#!/usr/bin/env python3
"""
hh_vacancy_filter.py — фильтр вакансий HH по нише через CDP.

Использование:
  1. Запустить постоянный Chrome: --remote-debugging-port=9223
  2. Открыть hh.ru с фильтром "Удалённая работа"
  3. Запустить скрипт — он извлечёт список вакансий и применит нишевый фильтр

Что фильтрует:
  OUT: телефонные звонки, Reels/видео, генерация картинок, sales/MLM,
       "за лайки", "возраст от 16", резюме-блокеры
  IN: AI/ML, автоматизация, маркетплейсы, аналитика, копирайтинг, ассистент

Требует: websocket-client
"""
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

try:
    import websocket
except ImportError:
    print("Установите websocket-client: pip install websocket-client")
    raise


@dataclass
class Vacancy:
    title: str
    company: str
    link: str
    salary: str
    snippet: str = ""


# Регулярки для фильтрации
OUT_PATTERNS = {
    "calls": re.compile(r"\b(звонк|обзвон|оператор\s+колл|оператор\s+входящ|оператор\s+кц|холодн)", re.IGNORECASE),
    "reels": re.compile(r"(reels?|рилс|видео\s*продакшн|монтаж|видеосъём)", re.IGNORECASE),
    "image_gen": re.compile(r"(генерац\w*\s*картин|генерац\w*\s*изображ|созда\w*\s+картин|рисов\w*\s+иллюстр)", re.IGNORECASE),
    "sales": re.compile(r"\b(менеджер\s+по\s+продаж|холодн\w*\s+продаж|менеджер\s+продаж|оптов\w*\s+продаж|активн\w*\s+продаж)", re.IGNORECASE),
    "mlm": re.compile(r"\b(млм|mlm|сетевой\s+маркетинг|сетев\w+\s+бизнес)", re.IGNORECASE),
    "microtasks": re.compile(r"(за\s+лайк|за\s+отзыв|за\s+подписк|микрозадач|легк\w+\s+заработ)", re.IGNORECASE),
    "young": re.compile(r"(от\s+14\s+лет|от\s+16\s+лет|без\s+опыта\s+подходит\s+всем|школьник)", re.IGNORECASE),
}

IN_PATTERNS = {
    "ai": re.compile(r"\b(ai|llm|gpt|нейросет|нейрон|искусственн\w+\s+интеллект|machine\s+learning|ml\s+инженер)", re.IGNORECASE),
    "automation": re.compile(r"\b(автоматизац|n8n|airflow|workflow|pipeline|интеграц)", re.IGNORECASE),
    "analytics": re.compile(r"\b(аналит|data\s*scientist|sql|дашборд|метрик|парсинг)", re.IGNORECASE),
    "marketplace": re.compile(r"\b(wb|wildberries|ozon|wildberr|маркетплейс|селлер|карточк\w+\s+товар)", re.IGNORECASE),
    "copywriting": re.compile(r"\b(копирайт|редакт|контент|textwriter|writer|рерайт)", re.IGNORECASE),
    "assistant": re.compile(r"\b(ассистент|личн\w+\s+помощник|помощник\s+руководител)", re.IGNORECASE),
    "smm": re.compile(r"\b(smm|смм|маркетинг\s+в\s+соцсет|контент-маркетинг)", re.IGNORECASE),
    "telegram": re.compile(r"\b(telegram\s*бот|tg\s*бот)", re.IGNORECASE),
    "support": re.compile(r"\b(поддержк|helpdesk|саппорт)", re.IGNORECASE),
}


def classify(vac: Vacancy) -> tuple[str, list[str]]:
    """Возвращает ('in' | 'out' | 'review', [причины])."""
    text = f"{vac.title} {vac.company} {vac.snippet}"

    out_hits = []
    for name, pat in OUT_PATTERNS.items():
        if pat.search(text):
            out_hits.append(name)

    if out_hits:
        return "out", out_hits

    in_hits = [name for name, pat in IN_PATTERNS.items() if pat.search(text)]
    if in_hits:
        return "in", in_hits

    return "review", ["не подошёл ни под IN, ни под OUT — нужно прочитать вручную"]


def extract_vacancies(target_id: str) -> List[Vacancy]:
    """Извлекает вакансии с текущей страницы HH через CDP."""
    ws = websocket.create_connection(
        f"ws://127.0.0.1:9223/devtools/page/{target_id}", timeout=30
    )
    cmd_id = [0]
    def send(method, params=None, timeout=30):
        cmd_id[0] += 1
        ws.send(json.dumps({"id": cmd_id[0], "method": method, "params": params or {}}))
        ws.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = json.loads(ws.recv())
                if r.get("id") == cmd_id[0]:
                    return r
            except websocket.WebSocketTimeoutException:
                return {"error": f"timeout for {method}"}
        return {"error": f"timeout outer for {method}"}

    extract = """
    (() => {
      const items = document.querySelectorAll('[data-qa=\"vacancy-serp__vacancy\"]');
      const result = [];
      for (const el of items) {
        const title = (el.querySelector('[data-qa=\"serp-item__title\"]')?.innerText || '').trim();
        const link = (el.querySelector('a[data-qa=\"serp-item__title\"]')?.href || '').trim();
        const salary = (el.querySelector('[data-qa=\"vacancy-serp__vacancy-compensation\"]')?.innerText || '').trim();
        const company = (el.querySelector('[data-qa=\"vacancy-serp__vacancy-employer\"]')?.innerText || '').trim();
        const snippet = (el.querySelector('[data-qa=\"vacancy-serp__vacancy_snippet\"]')?.innerText || '').trim();
        result.push({title, link, salary, company, snippet});
      }
      return JSON.stringify({count: items.length, items: result});
    })()
    """
    r = send("Runtime.evaluate", {"expression": extract})
    val = json.loads(r['result']['result']['value'])
    ws.close()
    return [
        Vacancy(
            title=it['title'],
            company=it['company'],
            link=it['link'],
            salary=it['salary'],
            snippet=it['snippet'],
        )
        for it in val.get('items', [])
    ]


def find_hh_tab() -> Optional[str]:
    """Находит target_id открытой вкладки с hh.ru."""
    data = json.load(urllib.request.urlopen("http://127.0.0.1:9223/json", timeout=5))
    for t in data:
        if t.get('type') == 'page' and 'hh.ru' in (t.get('url') or ''):
            return t['id']
    return None


def main():
    target_id = find_hh_tab()
    if not target_id:
        print("Не найдена открытая вкладка hh.ru в persistent Chrome (port 9223)")
        return

    print(f"Tab: {target_id[:12]}")
    vacancies = extract_vacancies(target_id)
    print(f"Извлечено: {len(vacancies)} вакансий\n")

    buckets = {"in": [], "out": [], "review": []}
    for v in vacancies:
        verdict, reasons = classify(v)
        buckets[verdict].append((v, reasons))

    for verdict in ("in", "out", "review"):
        bucket = buckets[verdict]
        if not bucket:
            continue
        print(f"=== {verdict.upper()} ({len(bucket)}) ===")
        for v, reasons in bucket:
            tag = ", ".join(reasons) if reasons else "?"
            print(f"  [{v.salary or '—'}] {v.title[:60]}")
            print(f"    {v.company[:40]}  ::  {tag}")
            print(f"    {v.link[:80]}")
            print()
        print()


if __name__ == "__main__":
    main()
