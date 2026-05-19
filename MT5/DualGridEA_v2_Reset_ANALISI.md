# Dual-Grid Reset EA — Anàlisi i Especificació

> **Data**: 2026-05-14
> **Objectiu**: Replicar l'estratègia "Grid XAU V10" d'un tercer en un EA propi de MT5 per provar-la en VTMarkets (XAUUSD, possiblement BTCUSD més endavant).
> **Estat**: Anàlisi completa. Pendents respostes del creador a 4 preguntes clau abans de codificar.

---

## 1. Context

Un trader conegut ens va passar:
- Una **spec textual** del seu sistema (basada en una versió més antiga/simplificada)
- **Dos screenshots** del bot "Grid XAU V10" funcionant en real (XAUUSD-VIP, VTMarkets, balance ~123k$)
- **Diversos missatges descrivint el comportament** del seu EA

Volem **replicar l'estratègia** en un EA propi (`DualGridEA_v2_Reset.mq5`) per provar-la en paral·lel.

**No tenim accés al codi font** del v10. Tota la informació prové d'observació + descripcions del creador.

---

## 2. El concepte central

**Doble grid bidireccional** en XAUUSD:
- Un grid **LONG** (BUY LIMITs sota d'un centre)
- Un grid **SHORT** (SELL LIMITs sobre d'un centre)
- Centres inicialment iguals al preu d'arrencada
- **Sense Stop Loss** — només liquidació del broker

**Mecànica de reset UNILATERAL** (clau de l'estratègia):
- Quan l'equity total arriba a +1% sobre el balance d'arrencada
- I un dels dos costats (long o short) està en flotant negatiu
- **Es tanca només aquell costat** i es recolloca amb el centre al preu actual
- El costat positiu segueix intacte

**Resultat amb el temps**: els dos centres es separen progressivament, creant un "sandwich" entre LONG_center i SHORT_center. Quan el preu queda entremig, **els dos costats poden estar en profit alhora**.

---

## 3. Citacions literals del creador (font primària)

### 3.1 Sobre el sistema general

> "es un grid doble en long y short a la vez y se resetea según algunas condiciones. Ronda el 2% diario que no va nada mal"

> "es muy facil, solo pones el lotaje por cada lado (por defecto 0.01) y el rango y número de grids, parecido a los de cripto"

> "El resto lo hace el EA y reposiciona cuando el equity es mayor al x% (tb configurable) al balance en el arranque del bot y el margen ha bajado de X % para que no tenga limites de margen"

> "es una fumada que se me ocurrió, y es que encima vtmarkets ha creado cuenta cent para btc tb, así que podría probarlo tb con btc"

### 3.2 Sobre el reset

> "no tiene SL, solo tiene liquidación. elijo un rango relativamente amplio y se resetea en el caso de que long o short se va a negativo y siempre que el profit supere el 1%"

> "he puesto un sistema para el reseteo: lo que hace el grid es aguantar siempre la mejor posición, así cuando se resetea, si tiene posiciones abiertas muy en contra las cierra y las vuelve a abrir en mejor posición. **Solo resetea el lado que esté en negativo, si la posición está en positivo sigue funcionando sin mas**. Y tiene en cuenta el equity respecto al balance de la cuenta en el arranque"

> "Se resetea cada vez que llega al 1% de profit y si hay algún sentido en negativo cierra ese sentido y abre en mejor posición. Si los 2 sentidos están en positivo sigue operando sin mas hasta que uno de los 2 se quede en negativo"

### 3.3 Sobre el funcionament del grid

> "me he basado en los grid de cripto, cuando se abre un grid se abren todas las posiciones necesarias desde arriba hasta el precio actual (en el caso de long) y a medida que el precio sube las va cerrando obteniendo profit. Pero si se va hacia abajo, las posiciones iniciales se quedan con pérdida"

> "Si el bot se resetea más abajo (porque la suma de todos los profits hasta el momento ya superan el 1% de profit del balance respecto a la apertura del bot) lo que hace es cerrar todas las posiciones de arriba y las abre en ese punto teniendo todas las posiciones en un mejor punto de entrada"

> "cierra en pérdidas, pero siempre que el profit que haya ganado rascando supere el balance inicial"

### 3.4 Sobre la imatge del bot en sandwich (positiu)

> "Mira como va el grid doble, se ha reseteado en algunos sentidos y ahora tanto el lado long como el lado short están en positivos. Es algo raro pero el tema es que mira lo que lleva sacado desde ayer. Cada lado se ha reposicionado mejor, por eso puede operar ahora en ambos sentidos a la vez teniendo una posición global mucho mas positiva"

> "La flecha roja de arriba indica el precio donde se abrió la parte short, como está en positivo no la cierra y sigue operando. En cambio la parte long puedes ver en la flecha de abajo que se reseteó y tiene todas las operaciones abiertas justo en ese punto. Al arrancar la parte long tb abrió justo donde la parte short, pero al resetear esta parte long reposiciona todo en un mejor punto y por eso aparece abajo el grueso de operaciones"

---

## 4. Anàlisi forense dels screenshots

### 4.1 Screenshot 1 — Estat POSITIU (sandwich funcionant)

**Data al chart**: 13 May 11:00 → 14 May 16:00 (~30 hores)

**Panel del bot**:
- **LONG**: 116 posicions, flotant **+1660.23$**, BE 4671.71, estat ACTIVO
- **SHORT**: 186 posicions, flotant **+2228.33$**, BE 4697.66, estat ACTIVO
- Balance inicial: **123621.56$**
- Nivel de margen: **19565.4%**
- Equity vs inicio: **+4.10%**
- Spread al moment: 36 punts

**Càlcul del preu actual del moment** (verificació matemàtica):
- LONG: avg P/L per posició = 1660/116 = +14.31$ → preu = BE + 14.31 = **4686.02**
- SHORT: avg P/L per posició = 2228/186 = +11.98$ → preu = BE - 11.98 = **4685.68**
- **Preu real**: ~4685.85 (coincideix amb el marker del chart 4685.00 + spread 36)

**Inferència dels centres dels dos grids** (amb step ~0.5$ entre nivells):
- LONG_center ≈ **4701** (estimat — el grid LONG es va resetejar quan el preu era ~4701)
- SHORT_center ≈ **4651** (estimat — el grid SHORT es va resetejar quan el preu era ~4651)
- **Diferència de 50$ entre els dos centres** = sandwich actiu

**Interpretació**: el bot porta dies operant. El grid LONG s'ha resetejat almenys una vegada (probablement més) durant períodes en què el preu va baixar, recolocant el centre cada cop a un preu més baix. El SHORT mateix però en sentit invers. Ara el preu (4685) cau entre els dos centres → tots dos costats en profit.

### 4.2 Screenshot 2 — Estat NEGATIU (failure mode PROT.BE)

**Data al chart**: 11 May 11:00 → 12 May 16:00 (~30 hores, abans del primer screenshot)

**Panel del bot**:
- **LONG**: 127 posicions, flotant **-4123.61$**, BE 4708.30, estat **PROT. BE**
- **SHORT**: 178 posicions, flotant **-3312.66$**, BE 4656.86, estat **PROT. BE**
- Balance inicial: **125682.41$** (lleugerament diferent del screenshot 1, indica que hi va haver activitat entre les dues captures)
- Nivel de margen: **26043.4%**
- Equity vs inicio: **-1.57%**

**Càlcul del preu actual del moment**:
- LONG: avg P/L per posició = -4123/127 = -32.46$ → preu = BE - 32.46 = **4675.84**
- SHORT: avg P/L per posició = -3312/178 = -18.61$ → preu = BE + 18.61 = **4675.47**
- Preu real: **~4675.5** (coincideix amb marker 4675.00)

**Inferència dels centres**:
- LONG_center ≈ **4748** (els LONGs es van obrir quan el preu era prop de 4748, després va baixar fins 4675)
- SHORT_center ≈ **4601** (els SHORTs es van obrir quan el preu era prop de 4601, després va pujar fins 4675)

**Interpretació**: **failure mode**. El preu ha fet un trend baixista (de ~4750 a 4675 en 30h) que ha atrapat els LONGs en flotant negatiu. Però els SHORTs també estan en negatiu perquè es van obrir a preus encara més baixos en un swing anterior. **Els dos costats en pèrdua alhora**.

L'equity vs inici és **-1.57%** (per sota del threshold +1% per resetejar). Per tant **el bot està bloquejat en estat PROT.BE** — no pot resetejar cap costat perquè no té cushion suficient. Espera que el preu reverteixi o que els altres costats compensin.

### 4.3 Confirmació visual del sandwich (insight del creador)

Al screenshot 1, el creador va dibuixar **dues fletxes vermelles**:
- **Fletxa superior** → apunta a ~4697 (= SHORT BE) — "donde se abrió la parte short"
- **Fletxa inferior** → apunta a ~4671 (= LONG BE) — "donde se reseteó la parte long"

**Aquesta és la prova visual del sandwich**: el cluster de SHORTs està concentrat a la part superior del chart i el cluster de LONGs a la part inferior. Cada cluster correspon al centre del seu grid respectiu.

### 4.4 Pattern de les línies blaves (revelador sobre TPs)

Les **línies blaves diagonals** del chart **convergeixen a punts específics**, no estan escampades pel chart. Això és el patró típic d'un **tancament massiu** (un reset que tanca 100+ posicions alhora → 100+ línies convergint al mateix punt).

Si hi haguessin TPs individuals per posició, les línies acabarien escampades per tot el chart. **No és el cas**. Per tant:

> **El v10 NO té TPs per posició**. Les posicions només es tanquen en els events de reset massiu.

Aquesta és una conclusió IMPORTANT (vam canviar d'opinió durant l'anàlisi).

---

## 5. Estats coneguts del bot

| Estat | Quan apareix | Comportament |
|---|---|---|
| **ACTIVO** | Operant normalment | Pendents disparant-se, posicions obertes, sense reset actiu |
| **PROT. BE** | Flotant del costat < 0 però equity < +1% | Bloquejat — no pot resetejar perquè no té cushion. Espera reversal. |

**Pregunta oberta**: en PROT.BE, el bot segueix deixant que es disparin els pendents existents (acumulant més posicions) o queda **totalment paralitzat**?

---

## 6. Què tenim SEGUR

1. **Doble grid LONG + SHORT** amb dos centres separats
2. **Inici simètric**: `long_center = short_center = preu d'arrencada del bot`
3. **Reset UNILATERAL**: només es reseteja el costat negatiu, l'altre intacte
4. **Trigger del reset**: `equity ≥ start_balance × 1.01` AND `flotant_costat < 0`
5. **`start_balance`**: fixat al moment d'arrencar el bot (confirmar si s'actualitza amb cada reset, pregunta 3)
6. **Acció del reset**: tanca **totes** les posicions d'aquell costat + recolloca el grid amb centre = preu actual
7. **NO SL** — només liquidació del broker (margin call)
8. **Inputs configurables**: lot per costat (default 0.01), range (% sobre preu actual), número de grids (nivells)
9. **NO hi ha TPs per posició** — totes les posicions es mantenen obertes fins al reset
10. **Inspiració**: grids de criptomonedes (Pionex, etc.)
11. **Visualment**: dos clusters de posicions als "extremes" = els dos centres separats

---

## 7. Què tenim INFERIT amb alta confiança

| Element | Inferència | Base |
|---|---|---|
| Espaiat entre nivells | ~0.5$ | Labels visibles "BUY 0.01 at 4593.30, 4592.85, 4592.15..." |
| Nivells per costat | 100-200 | Range 2% × 4685 = 93$, dividit per 0.5$ = ~180 nivells |
| Range del grid | ~1-2% | Coherent amb espaiat × nº nivells |
| Tipus d'ordre | BUY LIMIT sota / SELL LIMIT sobre | Mean-reversion estil cripto |
| Magic number | Configurable | Estandard MT5 |

---

## 8. Preguntes OBERTES al creador (pendents de resposta)

Les 4 preguntes que se li han enviat:

1. **Les posicions que es van obrint tenen TP propi o només es tanquen totes al reset?** *(esperem confirmar que NO hi ha TP per posició)*

2. **Quan es dispara un pendent, vols posar-ne un altre al mateix nivell o el deixes buit fins al reset?** *(determina si el grid és "auto-replenishing" o "consumptiu")*

3. **El 1% de profit es compta contra el balance d'arrencada (fix) o s'actualitza amb cada reset?** *(crítica per la dinàmica de resets recurrents)*

4. **Si el preu surt del rang del grid, què fa l'EA? Es queda quiet o estira el rang?** *(edge case important)*

---

## 9. Algoritme reconstruït (model actual, pre-respostes)

```python
OnInit:
  start_balance = AccountBalance()        # FIX, no s'actualitza
  long_center = current_price
  short_center = current_price
  place_grid_LONG(long_center)            # BUY LIMITs sota
  place_grid_SHORT(short_center)          # SELL LIMITs sobre

OnTick:
  long_flotant = sum(P/L de LONGs oberts)
  short_flotant = sum(P/L de SHORTs oberts)
  equity = balance + long_flotant + short_flotant
  
  # Trigger del reset
  if equity >= start_balance * 1.01:
    if long_flotant < 0:
      close_all_LONGs()                   # MASSIVE CLOSE — realitza pèrdua
      long_center = current_price         # Recentrar al preu actual
      place_grid_LONG(long_center)        # Nou grid fresc
    
    if short_flotant < 0:
      close_all_SHORTs()
      short_center = current_price
      place_grid_SHORT(short_center)
  
  # Kill switch hard (afegit per seguretat, NO al v10 original)
  if equity < start_balance * (1 - MaxDrawdownPct/100):
    close_all()
    cancel_all_pendings()
    EA_state = KILLED
```

**Sense**: TPs per posició, repoblament de pendents (PENDENT confirmació), trailing target, news filter.

---

## 10. Anàlisi de rendibilitat i risc

### 10.1 Pot ser rendible? **Sí, en certs règims**

Math favorable per XAU:
- ATR diari ~50-80$
- Volatilitat alta amb mean-reversion freqüent
- Cada reset captura ≥1% de profit
- 1-3 resets/dia × 1% = 1-3% diari **factible**

### 10.2 És consistent? **NO. Té tail risk seriós**

**El "1%" cushion és fràgil**:
- Bot recent → poc cushion → vulnerable
- Sense SL → en trend fort, pèrdues il·limitades fins margin call
- Failure mode visible al screenshot 2: tots dos costats en negatiu + equity sota threshold = bloqueig

**Worst case calculat** (sobre balance 123k):
- 100 LONGs × 0.01 lot × 1$/punt = $100/punt de moviment advers
- Si XAU baixa 100$ sense TPs (no n'hi ha) ni resets (no possibles): pèrdua flotant = -$10,000 = -8.1%
- Si baixa 200$: -$20,000 = -16%
- Si baixa 300$: -$30,000 = -24% → s'apropa a margin call

**Moviments reals XAU recents**:
- Abril-Octubre 2024: 2280 → 2790 (+22%) en mesos
- Octubre-Novembre 2024: 2790 → 2540 (-9%) en 2 setmanes
- Febrer-Abril 2025: 2800 → 3450 (+23%) en 8 setmanes
- **Cap d'aquests moviments hauria estat sobreviscut sense un cushion molt gran**

### 10.3 Probabilitats estimatives

| Esdeveniment | Probabilitat (1 any) |
|---|---|
| Fer >30% anual en règim normal | 60-70% |
| Drawdown >40% en algun moment | 25-35% |
| Liquidació total per trend extrem | 5-15% |

### 10.4 Recomanacions per fer-ho més robust

Modificacions de seguretat **que el v10 NO té** però jo afegiria:

1. **Cushion més ample**: 3-5% en lloc d'1% (menys resets però molt més marge)
2. **Hard kill switch a -15% / -20% equity** (tancament defensiu — NO "només liquidació")
3. **Detector de trend fort**: pausar obertura de nous pendents si ATR(H1) > X o si preu fora del range Y temps
4. **Cooldown post-reset**: no obrir grid fresc immediatament després del reset (evitar overfilling en trends)

---

## 11. Paràmetres recomanats (per defecte, configurables)

| Paràmetre | Default v10 (fidel) | Default conservador (recomanat) |
|---|---|---|
| LotSize | 0.01 | 0.01 |
| GridRangePercent | 2.0 | 2.0 |
| GridLevels | 100 | 50 |
| ResetEquityPct | 1.0 | 3.0 |
| MaxDrawdownPct | (no n'hi ha) | 20.0 |
| MaxSpreadPoints | (no especificat) | 80 |
| AvoidWeekend | (no especificat) | true |
| Magic | propi | 88888 (diferent del v1 = 77777) |

---

## 12. Arquitectura del codi

### 12.1 Decisions

- **EA separat** del v1 actual (`DualGridEA_v1.mq5`) — NO modificar el v1
- **Un sol fitxer** per ara (`DualGridEA_v2_Reset.mq5`) — modular si calgués més endavant
- **Sense Telegram directe** — escriu heartbeat JSON, Python ho llegirà (com Brain v3)
- **Persistència JSON** a `Common\Files` per sobreviure reinicis MT5

### 12.2 Ubicació dels fitxers

Segons regla del projecte (editar al terminal MT5 primer, copiar al projecte):

**Treball real**:
`C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\9BB124B7D418C7FB69DF2865535BA9BF\MQL5\Experts\DualGridEA_v2_Reset.mq5`

**Còpia projecte**:
`C:\Users\Administrator\Desktop\MT4 Claude\MT5\MQL5\Experts\DualGridEA_v2_Reset.mq5`

**Fitxers de runtime**:
- Estat persistit: `Common\Files\dualgrid_v2_state_<Symbol>_<Magic>.json`
- Heartbeat: `Common\Files\dualgrid_v2_status.json`
- Logs (si activats): `MQL5\Files\dualgrid_v2_log_<YYYYMMDD>.log`

---

## 13. Pla d'implementació (per fases)

Quan tinguem les respostes del creador:

1. **Skeleton** — fitxer compila, `OnInit`/`OnTick` alive amb log inicial. Sense lògica de trading.
2. **Grid placement** — col·loca pendents amb retry, cancel·la al deinit
3. **Reset unilateral** — la lògica core
4. **Tracking de centres separats** — long_center i short_center independents
5. **Estat PROT.BE** — gestió de l'estat bloquejat
6. **Persistència JSON** — sobreviure reinici MT5
7. **Dashboard chart** — panel visual estil v10 amb tot el contingut clau
8. **Heartbeat JSON** — per integració amb Python
9. **Filtres bàsics** — spread màxim + AvoidWeekend
10. **Kill switch hard** — protecció catastròfica
11. **Test demo** — 24-48h en demo VTMarkets

**Cada fase es compila i revisa abans de passar a la següent.**

---

## 14. Historial de la conversa (decisions clau)

- **Inici**: l'usuari demana replicar l'estratègia. Pregunta si l'entenc.
- **Spec inicial**: l'usuari passa una spec textual i un screenshot. Jo interpreto malament — penso que el reset és **bilateral**.
- **Correcció 1**: l'usuari aporta més info del creador. Descobrim que el reset és **UNILATERAL** (només el costat negatiu).
- **Inferència errònia 2**: jo afirmo que hi ha **TPs per posició** (basant-me en la paraula "rascando" i en les línies del chart).
- **Correcció 2**: l'usuari observa que les línies **convergeixen a punts específics** (massive close, no TPs individuals). Em retracto — **NO hi ha TPs per posició**.
- **Estat actual**: model algorítmic clar. 4 preguntes pendents al creador per confirmar detalls fins.

---

## 15. Riscos coneguts NO resolts

1. **Vulnerabilitat inicial**: bot nou amb 0 cushion + trend immediat = bloqueig des del dia 1
2. **Sense SL**: una sola seqüència adversa pot causar liquidació
3. **Sample size del creador**: "2% diari" durant unes setmanes ≠ estratègia consistent a llarg termini
4. **No backtested**: no hem provat l'algoritme amb tick data històric (Apr-Oct 2024 seria letal)
5. **Spread costs**: amb resets freqüents, els spreads acumulats poden ser significatius (no calculat)
6. **Swap costs**: 100-400 posicions obertes alhora paguen swap diari (no calculat)

---

## 16. Pròxims passos immediats

1. ✅ **Aquest document** — recapitulació completa
2. ⏳ **Esperar respostes** del creador a les 4 preguntes (Telegram)
3. ⏳ **Actualitzar el model** segons respostes
4. ⏳ **Començar skeleton** del v2

---

*Fitxer mantingut per reflectir l'estat actual de l'anàlisi. Actualitzar quan arribin respostes del creador o canviï la comprensió de l'algoritme.*
