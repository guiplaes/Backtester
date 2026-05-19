# PAXG Grid Manager

Automated Claude-driven monitoring and adjustment of a Pionex spot grid bot for PAXG (gold).

## How it works

1. **Boundary monitor** (every 5 min): checks if price is near range edge
2. **Daily review** (22:00 UTC): full analysis + macro context via Claude
3. **Claude CLI** is invoked when triggers fire
4. **Pionex MCP** is used by Claude to read/modify the bot
5. **SQLite log** of all snapshots + decisions
6. **Streamlit dashboard** for visualization

## Asymmetric adjustment philosophy

- UPPER breakout → reposition aggressively (we have USDT)
- LOWER breakout → wait 5 days or 5%+ below floor before forcing
- New range center is slightly low-biased (favors keeping PAXG long-term)
- Macro events (CPI/FOMC/NFP) widen the range

## Setup (one-time)

1. **Install Python deps**:
   ```
   pip install requests tomli streamlit pandas
   ```

2. **Verify Pionex API config** at `~/.pionex/config.toml`:
   ```toml
   api_key = "your_key"
   api_secret = "your_secret"
   ```

3. **Verify Claude CLI** is in PATH:
   ```
   claude --version
   ```

4. **Test components manually**:
   ```bash
   python pionex_client.py    # should show PAXG ticker + balance
   python db.py               # initializes SQLite
   python monitor.py          # one boundary check
   python daily_check.py      # one daily analysis
   streamlit run dashboard.py --server.port 8502 # UI at localhost:8502 (8501 is BTC lab)
   ```

5. **Register scheduled tasks** (run as Admin):
   ```
   setup_scheduler.bat
   ```

## Update bot ID

Edit `config.py` and change `BOT_ID` if you create a new bot.

## Logs

- `logs/monitor.log` — 5-min boundary checks
- `logs/daily.log` — daily reviews
- `db/grid_manager.sqlite` — all data

## Manual run

```bash
# Force check now
python monitor.py

# Force daily review
python daily_check.py

# Open dashboard (port 8502; 8501 is reserved for BTC lab)
streamlit run dashboard.py --server.port 8502
```

## Disable automation

```cmd
schtasks /Delete /TN "GridManager_Monitor" /F
schtasks /Delete /TN "GridManager_Daily" /F
```
