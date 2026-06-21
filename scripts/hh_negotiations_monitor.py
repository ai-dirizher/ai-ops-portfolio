#!/usr/bin/env python3
"""
hh_negotiations_monitor.py — мониторинг HH negotiations (отклики и приглашения).

Подключается к persistent Chrome через CDP, загружает страницу /applicant/negotiations,
извлекает все элементы negotiations со статусами (Собеседование, Ожидание, Отказ,
Приглашение, Не просмотрен) и выводит в JSON или Markdown.

В отличие от hh_auto_responder (который ОТПРАВЛЯЕТ сообщения в один чат),
этот скрипт только ЧИТАЕТ — безопасен, идемпотентен, можно запускать часто.

Использование:
    python3 hh_negotiations_monitor.py                     # все статусы, markdown
    python3 hh_negotiations_monitor.py --status Собеседование
    python3 hh_negotiations_monitor.py --unread-only
    python3 hh_negotiations_monitor.py --json out.json
    python3 hh_negotiations_monitor.py --open 5420476396  # открыть чат по ID

Полезен для: чек-листов по поиску работы, чтобы за минуту увидеть, какие HR
написали за последнее время, без ручного сканирования каждого чата.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
from typing import Any

CDP_HTTP = "http://127.0.0.1:9223"


def get_target_id(url_hint: str = "hh.ru") -> str | None:
    """Find the target_id of the HH tab (or open a new one)."""
    data = json.load(urllib.request.urlopen(f"{CDP_HTTP}/json"))
    for t in data:
        if t.get("type") != "page":
            continue
        u = t.get("url", "")
        if url_hint in u:
            return t["id"]
    return None


def open_new_tab(url: str) -> str:
    """Open a new tab via /json/new and return its target_id."""
    req = urllib.request.Request(
        f"{CDP_HTTP}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        new = json.loads(resp.read())
        return new["id"]


def ws_eval(target_id: str, expression: str, timeout: int = 30) -> Any:
    """Run Runtime.evaluate on a target via WebSocket. Returns parsed value."""
    import websocket  # type: ignore

    ws = websocket.create_connection(
        f"ws://127.0.0.1:9223/devtools/page/{target_id}", timeout=timeout
    )
    cmd_id = int(time.time() * 1000)
    ws.send(json.dumps({"id": cmd_id, "method": "Runtime.evaluate", "params": {"expression": expression}}))
    ws.settimeout(timeout)
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == cmd_id:
            res = r.get("result", {}).get("result", {})
            if res.get("type") == "string":
                return json.loads(res["value"])
            return res.get("value")
    return None


def ws_navigate(target_id: str, url: str) -> None:
    """Navigate target to URL."""
    import websocket  # type: ignore

    ws = websocket.create_connection(
        f"ws://127.0.0.1:9223/devtools/page/{target_id}", timeout=30
    )
    cmd_id = int(time.time() * 1000)
    ws.send(json.dumps({"id": cmd_id, "method": "Page.navigate", "params": {"url": url}}))
    ws.close()
    time.sleep(5)  # let the page load + render negotiations


def extract_negotiations(target_id: str) -> list[dict[str, Any]]:
    """Extract all negotiations items from the current HH page."""
    js = """
    (function(){
        const items = Array.from(document.querySelectorAll('[data-qa="negotiations-item"]'));
        if (items.length === 0) {
            // Fallback: derive from sidebar chat list
            const chats = Array.from(document.querySelectorAll('a[href^="/chat/"]'));
            return chats.map(a => ({
                title: '',
                employer: '',
                status: '',
                date: '',
                is_online: '',
                chat_id: (a.href.match(/\\/chat\\/(\\d+)/) || [])[1] || '',
                chat_url: a.href,
                from_sidebar: true,
                raw: (a.innerText || '').slice(0, 200)
            }));
        }
        return items.map(it => {
            const titleEl = it.querySelector('[data-qa="vacancy-title"]') ||
                            it.querySelector('a[href*="/vacancy/"]');
            const employerEl = it.querySelector('[data-qa="company"]') ||
                               it.querySelector('[data-qa="vacancy-company-name"]');
            const statusEl = it.querySelector('[data-qa="status"]') ||
                             it.querySelector('[class*="status"]');
            const dateEl = it.querySelector('[data-qa="negotiations-item__date"]') ||
                           it.querySelector('time');
            const chatBtn = it.querySelector('button[data-qa="open_chat"]');
            const href = (titleEl && titleEl.href) || '';
            return {
                title: (titleEl?.innerText || '').trim(),
                employer: (employerEl?.innerText || '').trim(),
                status: (statusEl?.innerText || '').trim(),
                date: (dateEl?.innerText || (dateEl?.getAttribute && dateEl.getAttribute('datetime'))) || '',
                vacancy_id: (href.match(/vacancy\\/(\\d+)/) || [])[1] || '',
                has_chat: !!chatBtn,
                chat_btn_present: !!chatBtn,
                raw: (it.innerText || '').slice(0, 300),
            };
        });
    })()
    """
    return ws_eval(target_id, js) or []


def extract_sidebar_chats(target_id: str) -> list[dict[str, Any]]:
    """Extract chat IDs from the sidebar (always available, fast)."""
    js = """
    (function(){
        const links = Array.from(document.querySelectorAll('a[href^="/chat/"]'));
        return links.map(a => {
            const id = (a.href.match(/\\/chat\\/(\\d+)/) || [])[1] || '';
            const text = (a.innerText || '').slice(0, 300);
            const lines = text.split('\\n');
            return {
                chat_id: id,
                chat_url: a.href,
                preview: lines.slice(0, 5).join(' | ')
            };
        }).filter(c => c.chat_id);
    })()
    """
    return ws_eval(target_id, js) or []


def filter_status(items: list[dict], status: str | None) -> list[dict]:
    if not status:
        return items
    s = status.strip().lower()
    return [it for it in items if s in (it.get("status") or "").lower()]


def to_markdown(items: list[dict]) -> str:
    if not items:
        return "_(не найдено)_"
    lines = ["| Статус | Дата | Вакансия | Компания | ID чата |", "|---|---|---|---|---|"]
    for it in items:
        st = (it.get("status") or "").replace("|", "/")
        dt = (it.get("date") or "").replace("|", "/")
        t = (it.get("title") or "").replace("|", "/")[:60]
        e = (it.get("employer") or "").replace("|", "/")[:30]
        cid = it.get("chat_id") or it.get("chat_url", "").split("/")[-1] or ""
        lines.append(f"| {st} | {dt} | {t} | {e} | {cid} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="HH negotiations monitor via CDP")
    parser.add_argument("--status", help="Filter by status (Собеседование, Ожидание, Отказ, Приглашение)")
    parser.add_argument("--unread-only", action="store_true", help="Only show items with chat button (has employer response)")
    parser.add_argument("--json", help="Write JSON to file")
    parser.add_argument("--md", action="store_true", help="Output as Markdown table")
    parser.add_argument("--open", help="Open chat by ID in top-level tab (prints URL)")
    args = parser.parse_args()

    print(f"[hh-monitor] Подключаюсь к {CDP_HTTP}...")
    target_id = get_target_id()
    if not target_id:
        print("[hh-monitor] HH tab не найден, открываю новый...")
        target_id = open_new_tab("https://nn.hh.ru/applicant/negotiations")
        time.sleep(5)
    print(f"[hh-monitor] target_id={target_id}")

    print("[hh-monitor] Загружаю negotiations...")
    ws_navigate(target_id, "https://nn.hh.ru/applicant/negotiations")

    print("[hh-monitor] Извлекаю items...")
    items = extract_negotiations(target_id)
    sidebar = extract_sidebar_chats(target_id)
    print(f"[hh-monitor] Найдено: {len(items)} items, {len(sidebar)} chats в sidebar")

    # Inject chat IDs from sidebar if missing (negotiations DOM sometimes doesn't expose them)
    sidebar_ids = {c["chat_id"]: c for c in sidebar}
    for it in items:
        if not it.get("chat_id"):
            # Try to match by employer+title from sidebar
            for sc in sidebar:
                if (it.get("employer") and it["employer"] in sc["preview"]) or \
                   (it.get("title") and it["title"][:30] in sc["preview"]):
                    it["chat_id"] = sc["chat_id"]
                    it["chat_url"] = sc["chat_url"]
                    break

    if args.status:
        items = filter_status(items, args.status)
    if args.unread_only:
        items = [it for it in items if it.get("has_chat")]

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"[hh-monitor] Сохранено в {args.json}")

    if args.open:
        url = f"https://nn.hh.ru/chat/{args.open}"
        print(f"[hh-monitor] Открываю чат: {url}")
        ws_navigate(target_id, url)
        return 0

    if args.md:
        print(to_markdown(items))
    else:
        for it in items:
            cid = it.get("chat_id") or "?"
            print(f"  • [{it.get('status','?'):14}] {it.get('date','?'):10} | {it.get('title','')[:50]} | {it.get('employer','')[:30]} | chat/{cid}")

    return 0


if __name__ == "__main__":
    sys.exit(main())