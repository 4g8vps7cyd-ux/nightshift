"""Minimal, dependency-free Bitget client for USDT-M futures.

Why hand-rolled: the hackathon agent must run on a bare VPS with no SDK, no
`pip install` beyond stdlib, and must be auditable line by line by judges.

Covers both account generations Bitget currently serves:

* **Classic**  -> ``/api/v2/mix/*``   (works on a Classic account)
* **UTA**      -> ``/api/v3/*``       (Unified Trading Account, Agent Hub)

The caller picks with ``api_version`` (``"classic"`` / ``"uta"``), or passes
``"auto"`` and the first read-only probe decides. The two families are mutually
exclusive and each rejects the other's endpoints, so guessing is not an option:
a Classic endpoint on a UTA account answers ``40085``, and a UTA endpoint on a
Classic account answers ``40084``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

HOST = "https://api.bitget.com"


class BitgetError(RuntimeError):
    """Raised for any non-00000 business code, with the raw payload attached."""

    def __init__(self, code: str, msg: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg
        self.payload = payload or {}


@dataclass(frozen=True)
class Credentials:
    key: str
    secret: str
    passphrase: str

    @classmethod
    def from_env(cls) -> "Credentials":
        return cls(
            os.environ["BITGET_API_KEY"],
            os.environ["BITGET_API_SECRET"],
            os.environ["BITGET_API_PASSPHRASE"],
        )

    @classmethod
    def from_file(cls, path: str) -> "Credentials":
        raw = json.load(open(path, encoding="utf-8"))

        def pick(*names: str) -> str:
            for n in names:
                if raw.get(n):
                    return raw[n]
            raise KeyError(f"missing one of {names} in {path}")

        return cls(pick("API_KEY", "apiKey"), pick("API_SECRET", "apiSecret"),
                   pick("API_PASSWORD", "passphrase"))


def _sign(secret: str, ts: str, method: str, path: str, query: str, body: str) -> str:
    message = ts + method.upper() + path + query + body
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class Bitget:
    """Signed REST client. One instance per process is enough."""

    def __init__(self, creds: Credentials | None = None, product_type: str = "USDT-FUTURES",
                 margin_coin: str = "USDT", timeout: int = 20, api_version: str = "classic") -> None:
        if api_version not in ("classic", "uta"):
            raise ValueError(f"api_version must be 'classic' or 'uta', got {api_version!r}")
        self.creds = creds
        self.product_type = product_type
        self.margin_coin = margin_coin
        self.timeout = timeout
        self.api_version = api_version
        self._hold_mode: str | None = None      # 'hedge_mode' | 'one_way_mode' (UTA)

    @property
    def uta(self) -> bool:
        return self.api_version == "uta"

    def detect_api_version(self) -> str:
        """Probe which API family the account answers, then store it.

        Read-only, one call. The account generation can change between sessions
        (a voucher expiring is enough to let Bitget upgrade the account), so this
        is re-probed instead of cached: assuming "it was classic yesterday" is
        the fastest way to waste an hour debugging the wrong layer.
        """
        try:
            self._call("GET", "/api/v3/account/settings", {"productType": self.product_type})
            self.api_version = "uta"
        except BitgetError as exc:
            if exc.code == "40084":
                self.api_version = "classic"
            else:
                raise
        return self.api_version

    def hold_mode(self, refresh: bool = False) -> str:
        """``'hedge_mode'`` or ``'one_way_mode'`` — read from the exchange, not assumed.

        A one-way account answers a hedge-shaped body with
        ``25200 SINGLE_SIDE_HOLD``; a hedge account needs the position side spelled
        out. Guessing wrong means the order never leaves the client — and it stays
        wrong every cycle, so the bot looks alive while never trading. Classic has
        no such setting: it always carries ``holdSide``.
        """
        if not self.uta:
            return "hedge_mode"
        if self._hold_mode is None or refresh:
            data = self._call("GET", "/api/v3/account/settings",
                              {"productType": self.product_type}) or {}
            raw = str(data.get("holdMode") or "").lower()
            self._hold_mode = "hedge_mode" if "hedge" in raw else "one_way_mode"
        return self._hold_mode

    @property
    def one_way(self) -> bool:
        return self.hold_mode() == "one_way_mode"

    def _post_adapted(self, path: str, build) -> Any:
        """POST a UTA order, rebuilding the body if the hold mode moved underfoot.

        ``build`` runs immediately before each attempt, so refreshing the cached
        mode after a ``SINGLE_SIDE_HOLD`` rejection actually changes what gets
        sent — the user can flip the mode in the app at any moment.
        """
        try:
            return self._call("POST", path, body=build())
        except BitgetError as exc:
            if not _single_side_error(exc):
                raise
            self.hold_mode(refresh=True)
            return self._call("POST", path, body=build())

    # ------------------------------------------------------------------ http
    def _call(self, method: str, path: str, query: dict | None = None,
              body: dict | None = None, signed: bool = True) -> Any:
        qs = "?" + urllib.parse.urlencode(query) if query else ""
        raw = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if signed:
            if self.creds is None:
                raise BitgetError("no-credentials", f"{path} requires credentials")
            ts = str(int(time.time() * 1000))
            headers.update({
                "ACCESS-KEY": self.creds.key,
                "ACCESS-SIGN": _sign(self.creds.secret, ts, method, path, qs, raw),
                "ACCESS-TIMESTAMP": ts,
                "ACCESS-PASSPHRASE": self.creds.passphrase,
            })
        req = urllib.request.Request(HOST + path + qs, data=(raw.encode() if raw else None),
                                     method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode())
            except Exception:  # pragma: no cover - non-JSON gateway error
                raise BitgetError(str(exc.code), "non-JSON error response") from exc
        if payload.get("code") not in ("00000", None):
            raise BitgetError(payload.get("code", "?"), payload.get("msg", "unknown"), payload)
        return payload.get("data")

    # ----------------------------------------------------------------- market
    def ticker(self, symbol: str) -> dict:
        data = self._call("GET", "/api/v2/mix/market/ticker",
                          {"symbol": symbol, "productType": self.product_type}, signed=False)
        return data[0] if isinstance(data, list) else data

    def last_price(self, symbol: str) -> float:
        return float(self.ticker(symbol)["lastPr"])

    def candles(self, symbol: str, granularity: str = "1H", limit: int = 200) -> list[list[float]]:
        """Return OHLCV as **oldest-first** rows: [ts, open, high, low, close, baseVol].

        Bitget's ordering has flipped between endpoints/releases, so we never
        trust it: every row is parsed and the list is sorted explicitly. A
        silently reversed series is the classic way to backtest a fantasy.
        """
        rows = self._call("GET", "/api/v2/mix/market/candles",
                          {"symbol": symbol, "productType": self.product_type,
                           "granularity": granularity, "limit": str(limit)}, signed=False)
        out = []
        for r in rows or []:
            ts, o, h, l, c, v = (float(r[0]), float(r[1]), float(r[2]),
                                 float(r[3]), float(r[4]), float(r[5]))
            out.append([ts, o, h, l, c, v])
        out.sort(key=lambda x: x[0])
        return out

    def candles_history(self, symbol: str, granularity: str = "15m", pages: int = 6,
                        limit: int = 1000) -> list[list[float]]:
        """Page backwards through history, newest page first, merged and sorted.

        One request returns at most 1000 bars, so a multi-week paper run needs
        pagination. Pages are keyed by timestamp and merged in a dict: an
        exchange that overlaps or repeats a boundary bar cannot corrupt the
        series or duplicate a trade.
        """
        rows: dict[float, list[float]] = {}
        end_time: float | None = None
        for _ in range(max(1, pages)):
            query = {"symbol": symbol, "productType": self.product_type,
                     "granularity": granularity, "limit": str(limit)}
            if end_time:
                query["endTime"] = str(int(end_time))
            try:
                data = self._call("GET", "/api/v2/mix/market/candles", query, signed=False)
            except BitgetError:
                break
            if not data:
                break
            for r in data:
                rows[float(r[0])] = [float(r[0]), float(r[1]), float(r[2]),
                                     float(r[3]), float(r[4]), float(r[5])]
            oldest = min(float(r[0]) for r in data)
            if end_time is not None and oldest >= end_time:
                break                      # no forward progress, stop instead of looping
            end_time = oldest
            time.sleep(0.25)               # be a good citizen on a public endpoint
        return [rows[ts] for ts in sorted(rows)]

    def contract_spec(self, symbol: str) -> dict:
        """Tick/size rules — required so orders are not rejected for precision."""
        data = self._call("GET", "/api/v2/mix/market/contracts",
                          {"symbol": symbol, "productType": self.product_type}, signed=False)
        row = data[0] if isinstance(data, list) else data
        return {
            "min_size": float(row.get("minTradeNum") or 0),
            "size_step": float(row.get("sizeMultiplier") or 0),
            "price_step": float(row.get("pricePlace") and 10 ** -int(row["pricePlace"]) or 0),
            "price_place": int(row.get("pricePlace") or 0),
            "max_leverage": int(float(row.get("maxLever") or 1)),
        }

    # ---------------------------------------------------------------- account
    def account(self) -> dict:
        if self.uta:
            data = self._call("GET", "/api/v3/account/assets", {"productType": self.product_type})
            return data if isinstance(data, dict) else (data or [{}])[0]
        data = self._call("GET", "/api/v2/mix/account/accounts", {"productType": self.product_type})
        return data[0] if isinstance(data, list) else data

    def equity(self) -> float:
        return float(self.account().get("accountEquity") or 0)

    def positions(self, symbol: str | None = None) -> list[dict]:
        if self.uta:
            query = {"category": self.product_type}
            if symbol:
                query["symbol"] = symbol
            data = self._call("GET", "/api/v3/position/current-position", query)
            rows = (data or {}).get("list") if isinstance(data, dict) else data
            return [p for p in (rows or []) if abs(float(p.get("total") or 0)) > 0]
        query = {"productType": self.product_type, "marginCoin": self.margin_coin}
        if symbol:
            query["symbol"] = symbol
        data = self._call("GET", "/api/v2/mix/position/all-position", query)
        return [p for p in (data or []) if abs(float(p.get("total") or 0)) > 0]

    def set_leverage(self, symbol: str, leverage: int, hold_side: str) -> Any:
        if self.uta:
            def build() -> dict[str, Any]:
                # One-way accounts take a single `leverage`; hedge accounts want
                # both sides set together (and reject `leverage` + `posSide` with
                # 25200 — see docs/WRONG.md).
                if self.one_way:
                    return {"category": self.product_type, "symbol": symbol,
                            "marginMode": "isolated", "leverage": str(leverage)}
                return {"category": self.product_type, "symbol": symbol,
                        "marginMode": "isolated", "longLeverage": str(leverage),
                        "shortLeverage": str(leverage)}
            return self._post_adapted("/api/v3/account/set-leverage", build)
        return self._call("POST", "/api/v2/mix/account/set-leverage", body={
            "symbol": symbol, "productType": self.product_type, "marginCoin": self.margin_coin,
            "leverage": str(leverage), "holdSide": hold_side,
        })

    # ----------------------------------------------------------------- orders
    _POS_SIDE = {"open": {"buy": "long", "sell": "short"},
                 "close": {"buy": "short", "sell": "long"}}

    def place_order(self, symbol: str, side: str, size: float, *, trade_side: str = "open",
                    order_type: str = "market", price: float | None = None,
                    stop_loss: float | None = None, take_profit: float | None = None,
                    margin_mode: str = "isolated", client_oid: str | None = None) -> dict:
        """Place an order **with its protective rails attached in the same call**.

        Preset TP/SL is the whole point of this client. Sending the entry first
        and the stop a moment later leaves a real, unbounded window in which the
        position is naked — exactly the window in which markets tend to move.
        """
        if self.uta:
            def build() -> dict[str, Any]:
                body: dict[str, Any] = {
                    "category": self.product_type, "symbol": symbol, "qty": _num(size),
                    "side": side, "orderType": order_type, "marginMode": margin_mode,
                }
                # Hedge accounts require the position side; one-way accounts reject
                # it with 25200 SINGLE_SIDE_HOLD. v3 has no `productType`.
                if not self.one_way:
                    body["posSide"] = self._POS_SIDE[trade_side][side]
                if trade_side == "close":
                    body["reduceOnly"] = "YES"
                if price is not None:
                    body["price"] = _num(price)
                if stop_loss is not None:
                    # v3 names these `stopLoss`/`takeProfit`. The `presetStop*`
                    # spellings belong to v2: sent here they are rejected with
                    # "Parameter takeProfit cannot be empty" — and a silently
                    # ignored rail is worse than a loud one, because the client
                    # would believe a naked position was protected. `rails_of`
                    # reads the rails back from the exchange for exactly this
                    # reason; the tests pin the field names.
                    body["stopLoss"] = _num(stop_loss)
                    body["slTriggerBy"] = "mark"
                if take_profit is not None:
                    body["takeProfit"] = _num(take_profit)
                    body["tpTriggerBy"] = "mark"
                if client_oid:
                    body["clientOid"] = client_oid
                return body
            return self._post_adapted("/api/v3/trade/place-order", build)

        body = {
            "symbol": symbol, "productType": self.product_type, "marginCoin": self.margin_coin,
            "marginMode": margin_mode, "side": side, "tradeSide": trade_side,
            "orderType": order_type, "size": _num(size),
        }
        if price is not None:
            body["price"] = _num(price)
        if stop_loss is not None:
            body["presetStopLossPrice"] = _num(stop_loss)
        if take_profit is not None:
            body["presetStopSurplusPrice"] = _num(take_profit)
        if client_oid:
            body["clientOid"] = client_oid
        return self._call("POST", "/api/v2/mix/order/place-order", body=body)

    def close_position(self, symbol: str, hold_side: str) -> dict:
        if self.uta:
            # v3 has no "close everything" endpoint: a reduce-only market order
            # needs an explicit size, so read the live position first and refuse
            # rather than send a zero-quantity close into the void.
            size = 0.0
            for p in self.positions(symbol):
                side = str(p.get("holdSide") or p.get("posSide") or "").lower()
                if side == hold_side:
                    size = abs(float(p.get("total") or 0))
            if size <= 0:
                raise BitgetError("no-position", f"no {hold_side} position in {symbol} to close")
            return self.place_order(symbol, "sell" if hold_side == "long" else "buy", size,
                                    trade_side="close")
        return self._call("POST", "/api/v2/mix/order/close-positions", body={
            "symbol": symbol, "productType": self.product_type, "holdSide": hold_side,
        })

    def plan_orders(self, plan_type: str = "profit_loss") -> list[dict]:
        """Orders carrying a ``planType``: protection plans *and* trigger orders.

        On UTA those live in the open-orders list next to ordinary limit orders,
        so the caller must not assume every row is a stop — ``rails_of`` is the
        one that decides which rows protect the position.
        """
        if self.uta:
            data = self._call("GET", "/api/v3/trade/unfilled-orders",
                              {"category": self.product_type})
            rows = (data or {}).get("list") if isinstance(data, dict) else data
            return [o for o in (rows or []) if o.get("planType")]
        data = self._call("GET", "/api/v2/mix/order/orders-plan-pending",
                          {"productType": self.product_type, "planType": plan_type})
        if isinstance(data, dict):
            return data.get("entrustedList") or []
        return data or []

    def order_history(self, symbol: str | None = None, limit: int = 50) -> list[dict]:
        if self.uta:
            query = {"category": self.product_type, "limit": str(limit)}
            if symbol:
                query["symbol"] = symbol
            data = self._call("GET", "/api/v3/trade/history-orders", query)
            rows = (data or {}).get("list") if isinstance(data, dict) else data
            return rows or []
        query = {"productType": self.product_type, "limit": str(limit)}
        if symbol:
            query["symbol"] = symbol
        data = self._call("GET", "/api/v2/mix/order/history", query)
        if isinstance(data, dict):
            return data.get("entrustedList") or []
        return data or []

    def rails_of(self, symbol: str) -> dict[str, float]:
        """Read back the protective orders actually living on the exchange.

        Only rows whose ``planType`` names a plan are considered: a plain limit
        order carries no ``loss*``/``profit*`` plan type, and treating one as a
        take-profit would mark a naked position as protected. Unknown plan types
        are skipped rather than assumed.
        """
        out: dict[str, float] = {}
        for plan in self.plan_orders("profit_loss"):
            if plan.get("symbol") != symbol:
                continue
            kind_raw = str(plan.get("planType", "")).lower()
            if kind_raw.startswith("loss"):
                kind = "stop_loss"
            elif kind_raw.startswith("profit"):
                kind = "take_profit"
            else:
                continue
            try:
                out[kind] = float(plan.get("triggerPrice"))
            except (KeyError, TypeError, ValueError):
                continue
        return out


def _single_side_error(exc: Exception) -> bool:
    """True for Bitget's ``25200 SINGLE_SIDE_HOLD``: a hedge body met a one-way account.

    The account's hold mode can be flipped in the app at any moment, so this is a
    recoverable, re-readable condition — not a reason to give up on the cycle.
    """
    text = f"{getattr(exc, 'code', '')} {exc}".upper()
    return "25200" in text or "SINGLE_SIDE" in text


def _num(value: float) -> str:
    """Trim floats to a stable decimal string (avoids 4120.000000000001)."""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def chunked(items: Iterable, size: int):
    buf: list = []
    for item in items:
        buf.append(item)
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf
