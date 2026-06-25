#!/usr/bin/env python3
"""
cdp_eval.py — robust CDP Runtime.evaluate wrapper with auto-cmd-id loop.

Background: multiple Worker scripts (hh_negotiations_monitor.py,
avito_vacancy_filter.py) hang on `ws.recv()` because they don't track the
command id correctly. The bare `ws.recv()` reads the FIRST message, which
is often an unsolicited event (Target.targetCreated, Network.responseReceived,
Page.frameNavigated, etc.), not the response to their command. After
~25s the recv times out and the script crashes.

The fix: send command with a unique `id`, then read messages in a loop
until the response with that id appears. This pattern is in the
Hermes chrome-cdp-persistent-profile skill, but it's been re-implemented
inline in every script — usually incorrectly.

Usage:
    from scripts.cdp_eval import cdp_eval
    val = cdp_eval(target_id, "JSON.stringify(document.title)")
    val = cdp_eval(target_id, "..." , method="Page.navigate", params={"url": "..."})
    val = cdp_eval(target_id, "..." , timeout=60)

The function returns the `result.value` for Runtime.evaluate, the full
`result` dict for other methods, or raises on timeout / WS error.

Add `from hermes_tools import path_helpers as _ph; sys.path.insert(0, _ph.scripts_dir())`
at the top of consumer scripts that don't already have it.
"""
import json
import sys
import time
import urllib.request
import websocket


# ---- connection --------------------------------------------------------

def _ws_url_for(target_id: str) -> str:
    return f"ws://127.0.0.1:9223/devtools/page/{target_id}"


def _browser_ws_url() -> str:
    data = json.load(urllib.request.urlopen("http://127.0.0.1:9223/json"))
    return data[0]["webSocketDebuggerUrl"]


# ---- main API ----------------------------------------------------------

def cdp_eval(target_id: str, expression=None,
             method: str = "Runtime.evaluate", params=None,
             timeout: int = 30, return_by_value: bool = True):
    """
    Run a CDP command on `target_id` and return its result.

    For Runtime.evaluate, returns the `result.value` (already deserialized).
    For other methods, returns the full `result` dict.

    Robust to unsolicited events: filters by `id` and keeps reading
    until the matching response arrives (or timeout).
    """
    if params is None:
        params = {}
    if method == "Runtime.evaluate" and return_by_value and "returnByValue" not in params:
        params = dict(params)
        params["returnByValue"] = True
    if method == "Runtime.evaluate" and expression is not None and "expression" not in params:
        params = dict(params)
        params["expression"] = expression

    cmd_id = _next_id()
    payload = {"id": cmd_id, "method": method, "params": params}
    ws = websocket.create_connection(_ws_url_for(target_id), timeout=timeout)
    try:
        ws.send(json.dumps(payload))
        deadline = time.time() + timeout
        while time.time() < deadline:
            ws.settimeout(max(0.5, deadline - time.time()))
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # filter: only the response with our id
            if msg.get("id") == cmd_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP error: {msg['error']}")
                result = msg.get("result", {})
                if method == "Runtime.evaluate":
                    rv = result.get("result", {}).get("value")
                    # surface exceptionDetails
                    if result.get("exceptionDetails"):
                        raise RuntimeError(
                            f"JS exception: {result['exceptionDetails']}"
                        )
                    return rv
                return result or {}
        raise TimeoutError(
            f"cdp_eval: no response with id={cmd_id} in {timeout}s "
            f"(method={method})"
        )
    finally:
        try:
            ws.close()
        except Exception:
            pass


# ---- helpers -----------------------------------------------------------

_id_counter = 0


def _next_id() -> int:
    global _id_counter
    _id_counter += 1
    return _id_counter


def navigate(target_id: str, url: str, wait_load: float = 0.0) -> None:
    """Convenience: Page.navigate + optional sleep."""
    cdp_eval(target_id, method="Page.navigate", params={"url": url},
             timeout=20)
    if wait_load:
        time.sleep(wait_load)


def screenshot(target_id: str, path: str, fmt: str = "png") -> int:
    """Save a screenshot of the page. Returns file size in bytes."""
    import base64
    res = cdp_eval(target_id, method="Page.captureScreenshot",
                   params={"format": fmt}, timeout=30)
    data = base64.b64decode(res["data"])
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


def eval_all(target_ids, expression, method: str = "Runtime.evaluate",
             params=None, timeout: int = 30) -> dict:
    """
    Run the same expression on multiple targets. Returns {target_id: value}.
    Stops at first failure and includes the error in the value.
    """
    out = {}
    for tid in target_ids:
        try:
            out[tid] = cdp_eval(tid, expression, method=method,
                                params=params, timeout=timeout)
        except Exception as e:
            out[tid] = f"ERROR: {e}"
    return out


# ---- CLI demo ----------------------------------------------------------

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("expression", help="JS expression to evaluate")
    p.add_argument("--target", "-t", required=True,
                   help="CDP target id (from /json)")
    p.add_argument("--method", "-m", default="Runtime.evaluate")
    p.add_argument("--timeout", default=30, type=int)
    args = p.parse_args()
    val = cdp_eval(args.target, args.expression, method=args.method,
                   timeout=args.timeout)
    if isinstance(val, (dict, list)):
        print(json.dumps(val, indent=2, ensure_ascii=False))
    else:
        print(val)
