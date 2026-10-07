"""Outbound notifications.

The agent's loop is unattended, so its only defence against silent failure is
that a human can see, at any hour, what it decided and why. Telegram is used
because it is a single HTTPS call with no SDK.

Set ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID``. With no token configured
the module degrades to stdout — never to silence.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def send(text: str, *, silent: bool = False) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print(f"[notify:stdout] {text}")
        return False
    payload = urllib.parse.urlencode({
        "chat_id": chat, "text": text, "disable_notification": str(silent).lower(),
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception as exc:  # pragma: no cover - network best effort
        print(f"[notify:error] {exc} :: {text}")
        return False


def format_plan(plan_dict: dict, signal_reason: str = "") -> str:
    return (
        f"*{plan_dict['holdSide'].upper()} {plan_dict['symbol']}*\n"
        f"entry `{plan_dict['entry']}` · stop `{plan_dict['stop']}` · target `{plan_dict['target']}`\n"
        f"size `{plan_dict['size']}` @ {plan_dict['leverage']}x · margin {plan_dict['marginUsd']} USDT\n"
        f"risk {plan_dict['riskUsd']} USDT → reward {plan_dict['rewardUsd']} USDT (RR {plan_dict['rr']})\n"
        f"_{signal_reason}_"
    )


def format_screen(regimes: list[dict]) -> str:
    lines = ["*NightShift screen*"]
    for r in regimes:
        mark = "✅" if r["tradeable"] else "⛔"
        lines.append(f"{mark} `{r['symbol']}` ER {r['er']} · range {r['range_pct']}% — {r['reason']}")
    return "\n".join(lines)
