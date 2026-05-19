#!/usr/bin/env python3
"""
CME QuikStrike Auto Extractor v4 — Moneyness & Gamma Impact
Connects to Chrome (remote debugging), extracts OI/Change/Volume
from QuikStrike HTML, generates cme_levels.json for MT5 indicator.

Key decisions:
  - DTE weighting via 1/sqrt(DTE) — mathematically sound (gamma impact)
  - Bias = pure put/call ratio from OI only (no arbitrary combined formula)
  - Moneyness classification: ITM/ATM/OTM per strike relative to current price
  - Gamma impact: high (ATM ±3%), medium (±3-8%), low (far ITM/OTM)
  - Top 10 levels on chart, ALL levels sent to Claude for analysis
  - Daily historical copy saved to cme_history/
  - Claude prompt explains ITM vs OTM dynamics
"""

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import time, json, re, os, sys, math, subprocess, shutil
from datetime import datetime, timezone

CHROMEDRIVER = r"C:\Users\Administrator\.wdm\drivers\chromedriver\win64\146.0.7680.153\chromedriver-win32\chromedriver.exe"
COMMON_FILES = r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\Common\Files"
JSON_PATH = os.path.join(COMMON_FILES, "cme_levels.json")
HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cme_history")
TG_TOKEN = "8393198023:AAFbGB0pSzCyTujXb7orA0C-mSFUcQycOsg"
TG_CHAT_ID = "326155958"
CLI_PATH = r"C:\nodejs\node-v22.14.0-win-x64\node_modules\@anthropic-ai\claude-code\cli.js"
NODE_PATH = r"C:\nodejs\node-v22.14.0-win-x64\node.exe"
# Chart display: show all levels above these OI thresholds (per gamma impact)
# HIGH gamma (ATM) = most relevant, lower threshold
# LOW gamma (far ITM) = less relevant, needs much more OI to matter
CHART_OI_MIN = {'high': 500, 'medium': 1500, 'low': 5000}


# ===== CHROME / QUIKSTRIKE =====

def connect_to_chrome():
    opts = Options()
    opts.add_experimental_option('debuggerAddress', '127.0.0.1:9222')
    return webdriver.Chrome(service=Service(CHROMEDRIVER), options=opts)


def switch_to_quikstrike(driver):
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        if 'cmegroup' in driver.current_url:
            break
    for f in driver.find_elements(By.TAG_NAME, 'iframe'):
        if 'quikstrike' in (f.get_attribute('src') or '').lower():
            driver.switch_to.frame(f)
            return True
    return False


def find_and_click_view(driver, target):
    links = driver.find_elements(By.TAG_NAME, 'a')
    matches = [l for l in links if l.text.strip() == target and l.is_displayed()]
    if matches:
        matches[-1].click()
        print(f"  Clicked: '{target}'")
        return True
    return False


def parse_number(text):
    text = text.strip()
    if not text or text == '-':
        return 0
    clean = text.replace('.', '').replace(',', '')
    try:
        return int(clean)
    except ValueError:
        return 0


# ===== TABLE EXTRACTION WITH DTE WEIGHTING =====

def extract_table_data(driver):
    """Extract data from QuikStrike table with 1/sqrt(DTE) weighting per column.
    Also returns the nearest DTE found."""
    tables = driver.find_elements(By.TAG_NAME, 'table')
    data_table = None
    for table in tables:
        if len(table.find_elements(By.TAG_NAME, 'tr')) > 20:
            data_table = table
            break
    if not data_table:
        return {}, 999

    rows = data_table.find_elements(By.TAG_NAME, 'tr')
    if len(rows) < 4:
        return {}, 999

    # Row 1: expiration codes + DTE
    header1 = rows[1].find_elements(By.TAG_NAME, 'td') or rows[1].find_elements(By.TAG_NAME, 'th')
    header2 = rows[2].find_elements(By.TAG_NAME, 'td') or rows[2].find_elements(By.TAG_NAME, 'th')

    # Extract DTE per expiration column
    dte_values = []
    for cell in header1:
        m = re.search(r'(\d+)\s*DTE', cell.text.strip())
        if m:
            dte_values.append(int(m.group(1)))

    nearest_dte = min(dte_values) if dte_values else 999

    # Build column map: type (C/P) + DTE weight
    col_types = []
    col_weights = []
    current_dte_idx = 0

    for cell in header2:
        t = cell.text.strip()
        if t in ('C', 'P'):
            col_types.append(t)
            dte = dte_values[current_dte_idx] if current_dte_idx < len(dte_values) else 200
            col_weights.append(1.0 / math.sqrt(max(dte, 1)))
            if t == 'P':
                current_dte_idx += 1
        else:
            col_types.append('?')
            col_weights.append(0)

    # Parse data rows
    strikes = {}
    for row in rows[3:]:
        cells = row.find_elements(By.TAG_NAME, 'td')
        if not cells:
            continue
        strike_text = cells[0].text.strip()
        if not re.match(r'^\d{4}$', strike_text):
            continue

        strike = int(strike_text)
        calls_w, puts_w, calls_raw, puts_raw = 0.0, 0.0, 0, 0

        for i, cell in enumerate(cells[1:], 1):
            val = parse_number(cell.text)
            if val == 0 or i >= len(col_types):
                continue
            w = col_weights[i] if i < len(col_weights) else 0.05
            if col_types[i] == 'C':
                calls_w += abs(val) * w
                calls_raw += abs(val)
            elif col_types[i] == 'P':
                puts_w += abs(val) * w
                puts_raw += abs(val)

        if calls_raw > 0 or puts_raw > 0:
            strikes[strike] = {
                'calls': round(calls_w), 'puts': round(puts_w),
                'calls_raw': calls_raw, 'puts_raw': puts_raw,
                'total': round(calls_w + puts_w), 'total_raw': calls_raw + puts_raw
            }
    return strikes, nearest_dte


def extract_all_views(driver):
    results = {}
    nearest_dte = 999

    print("Switching to Open Interest...")
    find_and_click_view(driver, "OI")
    time.sleep(5)
    results['oi'], nearest_dte = extract_table_data(driver)
    print(f"  {len(results['oi'])} strikes, nearest DTE={nearest_dte}")

    print("Switching to OI Change...")
    if find_and_click_view(driver, "OI Change"):
        time.sleep(5)
        results['oi_change'], _ = extract_table_data(driver)
        print(f"  {len(results['oi_change'])} strikes")
    else:
        results['oi_change'] = {}

    print("Switching to Volume...")
    if find_and_click_view(driver, "Volume"):
        time.sleep(5)
        results['volume'], _ = extract_table_data(driver)
        print(f"  {len(results['volume'])} strikes")
    else:
        results['volume'] = {}

    results['nearest_dte'] = nearest_dte
    return results


# ===== MONEYNESS & GAMMA IMPACT =====

def classify_moneyness(strike, price):
    """Classify strike moneyness relative to current price.
    ATM = within ±3%, then near (±3-8%), then far (>8%)."""
    if price <= 0:
        return "OTM", "medium"

    pct_diff = abs(strike - price) / price * 100

    if pct_diff <= 3.0:
        return "ATM", "high"
    elif pct_diff <= 8.0:
        # Near OTM or near ITM
        return ("ITM" if strike < price else "OTM"), "medium"
    else:
        # Far from price
        return ("ITM" if strike < price else "OTM"), "low"


def classify_level_gamma(strike, price, bias):
    """Classify gamma impact based on research:
    - Gamma is MAXIMUM at ATM (strike ≈ price) for the dominant side
    - Puts are ATM/OTM when strike <= price, ITM when strike > price
    - Calls are ATM/OTM when strike >= price, ITM when strike < price
    - ITM options have HIGH delta but LOW gamma = hedging already done
    - Key insight: a "support" level (puts dominate) ABOVE the price has
      puts that are ITM = the support effect is WEAKER than one below price
    """
    if price <= 0:
        return "OTM", "medium"

    pct_diff = abs(strike - price) / price * 100

    # Determine moneyness of the DOMINANT side
    if bias == 'support':
        # Puts dominate. Puts are ITM when strike > price
        dominant_itm = strike > price
    elif bias == 'resistance':
        # Calls dominate. Calls are ITM when strike < price
        dominant_itm = strike < price
    else:
        # Magnet: both sides. Consider ITM if far from price
        dominant_itm = pct_diff > 3.0

    # Classify moneyness
    if pct_diff <= 1.5:
        moneyness = "ATM"
    elif dominant_itm:
        moneyness = "ITM"
    else:
        moneyness = "OTM"

    # Gamma impact:
    # ATM (very close) + dominant side OTM or near = HIGH
    # OTM within range = medium to high (gamma builds as price approaches)
    # ITM = LOW (hedging done, gamma drops with distance)
    if pct_diff <= 1.5:
        # Very close to price: always high impact
        gamma = "high"
    elif not dominant_itm:
        # Dominant side is OTM: gamma still relevant
        if pct_diff <= 5.0:
            gamma = "high"
        elif pct_diff <= 10.0:
            gamma = "medium"
        else:
            gamma = "low"
    else:
        # Dominant side is ITM: gamma drops
        if pct_diff <= 3.0:
            gamma = "medium"  # Just barely ITM, still some gamma
        elif pct_diff <= 6.0:
            gamma = "low"
        else:
            gamma = "low"

    return moneyness, gamma


# ===== BUILD JSON =====

def calc_max_pain(oi_data):
    all_strikes = sorted(oi_data.keys())
    if not all_strikes:
        return 0
    min_pain, mp_strike = float('inf'), 0
    for test in all_strikes:
        pain = sum(d['puts'] * (test - s) for s, d in oi_data.items() if s < test)
        pain += sum(d['calls'] * (s - test) for s, d in oi_data.items() if s > test)
        if pain < min_pain:
            min_pain, mp_strike = pain, test
    return mp_strike


def build_json(oi, oi_change, volume, nearest_dte):
    max_pain = calc_max_pain(oi)
    price = get_current_price()

    # Max pain always on chart
    chart_strikes = set()
    if max_pain:
        chart_strikes.add(max_pain)

    # Build ALL levels (for Claude), marking which are top (for chart)
    all_levels = []
    for strike, d in sorted(oi.items()):
        c, p, t = d['calls'], d['puts'], d['total']

        # Bias = pure put/call ratio
        ratio = p / c if c > 0 else 999
        if ratio > 1.3:
            bias = 'support'
        elif ratio < 0.77:
            bias = 'resistance'
        else:
            bias = 'magnet'

        # Moneyness and gamma impact
        moneyness, gamma_impact = classify_level_gamma(strike, price, bias)

        chg = oi_change.get(strike, {})
        vol = volume.get(strike, {})

        level = {
            'price': strike,
            'oi': t,
            'oi_raw': d.get('total_raw', t),
            'calls': c, 'puts': p,
            'calls_raw': d.get('calls_raw', c), 'puts_raw': d.get('puts_raw', p),
            'bias': bias,
            'ratio': round(ratio, 2),
            'moneyness': moneyness,
            'gamma_impact': gamma_impact,
            'show_on_chart': (strike in chart_strikes) or (t >= CHART_OI_MIN.get(gamma_impact, 5000)),
            'oi_change': chg.get('total', 0),
            'oi_change_calls': chg.get('calls', 0),
            'oi_change_puts': chg.get('puts', 0),
            'volume': vol.get('total', 0),
            'volume_calls': vol.get('calls', 0),
            'volume_puts': vol.get('puts', 0),
        }
        all_levels.append(level)

    # Load existing COT + GVZ
    cot_data, gvz_data = None, None
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, 'r') as f:
                existing = json.load(f)
            cot_data = existing.get('cot')
            gvz_data = existing.get('gvz')
        except Exception:
            pass

    return {
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'max_pain': max_pain,
        'days_to_expiry': nearest_dte,
        'current_price': round(price, 2),
        'levels': all_levels,
        'cot': cot_data,
        'gvz': gvz_data
    }


# ===== HISTORICAL COPY =====

def save_history(json_path):
    """Save a dated copy of the JSON to cme_history/ folder."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    hist_path = os.path.join(HISTORY_DIR, f"cme_levels_{today}.json")
    try:
        shutil.copy2(json_path, hist_path)
        print(f"History saved: {hist_path}")
    except Exception as e:
        print(f"History save error: {e}")


# ===== TELEGRAM =====

def send_telegram(text):
    import urllib.request, urllib.parse, ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'HTML'
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10, context=ctx)
        print("Telegram sent!")
    except Exception as e:
        print(f"Telegram error: {e}")


def get_current_price():
    for fname in ['claude_heartbeat.json', 'claude_positions.json']:
        try:
            with open(os.path.join(COMMON_FILES, fname), 'r') as f:
                data = json.load(f)
            price = data.get('bid', 0) or data.get('market', {}).get('bid', 0)
            if price and price > 1000:
                return price
        except Exception:
            pass
    return 0


def send_claude_report(result):
    """Have Claude Opus analyze the data and send via Telegram"""
    all_levels = result.get('levels', [])
    if not all_levels:
        send_telegram("⚠️ CME: No hi ha dades. Sessió caducada?")
        return

    price = result.get('current_price', 0) or get_current_price()
    dte = result.get('days_to_expiry', 999)

    # Build raw data for Claude — ALL levels, not just top N
    data_text = f"""DADES CME GOLD OPTIONS (actualitzades ara):
Preu actual XAUUSD: ${price:.2f}
Max Pain: ${result.get('max_pain', 0)} (dies fins expiració més propera: {dte})
COT: {json.dumps(result.get('cot'), indent=2) if result.get('cot') else 'N/A'}
GVZ: {json.dumps(result.get('gvz'), indent=2) if result.get('gvz') else 'N/A'}

TOTS ELS NIVELLS ({len(all_levels)} strikes, OI ponderat per DTE amb 1/sqrt(DTE)):
{'='*90}
"""
    for lvl in sorted(all_levels, key=lambda x: x['price']):
        chart_mark = " [CHART]" if lvl.get('show_on_chart') else ""
        data_text += (
            f"  ${lvl['price']} | {lvl['bias']:10s} | {lvl['moneyness']:3s} gamma={lvl['gamma_impact']:6s} | "
            f"OI_dte={lvl['oi']:>7,} OI_raw={lvl['oi_raw']:>7,} | "
            f"C={lvl['calls']:,} P={lvl['puts']:,} ratio={lvl['ratio']} | "
            f"chg={lvl['oi_change']:+,} vol={lvl['volume']:,}{chart_mark}\n"
        )

    # Load previous data to show changes
    old_path = JSON_PATH + '.prev'
    changes_text = ""
    if os.path.exists(old_path):
        try:
            with open(old_path, 'r') as f:
                old = json.load(f)
            old_levels = {l['price']: l for l in old.get('levels', [])}
            bias_changes = []
            for lvl in all_levels:
                old_lvl = old_levels.get(lvl['price'])
                if old_lvl and old_lvl.get('bias') != lvl['bias']:
                    bias_changes.append(f"  ${lvl['price']}: {old_lvl['bias']} → {lvl['bias']}")
            if bias_changes:
                changes_text = "\nCANVIS DE BIAS vs AHIR:\n" + "\n".join(bias_changes)
        except Exception:
            pass

    # Save current as previous
    if os.path.exists(JSON_PATH):
        shutil.copy2(JSON_PATH, old_path)

    prompt = f"""{data_text}{changes_text}

Ets un analista de trading de XAUUSD. El trader rep senyals BUY/SELL de canals de Telegram.

DADES:
- "suport" (S) = puts dominen a aquest nivell
- "resistència" (R) = calls dominen
- "imant" (M) = equilibrat
- moneyness: ATM (prop del preu), OTM (fora del diner), ITM (dins del diner)
- gamma: high/medium/low = impacte real sobre el preu

COM INTERPRETAR (CRÍTIC — aplica això, no ho expliquis al trader):
- Un nivell amb gamma HIGH i OTM: l'efecte serà FORT quan el preu s'hi acosti
  - Suport OTM per SOTA: si el preu cau cap allà, REBOTA (protecció real)
  - Resistència OTM per SOBRE: si el preu puja cap allà, FRENA
- Un nivell amb gamma LOW i ITM: l'efecte és FEBLE
  - Suport ITM per SOBRE del preu: el preu TRAVESSA fàcil pujant
  - Resistència ITM per SOTA del preu: el preu TRAVESSA fàcil baixant
- Imant: el preu gravita cap allà i s'hi queda. Perill si estàs en una posició
- DIRECCIÓ IMPORTA: el mateix nivell pot rebotar des d'un costat i travessar-se des de l'altre

MAX PAIN ({dte} DTE):
- DTE <= 3: gravitació MOLT forta | DTE 4-10: moderada | DTE > 10: orientativa

GENERA UN INFORME BREU per Telegram en CATALÀ. Preu actual ${price:.0f}:

1. ON ESTEM: entre quins nivells importants?
2. SI REB BUY: quins nivells per SOTA protegeixen de veritat? quins per SOBRE frenen o travessa fàcil?
3. SI REB SELL: quins nivells per SOBRE protegeixen? quins per SOTA frenen o travessa fàcil?
4. CONTEXT GENERAL:
   - Si COT diu LONG: "els institucionals estan comprant, biaix alcista"
   - Si COT diu SHORT: "els institucionals estan venent, biaix baixista"
   - Si GVZ > 30: "volatilitat alta, moviments grans esperables"
   - Si GVZ < 20: "volatilitat baixa, rang estret esperable"
   - Si hi ha canvis grans en OI Change: "diners nous entrant a $XXXX" o "es debilita $XXXX"
5. CANVIS vs ahir (si n'hi ha)

REGLES:
- ZERO teoria (no puts, calls, opcions, gamma, market makers, hedging, delta, ITM, OTM, contractes)
- Parla en termes de: "rebota", "frena", "travessa fàcil", "atrau", "protecció forta/feble"
- Dona nivells concrets amb $
- MÀXIM 4-5 nivells per direcció. Agrupa els propers.
- Si és cap de setmana, digues que les dades són de l'últim dia de mercat
- MAX 250 paraules
- Respon NOMÉS amb el text del missatge"""

    try:
        print("Calling Claude Opus...")
        proc = subprocess.run(
            [NODE_PATH, CLI_PATH, "-p", prompt, "--output-format", "text", "--model", "claude-opus-4-0"],
            capture_output=True, timeout=120,
            cwd=r"C:\Users\Administrator\Desktop\MT4 Claude"
        )
        stdout = proc.stdout.decode('utf-8', errors='replace').strip() if proc.stdout else ''
        if proc.returncode == 0 and stdout:
            send_telegram(stdout[:4000])
            print("Claude report sent!")
        else:
            stderr = proc.stderr.decode('utf-8', errors='replace')[:200] if proc.stderr else ''
            print(f"Claude CLI error: {stderr}")
            send_telegram(f"📊 CME: {len(all_levels)} nivells | Max Pain ${result.get('max_pain',0)}\n(Claude no disponible)")
    except Exception as e:
        print(f"Claude error: {e}")
        send_telegram(f"📊 CME: {len(all_levels)} nivells | Max Pain ${result.get('max_pain',0)}")


# ===== MAIN =====

def main():
    print("=" * 60)
    print("CME QuikStrike Auto Extractor v4 — Moneyness & Gamma")
    print("=" * 60)

    print("\nConnecting to Chrome...")
    driver = connect_to_chrome()

    print("Switching to QuikStrike iframe...")
    if not switch_to_quikstrike(driver):
        print("ERROR: QuikStrike iframe not found!")
        send_telegram("⚠️ CME: No puc accedir a QuikStrike. Fes login al Chrome de debug.")
        return

    print("Extracting data...")
    data = extract_all_views(driver)

    if not data.get('oi'):
        print("ERROR: No OI data!")
        send_telegram("⚠️ CME: No s'han extret dades. Revisa la pàgina.")
        return

    print("\nBuilding JSON...")
    result = build_json(data['oi'], data.get('oi_change', {}), data.get('volume', {}),
                        data.get('nearest_dte', 999))

    with open(JSON_PATH, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"Written to {JSON_PATH}")

    # Save historical copy
    save_history(JSON_PATH)

    # Print summary
    chart_levels = [l for l in result.get('levels', []) if l.get('show_on_chart')]
    all_levels = result['levels']
    print(f"\n{'='*60}")
    print(f"Total: {len(all_levels)} levels | Chart: {len(chart_levels)} | Max Pain: ${result['max_pain']} | DTE: {result['days_to_expiry']}")
    print(f"Price: ${result['current_price']}")
    print(f"{'='*60}")
    for lvl in sorted(all_levels, key=lambda x: -x['oi']):
        chart_tag = " [CHART]" if lvl.get('show_on_chart') else ""
        print(f"  ${lvl['price']} {lvl['bias'][0].upper()} {lvl['moneyness']:3s} g={lvl['gamma_impact']:6s} "
              f"OI:{lvl['oi']:>7,} (raw:{lvl['oi_raw']:>7,}) "
              f"C:{lvl['calls']:,} P:{lvl['puts']:,} chg:{lvl['oi_change']:+,} vol:{lvl['volume']:,}{chart_tag}")

    print("\nSending Claude report...")
    send_claude_report(result)
    print("Done!")


if __name__ == '__main__':
    main()
