"""NightShift — an agentic trading desk that keeps working while humans sleep.

Public surface kept intentionally small:

    from nightshift import Bitget, Credentials, NightShiftAgent, AgentConfig

Everything else (regime, signals, risk, journal, notify) is importable but is
an implementation detail of the loop.
"""

from .agent import AgentConfig, NightShiftAgent, summarize
from .exchange import Bitget, BitgetError, Credentials
from .journal import lessons, record, review
from .regime import Regime, efficiency_ratio, screen
from .risk import Plan, RiskRefusal, build_plan, liquidation_price, position_size
from .signals import ENGINES, Signal, atr, evaluate, evaluate_all, rsi

__version__ = "0.1.0"

__all__ = [
    "AgentConfig", "NightShiftAgent", "summarize",
    "Bitget", "BitgetError", "Credentials",
    "record", "review", "lessons",
    "Regime", "screen", "efficiency_ratio",
    "Plan", "RiskRefusal", "build_plan", "position_size", "liquidation_price",
    "Signal", "evaluate", "evaluate_all", "ENGINES", "rsi", "atr",
]
