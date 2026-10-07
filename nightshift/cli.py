"""Command line entry point.

    nightshift screen                      # regime + level report, no orders
    nightshift signals --symbol XAUUSDT    # every engine's opinion, one symbol
    nightshift plan --symbol XAUUSDT --engine rsi   # what the agent would send
    nightshift once                        # one full cycle (dry-run unless --live)
    nightshift run --cycles 12 --live      # unattended loop
    nightshift watch                       # open positions vs their rails
    nightshift review                      # expectancy + lessons from the journal
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .agent import DEFAULT_UNIVERSE, AgentConfig, NightShiftAgent
from .exchange import Bitget, BitgetError, Credentials
from .journal import DEFAULT_PATH, lessons, review
from .risk import RiskRefusal
from .signals import ENGINES, evaluate_all, rsi

DEFAULTS = {
    "credentials": None, "symbols": None, "timeframe": "15m", "engine": "rsi",
    "leverage": 50, "risk": 1.0, "max_positions": 1, "er_floor": 0.10,
    "interval": 300, "journal": DEFAULT_PATH, "timeout": 20, "live": False,
}


def _opt(args, name):
    """Read a shared option, falling back to its default when suppressed."""
    value = getattr(args, name, None)
    return DEFAULTS[name] if value is None else value


def _symbols(args) -> list[str] | None:
    """Normalise ``--symbols`` given as repeats or comma-separated.

    ``nargs="+"`` cannot be used here: a greedy multi-value option on the
    top-level parser eats whatever follows, so ``--symbols XAUUSDT screen``
    swallows ``screen`` and the CLI reports a missing command. Accepting
    ``--symbols A,B`` and repeated ``--symbols A --symbols B`` keeps the option
    position-independent, which is what users actually rely on.
    """
    raw = _opt(args, "symbols") or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw:
        out += [part.strip().upper() for part in str(item).split(",") if part.strip()]
    return out or None


def _client(args) -> Bitget:
    creds = None
    if _opt(args, "credentials"):
        creds = Credentials.from_file(_opt(args, "credentials"))
    elif os.environ.get("BITGET_API_KEY"):
        creds = Credentials.from_env()
    return Bitget(creds, timeout=_opt(args, "timeout"))


def _agent(args) -> NightShiftAgent:
    cfg = AgentConfig(
        universe=_symbols(args) or list(DEFAULT_UNIVERSE),
        timeframe=_opt(args, "timeframe"),
        engine=_opt(args, "engine"),
        leverage=_opt(args, "leverage"),
        risk_pct=_opt(args, "risk"),
        max_positions=_opt(args, "max_positions"),
        er_floor=_opt(args, "er_floor"),
        interval_seconds=_opt(args, "interval"),
        journal_path=_opt(args, "journal"),
    )
    return NightShiftAgent(_client(args), cfg, execute=bool(_opt(args, "live")))


def cmd_screen(args) -> int:
    agent = _agent(args)
    scans = agent.scan_universe()
    for s in scans:
        r = s["regime"]
        flag = "TRADEABLE" if r.tradeable else "SKIP"
        print(f"{s['symbol']:>10}  {s['price']:>10.2f}  ER {r.er:>4.2f}  range {r.range_pct:>5.2f}%  "
              f"RSI {rsi(s['candles']):>5.1f}  [{flag}] {r.reason}")
        for lv in r.levels:
            print(f"{'':>10}    {lv.kind:<10} {lv.price:>10.2f}  touches {lv.touches:>2}  "
                  f"breaks {lv.breaks:>2}  bounce {lv.bounce_pct:>5.2f}%")
    return 0


def cmd_signals(args) -> int:
    agent = _agent(args)
    for symbol in agent.cfg.universe:
        candles = agent.client.candles(symbol, agent.cfg.timeframe, 200)
        print(f"\n== {symbol} @ {candles[-1][4]:.4f} ==")
        for sig in evaluate_all(candles, symbol=symbol):
            print(f"  {sig.engine:<11} {sig.action:<6} conf {sig.confidence:.2f}  {sig.reason}")
    return 0


def cmd_plan(args) -> int:
    agent = _agent(args)
    scans = agent.scan_universe()
    if args.symbol:
        scans = [s for s in scans if s["symbol"] == args.symbol]
    if not scans:
        print("symbol not in universe", file=sys.stderr)
        return 2
    for s in scans:
        try:
            plan = agent.build(s)
        except RiskRefusal as refusal:
            print(f"{s['symbol']}: REFUSED — {refusal}")
            continue
        print(json.dumps(plan.as_dict(), indent=2))
    return 0


def cmd_once(args) -> int:
    agent = _agent(args)
    print(json.dumps(agent.cycle(), indent=2, default=str))
    return 0


def cmd_run(args) -> int:
    agent = _agent(args)
    agent.run(cycles=args.cycles)
    return 0


def cmd_watch(args) -> int:
    agent = _agent(args)
    rows = agent.watch()
    if not rows:
        print("no open positions")
    for row in rows:
        print(json.dumps(row, indent=2, default=str))
    return 0


def cmd_review(args) -> int:
    """Expectancy, per-engine attribution, and plain-language lessons."""
    print(json.dumps(review(_opt(args, "journal")), indent=2))
    print()
    for line in lessons(_opt(args, "journal")):
        print(f"- {line}")
    return 0


def _shared_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--credentials", default=argparse.SUPPRESS,
                        help="JSON with API_KEY/API_SECRET/API_PASSWORD")
    common.add_argument("--symbols", action="append", default=argparse.SUPPRESS,
                        help="universe, repeatable or comma-separated "
                             "(e.g. --symbols XAUUSDT,BTCUSDT)")
    common.add_argument("--timeframe", default=argparse.SUPPRESS)
    common.add_argument("--engine", choices=sorted(ENGINES), default=argparse.SUPPRESS)
    common.add_argument("--leverage", type=int, default=argparse.SUPPRESS)
    common.add_argument("--risk", type=float, default=argparse.SUPPRESS,
                        help="percent of equity per trade")
    common.add_argument("--max-positions", type=int, default=argparse.SUPPRESS)
    common.add_argument("--er-floor", type=float, default=argparse.SUPPRESS)
    common.add_argument("--interval", type=int, default=argparse.SUPPRESS)
    common.add_argument("--journal", default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    common.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                        help="actually send orders (default: dry-run)")
    return common


def build_parser() -> argparse.ArgumentParser:
    """Options work before *or* after the subcommand.

    Declaring the greedy ``--symbols`` only on the top-level parser makes
    ``--symbols XAUUSDT screen`` eat ``screen`` as a second symbol, then fail
    with "the following arguments are required: command". A parent parser plus
    ``SUPPRESS`` defaults removes the footgun without duplicating defaults.
    """
    common = _shared_options()
    p = argparse.ArgumentParser(prog="nightshift", parents=[common], description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    commands = [("screen", cmd_screen, "regime + levels"),
                ("signals", cmd_signals, "every engine's opinion"),
                ("plan", cmd_plan, "the order the agent would send"),
                ("once", cmd_once, "one full cycle"),
                ("run", cmd_run, "unattended loop"),
                ("watch", cmd_watch, "positions vs rails"),
                ("review", cmd_review, "expectancy + lessons")]
    for name, fn, helptext in commands:
        sp = sub.add_parser(name, parents=[common], help=helptext)
        if name == "plan":
            sp.add_argument("--symbol", required=True)
        if name == "run":
            sp.add_argument("--cycles", type=int, default=None)
        sp.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BitgetError as exc:
        print(f"exchange error: {exc}", file=sys.stderr)
        return 1
    except RiskRefusal as exc:
        print(f"risk refusal: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
