TRAILING REPOSITION — Bot near boundary, EXECUTE trailing logic now.

## Trigger
{trigger_reason}

## Current state
- PAXG price: {price}
- Bot range: {bottom} - {top}
- Distance to top: {dist_top_pct:.1%}
- Distance to bottom: {dist_bottom_pct:.1%}

## What to do (deterministic — no discretion)

1. Use the Pionex MCP to call `pionex_bot_spot_grid_adjust_params` on the active bot:
   - `buOrderId`: the BOT_ID from config (currently `76e6b1c8-af3b-42a0-b813-7462d60b303e`)
   - `top`: round({price} + 75, 0)  (price + half-width)
   - `bottom`: round({price} - 75, 0)  (price - half-width)
   - `row`: 8  (unchanged — optimitzat 2026-05-12)

2. This re-centers the bot $150 wide around current price. `adjust_params` is in-place
   (no cancel + create), so cost is ~$0. The bot keeps its PAXG inventory and only re-places
   limit orders to the new levels.

3. If `adjust_params` fails (returns an error), fallback to:
   - `pionex_bot_spot_grid_cancel` with `closeSellModel="NOT_SELL"` (keep PAXG in wallet)
   - `pionex_bot_spot_grid_create` with the new range and `closeSellModel="NOT_SELL"`

## Why this is correct
- The PAXG bought at higher prices is held long-term (HODL). Crystallizing as loss is meaningless.
- adjust_params preserves the inventory and just shifts the order book.
- Cost ~$0 means we can re-center freely without erosion.

## Output format

```
DECISION: REPOSITION
REASONING: trailing recenter, range shifted to {price}±$75 via adjust_params
NEW_RANGE: [bottom_rounded, top_rounded]
ACTION_TAKEN: adjust_params executed (or fallback used)
```

After executing, log the result. Do NOT wait, do NOT analyze macro, do NOT ask. Just execute.
