# 🏆 PLAN DE DEPLOYMENT — Sistema Mean-Rev Estables

## Resum executiu

**Compte VT Markets**: ~$63,000 USD
**Sistema**: Mean-Reversion Averaging amb 19 estratègies sobre 7 pairs estables
**Esperat**: +22-33%/any amb caiguda màxima 7-10%

---

## Pairs i estratègies (19 total)

### EURGBP (6 estratègies)
| TF | Direcció | SMA | Levels (σ) | Stop (σ) |
|---|---|---|---|---|
| D1 | LONG | 100 | -0.5/-1/-1.5/-2 | -4 |
| D1 | SHORT | 100 | -0.5/-1/-1.5/-2 | -4 |
| H4 | LONG | 200 | -0.5/-1/-1.5/-2 | -5 |
| H4 | SHORT | 200 | -0.5/-1/-1.5/-2 | -5 |
| H1 | LONG | 500 | -0.5/-1.5/-2.5/-3 | -3.5 |
| M15 | LONG | 2400 | -1/-1.5/-2/-2.5 | -4 |

### EURCHF (3 estratègies)
| TF | Direcció | SMA | Levels | Stop |
|---|---|---|---|---|
| D1 | LONG | 100 | -0.5/-1.5/-2.5/-3 | -5 |
| D1 | SHORT | 100 | -0.5/-1.5/-2.5/-3 | -5 |
| H4 | SHORT | 150 | -1.5/-2.5/-3.5/-4 | -6 |

### GBPCHF (2 estratègies)
| TF | Direcció | SMA | Levels | Stop |
|---|---|---|---|---|
| H4 | SHORT | 200 | -1/-2/-2.5/-3 | -4 |
| D1 | SHORT | 50 | -0.5/-1/-1.5/-2 | -4 |

### AUDCAD (5 estratègies — el rei mean-rev!)
| TF | Direcció | SMA | Levels | Stop |
|---|---|---|---|---|
| H4 | LONG | 50 | -0.5/-1/-1.5/-2 | -4 |
| H4 | LONG | 200 | -1/-1.5/-2/-2.5 | -4 |
| H1 | SHORT | 100 | -1/-1.5/-2/-2.5 | -5 |
| H1 | LONG | 150 | -1/-1.5/-2/-2.5 | -4 |
| M15 | LONG | 200 | -1/-1.5/-2/-2.5 | -5 |

### USDCAD (2 estratègies)
| TF | Direcció | SMA | Levels | Stop |
|---|---|---|---|---|
| H4 | LONG | 200 | -0.5/-1/-1.5/-2 | -3 |
| H4 | SHORT | 300 | -1/-1.5/-2/-2.5 | -5 |

### USDCHF (1 estratègia)
| TF | Direcció | SMA | Levels | Stop |
|---|---|---|---|---|
| H4 | SHORT | 1000 | -0.5/-1/-1.5/-2 | -3 |

---

## Configuració recomanada per el teu compte

### Conservador (recomanat començar)
- **Lot per entrada**: 0.05
- **Esperat**: +11%/any (~$7,000/any)
- **Caiguda màxima esperada**: -4.3% (~$2,700)
- **Pitjor dia**: $-3,499

### Moderat
- **Lot**: 0.10
- **Esperat**: +22%/any (~$14,000/any)
- **Caiguda**: -7.5% (~$4,700)
- **Pitjor dia**: $-7,000

### Sweet spot ⭐
- **Lot**: 0.15
- **Esperat**: +33%/any (~$21,000/any)
- **Caiguda**: -10.3% (~$6,500)
- **Calmar 3.27 (top tier)**

### Agressiu
- **Lot**: 0.20-0.30
- **Esperat**: +45-67%/any
- **Caiguda**: -12-16%

---

## Lògica del sistema (per cada estratègia)

```
PER CADA BAR TANCAT:
  1. Calcular SMA(N) i STD(N) sobre el pair+TF actual
  2. z-score = (close - SMA) / STD

  Si NO TINC posició oberta:
    Si direcció LONG i z <= level1 (ex: -1.0):
      → ENTRADA L1: comprar 1 unitat al close
    Si direcció SHORT i z >= +level1:
      → ENTRADA S1: vendre 1 unitat al close

  Si TINC posició LONG oberta amb N entries:
    Si z <= level(N+1) (next level, ex: -1.5 si tinc 1 entrada):
      → AFEGIR L(N+1): comprar 1 més
    Si close >= SMA:
      → TANCAR TOT al close (TARGET hit)
    Si z <= stop (ex: -4):
      → TANCAR TOT al close (STOP hit)

  (Mateix per SHORT mirror)
```

---

## Position sizing — exemples concrets

Compte $63k, lot 0.10:
- 19 estratègies, fins 4 entries/cada = 76 posicions màx teòriques
- Mark-to-market: pic 40 posicions simultànies real
- Exposició màxima: 40 × 0.10 lot = **4 lots totals**
- 4 lots EUR cross = ~$430k notional
- Margin requerit (50:1): **~$8,600** = 13.6% del compte
- **Capital lliure: $54k** (ben proporcionat)

---

## Implementació MT5 EA

### Opció A — EA Universal (recomanat)
1 EA aplicat a 19 charts diferents, cada chart amb el seu pair+TF
- Auto-detecció símbol+TF
- Aplica config del lookup table
- 1 instància per chart

### Opció B — Python Bridge (aprofitar Brain v3)
Reutilitzar `trader_brain.py` infraestructura
- Adaptar lògica DFMO → Mean-Rev
- 1 procés Python monitoritza 19 strategies
- Telegram alerts ja integrades

### Recomanat: Opció A (més robust, menys depèn de Python)

---

## Risk management

1. **Stop loss per estratègia individual**: ja al codi (-3 a -6 σ)
2. **Stop global compte**: si DD compte > 15%, atura totes les estratègies (manual o auto)
3. **News protection**: news_state.py pot bloquejar entries pre-news (opcional)
4. **Margin guard**: si margin used > 50%, no obrir noves entries

---

## Plan de deployment temporal

### Setmana 1-2: Paper trading
- Backtest a MT5 amb dades VT Markets reals (no Dukascopy)
- Validar que signals coincideixen amb Python backtest
- Forward test demo 1 setmana

### Setmana 3-4: Live mida petita (lot 0.025)
- 50% del lot recomanat
- Monitor 2 setmanes, validar comportament real

### Mes 2: Live mida normal (lot 0.05)
- Nivell conservador
- Monitor 1 mes

### Mes 3-6: Scale up segons resultats
- Si 3 mesos consecutius positius i DD < 5%, scale a lot 0.10
- 6 mesos consecutius positius: scale a lot 0.15

### Any 1+: Optimització periòdica
- Re-validar params cada 6 mesos amb dades noves
- Detectar si Hurst dels pairs ha canviat

---

## Archiu de la història del compte

- Backtest mostra: +33%/any sostingut 5 anys
- Compte mai sota -2% inicial (gestionable)
- 0/6 anys negatius
- Calmar 3.27 (top tier)

**Aquest sistema és real. Pot funcionar.**
