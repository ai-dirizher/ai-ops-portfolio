#!/usr/bin/env python3
"""
chat_unread_extractor.py — extract unread/HR-replied chat threads from
HH (negotiations + chatik), Avito messenger, Telegram Web A, VK messenger.

Used in work-checklist Phase 2 to get a quick state of all HR-side
activity without opening each platform separately.

Usage:
    python3 scripts/chat_unread_extractor.py [platform]
    platforms: hh, avito, telegram, vk, max, all (default)
"""
import json
import sys
import urllib.request
import websocket

CDP_BASE = "http://127.0.0.1:9223"


def get_targets():
    """Return dict {platform: target_id} from /json endpoint."""
    data = json.load(urllib.request.urlopen(f"{CDP_BASE}/json", timeout=5))
    by_url = {}
    for t in data:
        if t.get("type") != "page":
            continue
        url = t.get("url", "")
        if "mail.google.com" in url:
            by_url["gmail"] = t["id"]
        elif "telegram.org" in url:
            by_url["telegram"] = t["id"]
        elif "vk.com" in url:
            by_url["vk"] = t["id"]
        elif "web.max.ru" in url:
            by_url["max"] = t["id"]
        elif "avito.ru" in url:
            by_url["avito"] = t["id"]
        elif "hh.ru" in url:
            by_url["hh"] = t["id"]
    return by_url


def ws_eval(target_id, expression, timeout=15):
    """Send Runtime.evaluate via WebSocket, return .value field."""
    ws = websocket.create_connection(
        f"ws://127.0.0.1:9223/devtools/page/{target_id}", timeout=timeout
    )
    ws.send(
        json.dumps(
            {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
        )
    )
    ws.settimeout(timeout)
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == 1:
            ws.close()
            return r.get("result", {}).get("result", {}).get("value")


def scan_hh(target_id):
    """Get list of Собеседование items + chat TOPIC_IDs."""
    # First navigate to Собеседование tab if not there
    ws_eval(
        target_id,
        """(() => {
        const a = Array.from(document.querySelectorAll('a, button, [role="tab"]'));
        const tab = a.find(x => x.innerText && x.innerText.trim().startsWith('Собеседование'));
        if (tab) tab.click();
        return 'ok';
    })()""",
    )

    import time
    time.sleep(3)

    # Get Собеседование items
    data = ws_eval(
        target_id,
        """(() => {
        const items = Array.from(document.querySelectorAll('[data-qa="open_chat"]'));
        return JSON.stringify(items.slice(0, 10).map(btn => {
            let row = btn;
            for (let i = 0; i < 8; i++) {
                row = row.parentElement;
                if (!row) break;
                const link = row.querySelector('a[href*="/vacancy/"]');
                if (link) {
                    const title = link.innerText.trim().slice(0, 80);
                    const vid = link.getAttribute('href').match(/\\/vacancy\\/(\\d+)/)?.[1];
                    return {title, vid};
                }
            }
            return null;
        }).filter(Boolean));
    })()""",
    )
    return json.loads(data) if data else []


def scan_avito(target_id):
    """Get list of Avito messenger chats with read state and last message preview."""
    data = ws_eval(
        target_id,
        """(() => {
        const channels = Array.from(document.querySelectorAll('[data-marker="channels/channel"]'));
        return JSON.stringify(channels.slice(0, 20).map(c => ({
            name: (c.querySelector('[data-marker="channels/channel-name"]') || {}).innerText || '',
            read: c.getAttribute('data-read'),
            body: c.innerText.replace(/\\n+/g, ' | ').slice(0, 200)
        })));
    })()""",
    )
    return json.loads(data) if data else []


def scan_telegram(target_id):
    """Get list of TG non-channel chats + unread badges."""
    data = ws_eval(
        target_id,
        """(() => {
        const items = Array.from(document.querySelectorAll('.ListItem.Chat'));
        return JSON.stringify(items.map(it => {
            const a = it.querySelector('a[href^="#"]');
            if (!a) return null;
            const m = a.getAttribute('href').match(/^#(-?\\d+)/);
            if (!m) return null;
            const id = m[1];
            if (id.startsWith('-100')) return null;  // skip channels
            const name = (a.innerText || '').split('\\n')[0].trim();
            const badge = it.querySelector('[class*="badge"], [class*="Badge"], [class*="counter"]');
            const badgeText = badge ? badge.innerText.trim() : '';
            const unread = !!badgeText && /\\d/.test(badgeText);
            return {id, name: name.slice(0, 50), unread, badge: badgeText};
        }).filter(Boolean));
    })()""",
    )
    return json.loads(data) if data else []


SCANNERS = {
    "hh": scan_hh,
    "avito": scan_avito,
    "telegram": scan_telegram,
}


def main():
    platform = sys.argv[1] if len(sys.argv) > 1 else "all"
    targets = get_targets()
    if platform == "all":
        keys = list(SCANNERS.keys())
    else:
        keys = [platform]

    for k in keys:
        tid = targets.get(k)
        if not tid:
            print(f"[{k}] NO TARGET — Chrome tab missing")
            continue
        try:
            data = SCANNERS[k](tid)
            print(f"\n=== {k.upper()} ({len(data)} items) ===")
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"[{k}] ERROR: {e}")


if __name__ == "__main__":
    main()