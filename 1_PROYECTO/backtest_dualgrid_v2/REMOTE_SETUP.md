# Setup ràpid en un altre PC

## TL;DR (3 passos)

1. Instal·la MT5 + Claude Code (15 min)
2. Clone el repositori
3. `claude` → diu "executa SETUP_REMOTE.bat"

## Detall

### Pas 1 — Pre-requisits (només primera vegada)

**Descarregar i instal·lar (manual):**
- MT5 VTMarkets: https://vtmarkets.com → download MT5 → instal·lar
- Python 3.10+: https://python.org/downloads (✓ Add to PATH)
- Node.js: https://nodejs.org (per Claude Code)
- Claude Code: terminal → `npm install -g @anthropic-ai/claude-code`

**Configurar MT5 manualment:**
- Obrir MT5 → File → Login to Trade Account
- Server: `VTMarkets-Demo` | Login: `1110830` | Password: `lN5V7&QK`
- Esperar fins icona verda (connectat)

### Pas 2 — Clonar projecte

```cmd
cd C:\Users\<TuUsuari>\Desktop
git clone <url-del-repo-github> "MT4 Claude"
cd "MT4 Claude"
```

(O copia el folder per USB si no tens GitHub configurat)

### Pas 3 — Auto-setup amb Claude

```cmd
claude
```

Quan Claude estigui obert:
```
Executa el script SETUP_REMOTE.bat al projecte
```

El script farà tot automàticament:
1. Detecta MT5 install
2. Crea 6 slots paral·lel (C:\MT5_Tester{1..6})
3. Copia accounts.dat per autologin
4. Sincronitza EA
5. Adapta run_batch_parallel.py per 6 slots

### Pas 4 — Recuperar context de la sessió actual

Al Claude del PC nou, mostra-li aquests fitxers per context:
- `1_PROYECTO/backtest_dualgrid_v2/BREAKTHROUGH_P04.md` (millors troballes)
- `MT5/MQL5/Experts/DualGridEA_v2_Reset.mq5` (EA actual)
- `1_PROYECTO/backtest_dualgrid_v2/results.csv` (tots resultats històrics)
- Aquest fitxer (REMOTE_SETUP.md)

I li dius: "continua la investigació del backtest dual-grid des d'on van quedar"

## Avantatges PC potent

Amb i7 + 32GB pots tenir 6-8 slots paral·lel (3 ara) → **2-3× més ràpid el cribage**.
Model=4 (real ticks) viable: 6 mesos en ~6h (vs 18h ara).

## Estat actual de la investigació (perquè Claude tingui context)

- Grid bidireccional XAU **NO funciona en tics reals amb config tradicional**
- Model=1 (1m OHLC) **infla resultats sistemàticament 200-300%**
- **Wide grid** (sp=$30-$50) sobreviu i pot generar profit modest
- W1 (sp=$30 noVD) = **+$2,025 Feb 2026** (millor fins ara)
- Nou mecanisme **Progressive Trim** afegit a EA — tanca pitjor posició si gap>X%
- Cal validar Progressive Trim + EGR + wide grid combinacions
- Periodes test: Feb 2026 (high-vol up), Mar 2026 (high-vol down), Oct 2025 (mid), Nov 2024 (low-vol)
