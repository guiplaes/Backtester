# 📊 BACKTEST REPORT — Estratègia Grid Manager Pionex

**Període**: 2025-05-18 → 2026-05-13 (12 mesos / 360 dies)
**Dades**: 2.073.600 barres M1 (Binance API)
**Capital simulat**: $1.000 inicial + 5% reserve USDT
**Cartera**: PAXG 40% · BTC 30% · ETH 20% · SOL 10%

---

## 🚨 EXECUTIVE SUMMARY

> **L'estratègia actual de trailing grid + rebalanceig 5% genera pèrdues consistents en bear market**. En el període 2025-05 a 2026-05 (bear cripto generalitzat), totes les 10 configuracions provades són **negatives** (entre −13% i −55%).
>
> Només **PAXG** (or) ha generat Grid Alpha NET positiu (+$33,73). Tots els bots cripto (BTC/ETH/SOL) tenen Grid Alpha NET negatiu.
>
> **La causa estructural**: el trailing reactiu en mercats lineals baixistes destrueix valor sistemàticament — cada recolocació "abandona" cells altes amb potencial de profit que mai es realitzaran.

---

## 📈 RESULTATS — Config producció actual (EDGE 10%, width segons bot, threshold 5%)

```
Capital inicial:      $1.000,00
Capital final:          $678,62
─────────────────────────────────
Total ROI:              -32,14%
Grid Alpha NET:         -$240,46  (-24,05%)
MTM unrealized:         -$143,93  (-14,39%)
─────────────────────────────────
Recolocacions:           1.973 (cost mig $0,032)
Rebalanceigs:               3
External deposits:          $0 (no van caldre)
Volum total operat:       $122
Fees totals:              $328 (33% del capital)
   - Per fill:            $265
   - Recolocacions:        $63
   - Rebalanceigs:          $0,10
```

### Per bot (12 mesos)

| Bot | Capital | Cycles | Fills | Grid Alpha | Fees | ROI |
|---|---:|---:|---:|---:|---:|---:|
| **PAXG_USDT** | $308 | 1.795 | 3.497 | **+$33,73** ✅ | $103 | -6,4% |
| BTC_USDT | $327 | 4.039 | 8.134 | -$81,35 ❌ | $95 | -38,3% |
| ETH_USDT | $234 | 5.723 | 11.422 | -$62,36 ❌ | $94 | -47,8% |
| SOL_USDT | $131 | 3.921 | 7.299 | -$67,69 ❌ | $35 | -48,8% |

**Notable**: PAXG fa **menys cycles** (1.795) que cripto però **GENERA PROFIT NET** del grid. Cripto fa 3-5× més cycles però amb menys profit per cycle, no compensa fees.

---

## 🔬 SENSITIVITY ANALYSIS (10 configs provades)

### Variable més impactant: **WIDTH del grid**

| Width × | Recolocacions | Grid Alpha | Veredicte |
|---:|---:|---:|---|
| 0,70 (més estret) | 3.790 | **-$459** | Pessim — recoloca contínuament |
| **1,00 (actual)** | 1.973 | -$238 | Baseline |
| 1,30 (més ample) | 1.164 | -$90 | Millora 62% |
| 1,60 (molt ample) | 800 | **-$60** | **Millor del lot** (encara negatiu) |

→ **Width 60% més ample reduiria pèrdues de -32% a -13%** en el mateix període.

### Variable secundària: **EDGE_TRIGGER_PCT**

| Edge | Recolocacions | Grid Alpha |
|---:|---:|---:|
| 5% | 1.529 | -$232 |
| **10% (actual)** | 1.973 | -$238 |
| 15% | 2.559 | -$268 |
| 20% | 3.319 | -$237 |

→ **Impacte petit** sobre el resultat. 5% lleugerament millor.

### Variable irrellevant en bear: **REBALANCE_THRESHOLD**

Threshold 3% / 5% / 8% / 12% dóna resultats molt similars perquè el rebalanceig dispara molt poques vegades (3 cops en 12 mesos).

---

## 🎯 INSIGHTS CRÍTICS

### 1) El grid trailing **destrueix valor en bear lineal**
Cada vegada que recolocem avall (cosa que passa constantment quan el preu cau):
- Cancel·lem les SELLs pendents a preus alts
- Comprem més base al preu actual (més baix)
- Si el preu segueix caient → repetim → comprem encara més baix
- **Mai venen els SELLs alts** que vam abandonar = profit perdut crònic

### 2) Els fees són letals: 33% del capital en 12 mesos
- $328 de fees per processar 30.352 fills
- Cost mig per fill: $0,011
- 5,5 recolocacions/dia entre 4 bots → cost recolocació rellevant ($63 acumulat)

### 3) PAXG (or) funciona perquè:
- Width estret (3,2%) optimitzat per la baixa volatilitat
- Or es comporta com refugi i fa moviments laterals
- Step gran ($50 a $4.600) → step × vol > fees per cycle

### 4) BTC/ETH/SOL fallen perquè:
- Bear lineal sostingut 12 mesos
- Width relativament estret (5-7%) → moltes recolocacions
- Step petit en relació al preu → cycle profit prop d'fee threshold

---

## 💡 RECOMANACIONS CONCRETES

### Curtterm (ja aplicables)

1. **Augmentar width_pct un 50-60% per als bots cripto**:
   - BTC: 0,0516 → **0,08**
   - ETH: 0,067 → **0,10**
   - SOL: 0,070 → **0,11**
   - Resultat esperat: pèrdues reduïdes ~60% en escenaris bear

2. **Mantenir PAXG amb width estret** (3,2% actual)
   - Sigui el cor del portfolio quan el mercat estigui lateral o baixant

3. **Considerar augmentar pes de PAXG al 50-60%**
   - L'únic bot estructuralment profitable en el període testat

### Mig termini

4. **Activar grids només en règim "ranged"**:
   - Detectar mercat lateral (low ADX, preu dins canal)
   - En bear lineal sostingut → desactivar grids cripto, mantenir només PAXG
   - Caldria un detector de règim (Hurst exponent, ADX, etc.)

5. **Asymmetric edge trigger** (suggerit anteriorment):
   - Trigger AVALL amb edge 5% (talla pèrdues ràpid)
   - Trigger AMUNT amb edge 25% (deixa córrer profits)
   - Reduiria les recolocacions "destructives" en bear

### Llarg termini

6. **Diversificar amb actius commodities/equity** (com vam discutir):
   - PAXG (or) + USOX (oil) + QQQX (Nasdaq) + 1-2 cripto
   - Cartera all-weather amb grids on cada actiu té el seu règim

7. **Re-validar amb període bull market**:
   - Aquest backtest cobreix bear 12 mesos
   - Validar la mateixa estratègia en 2021-2022 o 2023-2024 (bull markets)

---

## ⚠ CAVEATS

1. **Període específic = bear market lineal**: el resultat depèn fortament del període. En mercats laterals o bullish lents, el grid trailing pot ser molt rendible.

2. **Slippage real**: el simulator no modela slippage (assumeix preu del fill = preu de la cell). En realitat hi ha lleugera divergència.

3. **Order intra-minut**: M1 bars no diuen l'ordre exacte de high/low — el simulator usa heurística realista (up bar: O→L→H, down bar: O→H→L).

4. **Fee reserve no modelat**: Pionex pre-paga ~3% en fee reserve; al backtest les fees surten directament. Impacte cosmètic (~$30 sobre $1.000 inicial).

5. **Cells òrfens als extrems**: quan BUY a la última cell amunt, no es crea SELL pendent (rang esgotat). Inventari queda "stuck" fins la propera recolocació. Conservatiu — no infla profit.

---

## 📁 FITXERS GENERATS

```
backtest/
├── data/                            # 2,1M barres M1 (~200 MB)
│   ├── m1_PAXG_USDT.csv
│   ├── m1_BTC_USDT.csv
│   ├── m1_ETH_USDT.csv
│   └── m1_SOL_USDT.csv
├── results/
│   ├── full_12m_prod_config/        # Backtest principal
│   │   ├── summary.json
│   │   ├── equity_curve.csv
│   │   ├── recolocations.csv
│   │   ├── rebalances.csv
│   │   └── equity_plot.png
│   └── sensitivity/
│       └── sensitivity_report.json  # 10 configs comparades
└── REPORT.md                        # Aquest fitxer
```

---

## 🔬 VALIDACIÓ NECESSÀRIA

Abans d'aplicar canvis en producció, fer **micro-validació**:

1. Comparar últims 7 dies del backtest vs gridProfit real Pionex
2. Si discrepa > 20%, investigar el delta
3. Si quadra → confiar en les recomanacions

---

**Generat**: 2026-05-13
**Backtest engine**: custom Python, model Pionex fidel amb 7 fixes aplicats
**Validació pendent**: comparació última setmana vs producció real
