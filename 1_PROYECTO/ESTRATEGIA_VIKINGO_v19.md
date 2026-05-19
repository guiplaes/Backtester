# ESTRATEGIA VIKINGO — Document Tècnic v19
## Sistema de Trading XAUUSD amb Senyals de Telegram

---

## 1. ARQUITECTURA DEL SISTEMA

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   TELEGRAM       │────>│    PYTHON         │────>│    MT5 EA         │
│   Canal Vikingo  │     │  trading_app      │     │  ClaudeTradingBridge │
│   (senyals)      │<────│  (pont + UI)      │<────│  v8_MT5           │
└─────────────────┘     └──────────────────┘     └──────────────────┘
         │                      │                        │
    Senyals:              Funcions:                 Funcions:
    · BUY/SELL            · Detectar senyals        · Executar ordres MARKET
    · Cerramos            · Calcular lots            · Averaging proporcional (DFMO)
    · Movemos SL          · Escriure ordres JSON     · Trailing stops
    · Comentaris          · Supervisar (Claude CLI)  · Close via SL + trailing
                          · Alertes Telegram         · Heartbeat + positions.json
```

### Flux de comunicació
```
Python escriu  ──>  claude_orders.json   ──>  EA llegeix i executa
EA escriu      ──>  claude_positions.json ──>  Python llegeix estat
Python escriu  ──>  claude_heartbeat.json ──>  EA llegeix paràmetres
```

---

## 2. ENTRADA — Com s'obre una senyal

### Detecció de senyal (Python)
```
Missatge Telegram         Detecció              Tipus
─────────────────         ─────────             ─────
"SELL GOLD 5190"    ──>   \bSELL\b regex   ──>  SELL_SIGNAL
"COMPRA ORO"        ──>   \bBUY\b regex    ──>  BUY_SIGNAL
"cerramos todo"     ──>   CERRAMOS keyword ──>  CLOSE_SIGNAL
"movemos SL"        ──>   MOVEMOS SL       ──>  SL_ENTRY_SIGNAL
"chiringuito"       ──>   CHIRINGUITO      ──>  CLOSE_SIGNAL (Vikingo)
```

### Entrada MARKET (4 ordres simultànies)
```
┌─────────────────────────────────────────────────────────┐
│  SENYAL SELL DETECTADA                                   │
│                                                          │
│  lot_base = 0.12  (calculat per calculate_lot_size)      │
│  market_mult = 0.50  (Vikingo)                           │
│  lot_market = 0.12 × 0.50 = 0.06                        │
│                                                          │
│  Dividit en 4 ordres:                                    │
│  ┌────────┬────────┬────────┬────────┐                   │
│  │ MKT1   │ MKT2   │ MKT3   │ MKT4   │                  │
│  │ 0.02   │ 0.02   │ 0.01   │ 0.01   │ = 0.06 total     │
│  │ SELL   │ SELL   │ SELL   │ SELL   │                   │
│  │ TP=0   │ TP=0   │ TP=0   │ TP=0   │ (sense TP real)  │
│  └────────┴────────┴────────┴────────┘                   │
│                                                          │
│  Magic: 12345 (TG) | Totes sense SL ni TP               │
└─────────────────────────────────────────────────────────┘
```

### Proteccions anti-duplicat
- **Lock atòmic**: `_execute_lock` — un sol fil pot escriure ordres
- **Check posicions**: Si ja hi ha ≥4 posicions amb magic=12345 → BLOQUEJAT
- **Cooldown 5s**: Entre crides a `execute_immediate_order()`
- **_close_all_pending**: No obrir si CLOSE_ALL en curs (<3s)

---

## 3. CÀLCUL DE LOTS — Fórmula v18

### Paràmetres Vikingo
```
┌──────────────────────────────────────────┐
│  VIKINGO (canal de rangs curts)           │
│                                           │
│  Rang màxim:       $100                   │
│  ML_RATIO:         50 (target ML=5000%)   │
│  market_mult:      0.50                   │
│  avg_mult:         5.75                   │
│  SUMA_MULT:        6.25                   │
│  Leverage:         1:500                  │
└──────────────────────────────────────────┘
```

### Fórmula
```
MPL = (100 × gold_price) / 500          ← marge per lot

denominador = ML_RATIO × SUMA_MULT × MPL
            + 100 × avg_range × market_mult
            + 50  × avg_range × avg_mult

lot_base = equity / denominador
```

### Exemple amb equity $64,442 i gold $5,150
```
MPL = (100 × 5150) / 500 = 1,030

denominador = 50 × 6.25 × 1030       = 321,875
            + 100 × 100 × 0.50       =   5,000
            + 50 × 100 × 5.75        =  28,750
            ─────────────────────────────────────
            TOTAL                     = 355,625

lot_base = 64,442 / 355,625 = 0.18

lot_market  = 0.18 × 0.50 = 0.09  (4 ordres MARKET)
lot_per_$   = (0.18 × 5.75) / 100 = 0.01035  (lot per $ d'averaging)
```

### Distribució de lots en averaging
```
Preu en contra ($)    lot_per_dollar     Lot acumulat
──────────────────    ──────────────     ────────────
  $0  (entrada)          —              0.09 (MARKET)
 $10                  0.01035/dollar     0.10
 $20                  0.01035/dollar     0.21
 $30                  0.01035/dollar     0.31
 $40                  0.01035/dollar     0.41
 $50                  0.01035/dollar     0.52
 $60                  0.01035/dollar     0.62
 $70                  0.01035/dollar     0.72
 $80                  0.01035/dollar     0.83
 $90                  0.01035/dollar     0.93
$100 (RANG MÀX)       0.01035/dollar     1.04
                                         ─────
                              TOTAL:     1.13 lots
                              (= lot_base × SUMA_MULT = 0.18 × 6.25)
```

---

## 4. AVERAGING PROPORCIONAL — Com promediem

### Concepte
```
L'EA calcula contínuament:

  targetLot = distància_adversa × lot_per_dollar

Si targetLot > (lot_ja_obert + lot_manual) → obre la diferència

  needed = targetLot - avgOpenLotCache - manualLot
```

### Filtre DFMO (Dual Frame Momentum Oscillator)
```
Indicador: Slow Stochastic (25,4,4) + Fast RSI (3) — M1

  ┌──────────────────────────────────────────────────┐
  │  ZONA OB (>80): StochK > 80 AND RSI > 80        │
  │  ZONA OS (<20): StochK < 20 AND RSI < 20        │
  │                                                   │
  │  Senyals:                                         │
  │   +1 = OB descendent (exhaustion compra)   ✓ AVG │
  │   -1 = OS ascendent  (exhaustion venda)    ✓ AVG │
  │   +3 = OB accelerant (momentum pujant)     ✗ BLK │
  │   -3 = OS accelerant (momentum baixant)    ✗ BLK │
  │    0 = neutral                              ✗ —  │
  └──────────────────────────────────────────────────┘
```

### Lògica d'obertura
```
  Senyal SELL activa, preu puja (en contra):

  Preu ───────────────────────────────────────────>
  5150  5155  5160  5165  5170  5175  5180
  │     │     │     │     │     │     │
  ENTRY       $10   $15   $20   $25   $30
              │           │           │
              ▼           ▼           ▼
         need=0.10   need=0.21   need=0.31
              │           │           │
        DFMO check   DFMO check  DFMO check
              │           │           │
          ✗ BLK       ✓ open      ✓ open
          (K puja)    0.21 lot    0.10 lot
                    (tot needed)  (diferència)

  Quan DFMO confirma → obre TOT el lot pendent d'una vegada
  Cooldown: 1 execució per barra M1
  Màxim: 40 posicions d'averaging (AVG_MAX_POSITIONS)
```

### Per què DFMO?
```
  ┌─ SENSE filtre ──────────────────────────────────────┐
  │  Preu puja $30 en línia recta                        │
  │  → 3 ordres AVG a $10, $20, $30                      │
  │  → Totes en pèrdua creixent                          │
  │  → Si continua $50 més → DESASTRE                    │
  └──────────────────────────────────────────────────────┘

  ┌─ AMB filtre DFMO ───────────────────────────────────┐
  │  Preu puja $30 en línia recta                        │
  │  → DFMO en zona OB accelerant (+3) → BLOQUEJAT      │
  │  → Preu arriba a $50 → StochK comença a baixar      │
  │  → DFMO retorna +1 (exhaustion) → OBRE tot: 0.52 lot│
  │  → Entra al PIC, no durant l'explosió                │
  └──────────────────────────────────────────────────────┘
```

---

## 5. DRAWDOWN CALCULAT — Worst Case

### Escenari: Preu va $100 en contra (rang màxim Vikingo)
```
  Equity: $64,442 | lot_base: 0.18 | Gold: $5,150

  ┌────────────────────────────────────────────────────────┐
  │  POSICIÓ          LOT      DIST    PÈRDUA/LOT  PÈRDUA │
  │  ──────────────   ─────    ─────   ──────────  ────── │
  │  4× MARKET        0.09     $100    $10,000     $900   │
  │  AVG $10          ~0.10    $90     $9,000      $900   │
  │  AVG $20          ~0.10    $80     $8,000      $800   │
  │  AVG $30          ~0.10    $70     $7,000      $700   │
  │  AVG $40          ~0.10    $60     $6,000      $600   │
  │  AVG $50          ~0.10    $50     $5,000      $500   │
  │  AVG $60          ~0.10    $40     $4,000      $400   │
  │  AVG $70          ~0.10    $30     $3,000      $300   │
  │  AVG $80          ~0.10    $20     $2,000      $200   │
  │  AVG $90          ~0.10    $10     $1,000      $100   │
  │  AVG $100         ~0.10    $0      $0          $0     │
  │  ─────────────────────────────────────────────────────│
  │  TOTAL:           ~1.13 lots       DD TOTAL:  $5,400  │
  │                                                        │
  │  DD % = $5,400 / $64,442 = ~8.4%                      │
  │  Margin Level ≈ 4,640%                                 │
  │  Free Margin ≈ 89%                                     │
  └────────────────────────────────────────────────────────┘
```

### Target DD per canal
```
  ┌──────────────────────────────────────────┐
  │  Canal        Rang    ML    DD target    │
  │  ────────     ─────   ───   ──────────   │
  │  Vikingo      $100    50    ~8-9%        │
  │  TrueTrading  $125    50    ~9-10%       │
  └──────────────────────────────────────────┘
```

---

## 6. GESTIÓ DE TANCAMENTS

### A) "Cerramos todo" — CLOSE_ALL via SL
```
┌─────────────────────────────────────────────────────────────────┐
│  PAS 1: Python detecta "cerramos" → escriu CLOSE_ALL al JSON   │
│                                                                  │
│  PAS 2: EA llegeix → SetCloseAllSL() (ASYNC, simultani)         │
│                                                                  │
│  Per cada posició oberta:                                        │
│    SELL, Ask=5145 → SL = Ask + $0.50 = 5145.50                  │
│    BUY,  Bid=5145 → SL = Bid - $0.50 = 5144.50                  │
│                                                                  │
│  PAS 3: ManageCloseAllTrailing() — cada tick (ASYNC)             │
│                                                                  │
│    Si preu millora ≥ $1 → trailing 70% captura                   │
│                                                                  │
│    Tick 1: Ask=5144.50 → millora=$0.50 < $1 → SL quiet          │
│    Tick 2: Ask=5143.80 → millora=$1.20 ≥ $1 → trail!            │
│            newSL = 5145.50 - (1.20 × 0.70) = 5144.66            │
│    Tick 3: Ask=5142.00 → millora=$3.00                           │
│            newSL = 5145.50 - (3.00 × 0.70) = 5143.40            │
│    Tick 4: Ask puja a 5143.41 → SL tocat → BROKER TANCA!        │
│                                                                  │
│  Resultat: Posicions tancades automàticament pel broker          │
│  Temps: ~200-500ms per posar SLs, broker tanca quasi instant     │
└─────────────────────────────────────────────────────────────────┘
```

### Escenaris de tancament
```
  ┌───────────────────────────────────────────────────────┐
  │  Escenari                  Què passa                   │
  │  ─────────────────────     ──────────────────────────  │
  │  Preu no es mou           SL $0.50 → broker tanca     │
  │                            quasi instantàniament        │
  │                                                        │
  │  Preu va en contra         SL $0.50 → broker tanca     │
  │                            instantàniament              │
  │                                                        │
  │  Preu millora < $1         SL quiet a $0.50, tanca     │
  │                            al primer retrocés           │
  │                                                        │
  │  Preu millora ≥ $1         Trailing 70%, aprofitem     │
  │                            part de la millora           │
  │                                                        │
  │  Timeout (fallback)        CloseAllPositions() seqüenc.│
  └───────────────────────────────────────────────────────┘
```

### B) "Movemos SL" — Breakeven
```
┌──────────────────────────────────────────────────────┐
│  1. Python detecta "movemos SL" al canal              │
│  2. Escriu {"action": "MOVE_SL_ENTRY"} al JSON        │
│  3. EA posa SL a preu d'entrada + 2 punts (ASYNC)     │
│  4. Python marca breakeven_set = true                  │
│                                                        │
│  Efecte:                                               │
│  · Si preu retrocedeix → SL a entrada → pèrdua ≈ $0   │
│  · Si totes tanquen per SL → senyal desactivada        │
│  · NO reentrada després de breakeven                   │
└──────────────────────────────────────────────────────┘
```

---

## 7. TRAILING STOPS — Posicions MARKET

### Trailing de les 4 ordres inicials
```
┌──────────────────────────────────────────────────────┐
│  Paràmetres:                                          │
│  · Threshold: +$10 profit per activar                 │
│  · Retrace:   20% del pic de profit                   │
│  · Màxim:     2 posicions seguides                    │
│                                                        │
│  Exemple SELL, entrada $5180:                          │
│                                                        │
│  Preu baixa a $5170 → profit $10 → TRAILING ACTIVAT   │
│                                                        │
│  Preu baixa a $5160 → profit $20                       │
│    SL = 5180 - (20 × 0.80) = 5164                      │
│    (protegeix 80% del profit = $16)                    │
│                                                        │
│  Preu baixa a $5150 → profit $30                       │
│    SL = 5180 - (30 × 0.80) = 5156                      │
│    (protegeix $24)                                     │
│                                                        │
│  Preu retrocedeix a $5156 → SL tocat → tanca a +$24   │
└──────────────────────────────────────────────────────┘
```

---

## 8. AUTO-RESET — Mecanisme de seguretat

### Condicions d'activació
```
┌──────────────────────────────────────────────────────┐
│  1. max_adverse_reached ≥ $40                         │
│     (el preu ha anat $40+ en contra en algun moment)  │
│                                                        │
│  2. Preu torna a BE + $2                               │
│     (breakeven + marge de seguretat)                   │
│                                                        │
│  3. Trading enabled                                    │
│  4. No CLOSE_ALL en curs                               │
└──────────────────────────────────────────────────────┘
```

### Flux RESET_SL
```
  Preu ($)
  5220 ┤                    ╱╲
  5210 ┤                 ╱╱    ╲╲
  5200 ┤              ╱╱         ╲ ← max_adverse $40
  5190 ┤           ╱╱              ╲
  5180 ┤ ENTRY ──╱╱                  ╲
  5170 ┤                                ╲
  5160 ┤                                  ╲──── BE (avg entry)
  5158 ┤                                     ╲── BE + $2
       │                                        │
       │                              AUTO-RESET AQUÍ!
       │
  Què fa RESET_SL:
  ┌─────────────────────────────────────────────────────┐
  │  1. SL al breakeven per TOTES les posicions velles   │
  │     (EA: SetCloseAllSL amb bePrice)                  │
  │                                                      │
  │  2. Trailing 70% persegueix les velles               │
  │     (si preu millora → SL es mou a favor)            │
  │                                                      │
  │  3. SIMULTÀNIAMENT: 4× MARKET noves amb lot × 2      │
  │     (aprofitar que preu ha tornat a zona favorable)   │
  │                                                      │
  │  4. Noves posicions ja operen normalment              │
  │     (averaging proporcional disponible)               │
  │                                                      │
  │  Resultat: Velles es tanquen ~BE, noves continuen    │
  │  Benefici: Lot base × 2 → aprofitar el moviment     │
  └─────────────────────────────────────────────────────┘
```

---

## 9. SUPERVISOR CLAUDE CLI — Xarxa de seguretat

### Com funciona
```
  Cada event → Python envia a Claude CLI (model Sonnet)
  Claude CLI analitza i respon JSON:

  ┌──────────────────────────────────────────────────┐
  │  Event                    Resposta habitual       │
  │  ──────────────           ────────────────────    │
  │  BUY/SELL signal          NO_ACTION (validar)     │
  │  Canal info               NO_ACTION               │
  │  "cerramos todo"          CLOSE_ALL (seguretat)   │
  │  "movemos SL"             NO_ACTION (Python fa)   │
  │  Canvi de direcció        CLOSE_ALL               │
  │  Missatge NO senyal       CLOSE_ALL (si obert)    │
  └──────────────────────────────────────────────────┘

  80% → NO_ACTION
  20% → CLOSE_ALL (com a xarxa de seguretat)
```

---

## 10. CICLE COMPLET — Exemple real

```
  TIMELINE D'UNA SENYAL VIKINGO
  ═══════════════════════════════════════════════════════════

  14:30  Canal: "SELL GOLD 5180"
         ├─ Python detecta SELL_SIGNAL
         ├─ calculate_lot_size() → lot_base=0.18, lot_market=0.09
         ├─ 4× MARKET SELL: MKT1(0.03) MKT2(0.02) MKT3(0.02) MKT4(0.02)
         ├─ Claude CLI: "Senyal vàlida" → NO_ACTION
         └─ Heartbeat: entry=5180, dir=SELL, lot_per_$=0.01035, range=$100

  14:45  Preu puja a $5190 (+$10 en contra)
         ├─ EA: targetLot = 10 × 0.01035 = 0.10
         ├─ DFMO: StochK=85, RSI=82 → OB accelerant (+3) → BLOQUEJAT
         └─ (No s'obre averaging, momentum massa fort)

  15:10  Preu puja a $5210 (+$30 en contra)
         ├─ EA: targetLot = 30 × 0.01035 = 0.31
         ├─ DFMO: StochK=78 (baixant de 92) → OB descendent (+1) → CONFIRMAT!
         ├─ needed = 0.31 - 0 = 0.31
         └─ Obre 2× AVG SELL: 0.16 + 0.15 = 0.31 lot

  15:30  Preu puja a $5220 (+$40), max_adverse = $40
         ├─ EA: targetLot = 40 × 0.01035 = 0.41
         ├─ DFMO neutral → no obre (needed acumulant-se)
         └─ Python: max_adverse_reached = $40 ✓

  16:00  Preu baixa a $5195 (tornant a zona d'entrada)
         ├─ avg_entry ≈ $5193, preu a $5195
         ├─ advance = 5195 - 5193 = $2 ≥ BE_ADVANCE_REQUIRED ✓
         ├─ AUTO-RESET ACTIVAT!
         │  ├─ SL al BE ($5193) per velles posicions (ASYNC)
         │  ├─ Trailing 70% persegueix
         │  └─ 4× MARKET SELL noves: lot × 2 = 0.18 lot
         └─ Velles es tanquen ~$5193, noves continuent

  16:30  Canal: "cerramos todo"
         ├─ Python: CLOSE_SIGNAL detectat
         ├─ deactivate_signal()
         ├─ execute_close_all() → CLOSE_ALL al JSON
         ├─ EA: SetCloseAllSL() → SL a $0.50 (ASYNC, simultani)
         ├─ Broker tanca totes en ~200ms
         └─ Claude CLI: verifica 0 posicions → NO_ACTION

  ═══════════════════════════════════════════════════════════
```

---

## 11. AVANTATGES MT5 vs MT4

```
  ┌────────────────────────────────────────────────────────┐
  │  Operació              MT4            MT5               │
  │  ────────────────      ─────────      ──────────────   │
  │  Posar SL (9 pos)     ~900ms (seq)   ~5ms (ASYNC)     │
  │  Trailing (9 pos)     ~900ms (seq)   ~5ms (ASYNC)     │
  │  Close All (9 pos)    ~900ms (seq)   ~5ms (ASYNC)     │
  │  Breakeven (9 pos)    ~900ms (seq)   ~5ms (ASYNC)     │
  │  Modify TP (9 pos)    ~900ms (seq)   ~5ms (ASYNC)     │
  │  Entre ordres          200ms          50ms              │
  │  ────────────────────────────────────────────────────  │
  │  TOTAL CLOSE_ALL      ~2-4 segons    ~200ms            │
  └────────────────────────────────────────────────────────┘

  En mercats ràpids (NFP, FOMC), 2-4 segons = $5-20 de slippage.
  Amb MT5 ASYNC, tancament quasi instantani.
```

---

## 12. RESUM VISUAL — Tot en un cop d'ull

```
  PREU ($)
     ▲
5220 │                    ╱╲ ← DFMO bloqueja (momentum fort)
     │                 ╱╱    ╲
5210 │              ╱╱    ●    ╲ ← DFMO confirma: AVG SELL 0.31 lot
     │           ╱╱              ╲
5200 │        ╱╱                   ╲         max_adverse=$40
     │     ╱╱                        ╲
5190 │──╱╱── ENTRY SELL 0.09 lot       ╲
     │  ▲                                ╲
5180 │  │ 4× MKT                           ╲──── avg_entry ~$5193
     │                                         ╲
5170 │                                     AUTO─RESET (SL al BE + 4× noves × 2)
     │                                            │
5160 │                                             ╲
     │                                               ╲── "cerramos" → SL $0.50
5150 │                                                 ╲  broker tanca tot
     │                                                    ▼ TANCAT
     └──────────────────────────────────────────────────────> TEMPS

  LOTS ACUMULATS:
  ═══════════════
  $0:   0.09 (MARKET ×4)
  $10:  +0.10 (si DFMO confirma)
  $20:  +0.10
  $30:  +0.10 ← DFMO confirma aquí, obre 0.31 d'un cop
  $40:  +0.10
  ...
  $100: TOTAL ~1.13 lots (SUMA_MULT × lot_base)

  DD WORST CASE: ~8.4% ($5,400 / $64,442)
  MARGIN LEVEL:  ~4,640%
  FREE MARGIN:   ~89%
```

---

*Document generat el 2026-03-09 | Sistema v19 | Canal Vikingo*
*EA: ClaudeTradingBridge_v8_MT5 | Python: trading_app_integrated.py*
