"""Grid Manager Config — central settings."""
import os
from pathlib import Path

# ─── Bots to monitor (portfolio of 4 trailing grids) ─────────────────
BOT_ID = "76e6b1c8-af3b-42a0-b813-7462d60b303e"   # legacy single-bot var (PAXG, kept for compat)
SYMBOL = "PAXG_USDT"
BASE = "PAXG"
QUOTE = "USDT"

# Multi-bot config
BOTS = {
    "PAXG_USDT": {
        "id": "6a8b29cf-d43a-4604-bac8-4c3ee4b0fea5",  # re-creat 2026-05-23 test vault relauncher (parent 76e6b1c8)
        "symbol": "PAXG_USDT",
        "base": "PAXG",
        "quote": "USDT",
        "width_pct": 0.032,
        "rows": 8,
    },
    "BTC_USDT": {
        "id": "35720ef3-45ea-4864-9347-52b6dad0e222",
        "symbol": "BTC_USDT",
        "base": "BTC",
        "quote": "USDT",
        "width_pct": 0.0516,
        "rows": 12,
    },
    "ETH_USDT": {
        "id": "b9b4db3c-e6cf-45fb-abad-0c1d185c5ea4",  # re-creat 2026-05-13: vol-indexed, step 0.56%
        "symbol": "ETH_USDT",
        "base": "ETH",
        "quote": "USDT",
        "width_pct": 0.067,   # range $2220-$2374 sobre preu ~$2297 (cobreix daily P75 ETH 6.68%)
        "rows": 12,
    },
    "SOL_USDT": {
        "id": "1a71efd2-4955-4587-8823-04eac3f4a367",  # re-creat 2026-05-13: vol-indexed, step 0.78%
        "symbol": "SOL_USDT",
        "base": "SOL",
        "quote": "USDT",
        "width_pct": 0.070,   # range $91.91-$98.57 sobre preu ~$95.24 (cobreix daily P75 SOL 7.06%)
        "rows": 9,
    },
    "USOX_USDT": {
        "id": "c3b1a652-7673-4757-8405-00e69532ae1c",  # creat 2026-05-14: diversificacio 10% portfolio
        "symbol": "USOX_USDT",
        "base": "USOX",
        "quote": "USDT",
        "width_pct": 0.150,   # range $131.57-$152.89 sobre preu ~$142.23 (cobreix daily P75 USOX 7.90%×2)
        "rows": 10,
    },
    "SPYX_USDT": {
        "id": "995435be-7b15-4ac8-9514-2f80497e6619",  # recreat 2026-05-21: realloc 15->10%, $136 (era $200, $64 a PAXG)
        "symbol": "SPYX_USDT",
        "base": "SPYX",
        "quote": "USDT",
        "width_pct": 0.0435,  # range $728.01-$760.37 sobre preu ~$744 (= range última setmana + 2 nivells extra/banda)
        "rows": 10,
    },
}

# Target weights for rebalancing (2026-05-21: SPYX 15→10, PAXG 30→35)
TARGET_WEIGHTS = {
    "PAXG_USDT": 0.35,
    "BTC_USDT":  0.22,
    "ETH_USDT":  0.15,
    "SOL_USDT":  0.08,
    "USOX_USDT": 0.10,
    "SPYX_USDT": 0.10,
}

# ─── Rebalancer (portfolio level, on top of grids) ──────────────────
# Threshold uniforme 5% per a tots els bots
REBALANCE_THRESHOLDS = {
    "PAXG_USDT": 0.05,
    "BTC_USDT":  0.05,
    "ETH_USDT":  0.05,
    "SOL_USDT":  0.05,
    "USOX_USDT": 0.05,
    "SPYX_USDT": 0.05,
}
MIN_REBALANCE_USD = 10.0          # Moviment mínim per executar (evita microcicles)
REBALANCE_COOLDOWN_MIN = 30        # Min entre rebalanceigs del mateix bot
REBALANCE_SHADOW_MODE = False      # True = només LOG, False = EXECUTA real

# ─── Thresholds ──────────────────────────────────────────────────────
EDGE_TRIGGER_PCT = 0.10        # 10% from boundary → trigger trailing
DAILY_CHECK_HOUR_UTC = 22       # 22:00 UTC = NY close
POLLING_INTERVAL_SEC = 300      # 5 min polling

# ─── Reposition rules (asymmetric) ──────────────────────────────────
BIAS_LOW_PCT = 0.20             # new center = current - 0.2 * half_width
WIDTH_MULT_NORMAL = 1.5         # half_width = 1.5 × ATR(7)
WIDTH_MULT_MACRO = 2.25         # ×1.5 extra during macro events
DOWN_BREAKOUT_WAIT_DAYS = 5     # patience for down break before forcing
DOWN_BREAKOUT_FORCE_PCT = 0.05  # 5% below floor → force reposition
MIN_GRID_STEP_USD = 15          # min step to keep fees < 30% of gross

# ─── Cost gates ──────────────────────────────────────────────────────
ADJUST_COST_USD = 0.3           # estimated cost per reposition
MIN_PROFIT_TO_ADJUST = 1.0      # only adjust if expected profit > $1 over coming days

# ─── Paths ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
DB_PATH = BASE_DIR / "db" / "grid_manager.sqlite"
PROMPTS_DIR = BASE_DIR / "prompts"
PIONEX_CONFIG = Path.home() / ".pionex" / "config.toml"

# ─── Claude CLI ──────────────────────────────────────────────────────
CLAUDE_CLI = r"C:\nodejs\node-v22.14.0-win-x64\claude.cmd"  # absolute path (works from Task Scheduler)
CLAUDE_TIMEOUT_SEC = 300         # 5 min max per Claude call

# ─── Notifications (optional) ───────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
