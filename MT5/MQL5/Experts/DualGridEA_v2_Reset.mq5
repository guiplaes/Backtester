//+------------------------------------------------------------------+
//|                                       DualGridEA_v2_Reset.mq5    |
//|             Dual-Grid amb reset UNILATERAL del costat MES NEGATIU |
//|                                                                  |
//| Model (hipotesi D — basada en analisi v10 + intencio usuari):    |
//|  - Grid bidireccional: a cada nivell hi ha BUY+SELL pendents     |
//|    amb TP petit (rascada). Es repoblan despres de disparar.      |
//|  - "Ancores" del reset: posicions reobertes al market durant     |
//|    el reset. NO tenen TP. Romanen obertes esperant proxim reset. |
//|  - Trigger reset: equity >= start_balance*(1 + ResetPct%) i      |
//|    almenys un costat te flotant<0. Si tots dos negatius, tria    |
//|    el costat MES negatiu.                                        |
//|  - Reset action: tanca TOTES les posicions del costat triat      |
//|    (ancores velles + fluids) i reobre N ancores al preu actual   |
//|    (market orders sense TP).                                     |
//|  - Cushion molt baix (default 0.01%) → resets frequents,         |
//|    posicions sempre reposicionades on el preu va.                |
//|                                                                  |
//| Doc analisi: MT5/DualGridEA_v2_Reset_ANALISI.md                  |
//+------------------------------------------------------------------+
#property copyright "Claude + User"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

//+------------------------------------------------------------------+
//| Inputs                                                            |
//+------------------------------------------------------------------+
input group "=== Grid bidireccional ==="
input double InpLotSize             = 2.0;    // [N11 cent] Lot per posicio (2.0=cent=0.02 standard equiv)
input double InpLevelSpacingUSD     = 1.0;    // [sp1 demo] Distancia entre nivells (USD) - sp=$1 millor en demo
input int    InpLevelsEachSide      = 5;      // Pendents per costat actius (5 BUY+SELL dalt, 5 BUY+SELL baix)
input double InpFluidTPUSD          = 1.0;    // [sp1 demo] TP de cada posicio (= rascada $1)
input bool   InpUseVirtualTP        = true;   // true: TP gestionat pel codi (chart net, sense linies TP). false: TP natiu al broker (visible al chart)

input group "=== Reset unilateral ==="
input double InpResetEquityPct      = 1.0;    // [N11 validat live] reset costat quan captured+side_flot > 1% baseline
input bool   InpUpdateBaselineOnReset = true;  // true (DEFAULT): baseline = equity post-reset (cycle compounding, fa creixer balance). false: baseline = start_balance fix (sostre).

input group "=== VALVULES de proteccio (anti-trend) ==="
input double InpMaxLotPerSide = 0.0;        // [N11] V-A OFF: cap d'exposure per costat. 0=OFF.
input double InpEmergencyResetLossPct = 0.0; // [N11] V-C OFF: kill costat si perd > X%. 0=OFF.
input double InpPositionSLSteps = 5.0;       // [N11 validat live] V-D ON: SL per posicio a 5 nivells = $25 contra.
input double InpHarvestWinnerPct = 2.0;      // [N11 validat live] V-B ON: captura profit guanyador > 2% baseline.
input bool   InpHarvestBalanced = true;       // V-B balanced: tanca AMBDOS costats quan dispara V-B (evita phantom profit). Recomanat true.
input bool   InpConsolidateAnchor   = true;   // true: ancora reset = 1 posicio amb suma del lot tancat
input int    InpMinSecBetweenResets = 5;      // pausa minima entre resets

input group "=== Equity Gap Reset (NOU) ==="
input double InpEquityGapResetPct   = 0.0;    // [EGR] tanca tot si gap(bal-eq)>X% balance. 0=OFF. Ex 5.0
input double InpEquityGapMinProfitPct = 0.0;  // [EGR] nomes dispara si equity > baseline+Y%. 0=qualsevol moment. Ex 3.0
input int    InpEquityGapMinSec     = 30;     // [EGR] pausa min entre EGR resets (sec)

input group "=== Progressive Trim (NOU - mata pitjor 1 a 1) ==="
input double InpProgressiveTrimGapPct = 0.0;  // [TRIM] tanca POSICIO mes negativa si gap>X% balance. 0=OFF. Ex 5.0 (continua grid)
input int    InpProgressiveTrimMinSec = 60;   // [TRIM] pausa min entre trims (sec)
input int    InpProgressiveTrimMaxPerTick = 1; // [TRIM] max posicions a tancar per tick

input group "=== Filtre Direccional EMA (NOU) ==="
input bool   InpTrendFilterEnabled  = false;  // [TREND] ON/OFF
input ENUM_TIMEFRAMES InpTrendTF    = PERIOD_H4;  // TF per calcular EMA (H1, H4, D1)
input int    InpTrendFastEMA        = 20;     // EMA ràpid (fast)
input int    InpTrendSlowEMA        = 50;     // EMA lent (slow)
input double InpTrendThresholdPct   = 0.1;    // % de separació min EMAs per detectar trend
input bool   InpTrendAllowCounter   = false;  // si true permet 1 pendent en direcció contrària al trend

input group "=== Safety ==="
input double InpMaxDrawdownPct      = 20.0;   // kill switch: tanca tot si equity < start*(1-this/100)
input int    InpMaxSpreadPoints     = 80;
input bool   InpAvoidWeekend        = true;
input int    InpWeekendCloseHourUTC = 22;
input int    InpWeekendOpenHourUTC  = 22;
input double InpMinPositionDistance = 0.0;    // distancia minima pendent a posicio oberta mateixa direccio (0=desactivat)

input group "=== Operations ==="
input long   InpMagicNumber         = 88888;
input string InpComment             = "DGv2R";

input group "=== UI / Logs ==="
input bool   InpDrawDashboard       = true;
input bool   InpVerboseLog          = false;  // posa true per debug intensiu
input int    InpHeartbeatSec        = 5;
input string InpHeartbeatFile       = "dualgrid_v2_status.json";
input int    InpDashboardRefreshSec = 2;

input group "=== Init Behavior ==="
input bool   InpResetStateOnInit    = false;
input bool   InpCleanSlateOnInit    = false;
input double InpStartBalanceOverride= 0.0;

//+------------------------------------------------------------------+
//| Constants                                                         |
//+------------------------------------------------------------------+
#define DIR_LONG  +1
#define DIR_SHORT -1

#define SIDE_STATE_ACTIVE  "ACTIVO"
#define SIDE_STATE_PROT_BE "PROT.BE"
#define SIDE_STATE_KILLED  "KILLED"

//+------------------------------------------------------------------+
//| Globals                                                           |
//+------------------------------------------------------------------+
CTrade        g_trade;
CPositionInfo g_pos;
COrderInfo    g_ord;

double   g_start_balance      = 0.0;   // FIX (kill switch + dashboard)
double   g_cycle_baseline     = 0.0;   // baseline per threshold reset (s'actualitza si InpUpdateBaselineOnReset)
double   g_cycle_start_balance= 0.0;   // balance al inici del cicle actual (qualsevol reset) [legacy/display]
double   g_cycle_start_equity = 0.0;   // equity al inici del cicle actual (qualsevol reset) [legacy/display]

// CICLES PER-COSTAT (independents — utilitzats pel trigger)
double   g_long_cycle_start_equity  = 0.0;  // equity quan va reset LONG (per al seu propi trigger)
double   g_long_cycle_start_balance = 0.0;  // balance quan va reset LONG (per display)
double   g_short_cycle_start_equity = 0.0;  // equity quan va reset SHORT
double   g_short_cycle_start_balance= 0.0;  // balance quan va reset SHORT
double   g_grid_anchor        = 0.0;   // centre del grid (es mou amb el preu)
double   g_last_long_reset_px = 0.0;   // preu de l'ultim reset LONG (display)
double   g_last_short_reset_px= 0.0;   // preu de l'ultim reset SHORT (display)
datetime g_last_long_reset    = 0;
datetime g_last_short_reset   = 0;
int      g_long_reset_count   = 0;
int      g_short_reset_count  = 0;
bool     g_killed             = false;
double   g_point              = 0.0;
int      g_digits             = 0;
datetime g_last_heartbeat     = 0;
datetime g_last_dashboard     = 0;

string   g_long_state         = SIDE_STATE_ACTIVE;
string   g_short_state        = SIDE_STATE_ACTIVE;

//+------------------------------------------------------------------+
//| Helpers basics                                                    |
//+------------------------------------------------------------------+
double NormPrice(double p)  { return NormalizeDouble(p, g_digits); }
double AskNow()              { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }
double BidNow()              { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double MidNow()              { return (AskNow() + BidNow()) / 2.0; }
double SpreadPoints()        { return (AskNow() - BidNow()) / g_point; }

double SnapToGrid(double price)
{
   if(g_grid_anchor == 0.0) g_grid_anchor = price;
   double offset = price - g_grid_anchor;
   double n = MathRound(offset / InpLevelSpacingUSD);
   return NormPrice(g_grid_anchor + n * InpLevelSpacingUSD);
}

bool SpreadOk()
{
   double sp = SpreadPoints();
   if(sp > InpMaxSpreadPoints) return false;
   return true;
}

bool WeekendOk()
{
   if(!InpAvoidWeekend) return true;
   MqlDateTime dt;
   TimeToStruct(TimeGMT(), dt);
   if(dt.day_of_week == 6) return false;
   if(dt.day_of_week == 5 && dt.hour >= InpWeekendCloseHourUTC) return false;
   if(dt.day_of_week == 0 && dt.hour <  InpWeekendOpenHourUTC) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Comptadors per direccio                                           |
//+------------------------------------------------------------------+
int CountPositionsByDir(int dir)
{
   int cnt = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if((dir == DIR_LONG && is_long) || (dir == DIR_SHORT && !is_long)) cnt++;
   }
   return cnt;
}

double SumFloatantByDir(int dir)
{
   double sum = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if((dir == DIR_LONG && is_long) || (dir == DIR_SHORT && !is_long))
         sum += g_pos.Profit() + g_pos.Swap() + g_pos.Commission();
   }
   return sum;
}

double AvgEntryByDir(int dir)
{
   double total_vol = 0.0, weighted = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if((dir == DIR_LONG && is_long) || (dir == DIR_SHORT && !is_long))
      {
         double vol = g_pos.Volume();
         weighted += g_pos.PriceOpen() * vol;
         total_vol += vol;
      }
   }
   return total_vol > 0 ? weighted / total_vol : 0.0;
}

int CountPendingsByDir(int dir)
{
   int cnt = 0;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(!g_ord.SelectByIndex(i)) continue;
      if(g_ord.Symbol() != _Symbol) continue;
      if(g_ord.Magic()  != InpMagicNumber) continue;
      ENUM_ORDER_TYPE ot = g_ord.OrderType();
      bool is_long_pending  = (ot == ORDER_TYPE_BUY_LIMIT  || ot == ORDER_TYPE_BUY_STOP);
      bool is_short_pending = (ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP);
      if((dir == DIR_LONG && is_long_pending) || (dir == DIR_SHORT && is_short_pending)) cnt++;
   }
   return cnt;
}

bool PendingExistsAt(double level, ENUM_ORDER_TYPE order_type)
{
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(!g_ord.SelectByIndex(i)) continue;
      if(g_ord.Symbol() != _Symbol) continue;
      if(g_ord.Magic()  != InpMagicNumber) continue;
      if(g_ord.OrderType() != order_type) continue;
      if(MathAbs(g_ord.PriceOpen() - level) < InpLevelSpacingUSD / 4.0) return true;
   }
   return false;
}

// Tanca les posicions MES PROFITABLES d'un costat fins acumular target_profit.
// Retorna el profit total realitzat. Per V-B (cap del guanyador).
double HarvestTopPositionsByDir(int dir, double target_profit)
{
   // Recull tickets + profits
   ulong  arr_tickets[];
   double arr_profits[];
   int    n = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if((dir == DIR_LONG && !is_long) || (dir == DIR_SHORT && is_long)) continue;
      ArrayResize(arr_tickets, n+1);
      ArrayResize(arr_profits, n+1);
      arr_tickets[n] = g_pos.Ticket();
      arr_profits[n] = g_pos.Profit() + g_pos.Swap() + g_pos.Commission();
      n++;
   }
   if(n == 0) return 0;
   // Ordena per profit DESC (manual, simple selection sort, n petit)
   for(int i = 0; i < n - 1; i++)
   {
      int max_idx = i;
      for(int j = i + 1; j < n; j++)
         if(arr_profits[j] > arr_profits[max_idx]) max_idx = j;
      if(max_idx != i)
      {
         double tp = arr_profits[i]; arr_profits[i] = arr_profits[max_idx]; arr_profits[max_idx] = tp;
         ulong  tt = arr_tickets[i]; arr_tickets[i] = arr_tickets[max_idx]; arr_tickets[max_idx] = tt;
      }
   }
   // Tanca de major a menor profit fins acumular target_profit
   double realized = 0;
   for(int i = 0; i < n; i++)
   {
      if(realized >= target_profit) break;
      if(arr_profits[i] <= 0) break;  // ja no n'hi ha de profitables
      if(g_trade.PositionClose(arr_tickets[i]))
         realized += arr_profits[i];
   }
   return realized;
}

// Tanca les posicions MES PERDEDORES d'un costat fins acumular max_loss_abs de pèrdua.
// max_loss_abs es positiu (p.ex. 100 = pot realitzar fins -100). Retorna pèrdua realitzada (negativa).
double HarvestWorstPositionsByDir(int dir, double max_loss_abs)
{
   ulong  arr_tickets[];
   double arr_profits[];
   int    n = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if((dir == DIR_LONG && !is_long) || (dir == DIR_SHORT && is_long)) continue;
      ArrayResize(arr_tickets, n+1);
      ArrayResize(arr_profits, n+1);
      arr_tickets[n] = g_pos.Ticket();
      arr_profits[n] = g_pos.Profit() + g_pos.Swap() + g_pos.Commission();
      n++;
   }
   if(n == 0) return 0;
   // Ordena per profit ASC (mes negatiu primer)
   for(int i = 0; i < n - 1; i++)
   {
      int min_idx = i;
      for(int j = i + 1; j < n; j++)
         if(arr_profits[j] < arr_profits[min_idx]) min_idx = j;
      if(min_idx != i)
      {
         double tp = arr_profits[i]; arr_profits[i] = arr_profits[min_idx]; arr_profits[min_idx] = tp;
         ulong  tt = arr_tickets[i]; arr_tickets[i] = arr_tickets[min_idx]; arr_tickets[min_idx] = tt;
      }
   }
   double realized = 0;
   for(int i = 0; i < n; i++)
   {
      if(-realized >= max_loss_abs) break;  // ja hem tancat suficient perdua
      if(arr_profits[i] >= 0) break;  // ja no n'hi ha de perdedores
      if(g_trade.PositionClose(arr_tickets[i]))
         realized += arr_profits[i];
   }
   return realized;
}

// Sum lot from PENDING orders by direction (not open positions, those use SumLotByDir)
double SumPendingLotByDir(int dir)
{
   double sum = 0.0;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      if(!g_ord.SelectByIndex(i)) continue;
      if(g_ord.Symbol() != _Symbol) continue;
      if(g_ord.Magic()  != InpMagicNumber) continue;
      ENUM_ORDER_TYPE ot = g_ord.OrderType();
      bool is_long  = (ot == ORDER_TYPE_BUY_LIMIT  || ot == ORDER_TYPE_BUY_STOP);
      bool is_short = (ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP);
      if((dir == DIR_LONG && is_long) || (dir == DIR_SHORT && is_short))
         sum += g_ord.VolumeInitial();
   }
   return sum;
}

// Comprova si ja hi ha una POSICIO oberta de la direccio indicada a aquest nivell
// (per evitar reposar un pendent que tornaria a obrir una duplicada)
bool PositionExistsAt(double level, int dir)
{
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if(dir == DIR_LONG  && !is_long) continue;
      if(dir == DIR_SHORT &&  is_long) continue;
      if(MathAbs(g_pos.PriceOpen() - level) < InpLevelSpacingUSD / 2.0) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Tancament / cancel.lacio                                          |
//+------------------------------------------------------------------+
bool CloseAllPositionsByDir(int dir)
{
   bool all_ok = true;
   int max_iter = 5;
   while(max_iter-- > 0)
   {
      bool found = false;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         if(!g_pos.SelectByIndex(i)) continue;
         if(g_pos.Symbol() != _Symbol) continue;
         if(g_pos.Magic()  != InpMagicNumber) continue;
         bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
         bool target  = (dir == DIR_LONG && is_long) || (dir == DIR_SHORT && !is_long);
         if(!target) continue;
         found = true;
         ulong tk = g_pos.Ticket();
         if(!g_trade.PositionClose(tk))
         {
            if(InpVerboseLog)
               PrintFormat("[DGv2R] FALLA tancant pos tk=%I64u err=%d", tk, g_trade.ResultRetcode());
            all_ok = false;
         }
      }
      if(!found) break;
   }
   return all_ok;
}

void CancelAllPendingsByDir(int dir)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!g_ord.SelectByIndex(i)) continue;
      if(g_ord.Symbol() != _Symbol) continue;
      if(g_ord.Magic()  != InpMagicNumber) continue;
      ENUM_ORDER_TYPE ot = g_ord.OrderType();
      bool is_long_pending  = (ot == ORDER_TYPE_BUY_LIMIT  || ot == ORDER_TYPE_BUY_STOP);
      bool is_short_pending = (ot == ORDER_TYPE_SELL_LIMIT || ot == ORDER_TYPE_SELL_STOP);
      if((dir == DIR_LONG && is_long_pending) || (dir == DIR_SHORT && is_short_pending))
         g_trade.OrderDelete(g_ord.Ticket());
   }
}

void CloseAllAndCancelAll()
{
   CancelAllPendingsByDir(DIR_LONG);
   CancelAllPendingsByDir(DIR_SHORT);
   CloseAllPositionsByDir(DIR_LONG);
   CloseAllPositionsByDir(DIR_SHORT);
}

//+------------------------------------------------------------------+
//| Col.locacio del grid bidireccional (BUY+SELL a cada nivell, TPs)  |
//+------------------------------------------------------------------+
// Trend filter state
enum TrendDir { TREND_NEUTRAL=0, TREND_UP=1, TREND_DOWN=-1 };
TrendDir g_trend = TREND_NEUTRAL;
datetime g_last_trend_check = 0;

void UpdateTrend()
{
   if(!InpTrendFilterEnabled) { g_trend = TREND_NEUTRAL; return; }

   // recalcula al canviar barra
   datetime cur_bar = iTime(_Symbol, InpTrendTF, 0);
   if(cur_bar == g_last_trend_check) return;
   g_last_trend_check = cur_bar;

   double ema_fast[], ema_slow[];
   ArraySetAsSeries(ema_fast, true);
   ArraySetAsSeries(ema_slow, true);

   int handle_fast = iMA(_Symbol, InpTrendTF, InpTrendFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   int handle_slow = iMA(_Symbol, InpTrendTF, InpTrendSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   if(handle_fast == INVALID_HANDLE || handle_slow == INVALID_HANDLE) return;

   if(CopyBuffer(handle_fast, 0, 1, 1, ema_fast) != 1) return;
   if(CopyBuffer(handle_slow, 0, 1, 1, ema_slow) != 1) return;

   double fast = ema_fast[0];
   double slow = ema_slow[0];
   double threshold = slow * (InpTrendThresholdPct / 100.0);

   TrendDir new_trend;
   if(fast > slow + threshold)      new_trend = TREND_UP;
   else if(fast < slow - threshold) new_trend = TREND_DOWN;
   else                              new_trend = TREND_NEUTRAL;

   if(new_trend != g_trend)
   {
      PrintFormat("[DGv2R TREND] %s -> %s (fast=%.2f slow=%.2f diff=%.4f%%)",
                  EnumToString(g_trend), EnumToString(new_trend),
                  fast, slow, (fast-slow)/slow*100.0);
      g_trend = new_trend;
   }
}

void PlacePendingPair(double level, bool above)
{
   if(!SpreadOk() || !WeekendOk()) return;

   // Filtre direccional: en trend, només permet entrades en la direcció del trend
   // Exception: above=true (preu sobre el nivell -> obrir BUY_STOP/SELL_LIMIT)
   //            above=false (preu sota nivell -> obrir BUY_LIMIT/SELL_STOP)
   bool allow_buy  = true;
   bool allow_sell = true;
   if(InpTrendFilterEnabled && g_trend != TREND_NEUTRAL)
   {
      if(g_trend == TREND_UP)   allow_sell = InpTrendAllowCounter;
      if(g_trend == TREND_DOWN) allow_buy  = InpTrendAllowCounter;
   }

   // Si TP virtual: passem TP=0 al broker (no es veu cap linia). Si TP natiu: passem el valor calculat.
   double tp_buy  = InpUseVirtualTP ? 0 : NormPrice(level + InpFluidTPUSD);
   double tp_sell = InpUseVirtualTP ? 0 : NormPrice(level - InpFluidTPUSD);
   // V-D: SL per posicio a N × spacing (entry - N×sp per BUY, entry + N×sp per SELL)
   double sl_buy  = (InpPositionSLSteps > 0) ? NormPrice(level - InpPositionSLSteps * InpLevelSpacingUSD) : 0;
   double sl_sell = (InpPositionSLSteps > 0) ? NormPrice(level + InpPositionSLSteps * InpLevelSpacingUSD) : 0;
   string c       = StringFormat("%s_%.2f", InpComment, level);

   ENUM_ORDER_TYPE buy_type  = above ? ORDER_TYPE_BUY_STOP   : ORDER_TYPE_BUY_LIMIT;
   ENUM_ORDER_TYPE sell_type = above ? ORDER_TYPE_SELL_LIMIT : ORDER_TYPE_SELL_STOP;

   // V-A: comprovacio de cap d'exposure per costat (lots oberts + pendents)
   bool long_cap_ok  = true;
   bool short_cap_ok = true;
   if(InpMaxLotPerSide > 0)
   {
      double long_total  = SumLotByDir(DIR_LONG)  + SumPendingLotByDir(DIR_LONG);
      double short_total = SumLotByDir(DIR_SHORT) + SumPendingLotByDir(DIR_SHORT);
      if(long_total  + InpLotSize > InpMaxLotPerSide + 1e-9) long_cap_ok  = false;
      if(short_total + InpLotSize > InpMaxLotPerSide + 1e-9) short_cap_ok = false;
   }

   // BUY: nomes si no hi ha pendent BUY al nivell NI posicio LONG oberta al nivell I no excedeix cap I trend permet
   if(allow_buy && long_cap_ok && !PendingExistsAt(level, buy_type) && !PositionExistsAt(level, DIR_LONG))
   {
      if(above) g_trade.BuyStop (InpLotSize, level, _Symbol, sl_buy, tp_buy, ORDER_TIME_GTC, 0, c);
      else      g_trade.BuyLimit(InpLotSize, level, _Symbol, sl_buy, tp_buy, ORDER_TIME_GTC, 0, c);
   }
   // SELL: nomes si no hi ha pendent SELL al nivell NI posicio SHORT oberta al nivell I no excedeix cap I trend permet
   if(allow_sell && short_cap_ok && !PendingExistsAt(level, sell_type) && !PositionExistsAt(level, DIR_SHORT))
   {
      if(above) g_trade.SellLimit(InpLotSize, level, _Symbol, sl_sell, tp_sell, ORDER_TIME_GTC, 0, c);
      else      g_trade.SellStop (InpLotSize, level, _Symbol, sl_sell, tp_sell, ORDER_TIME_GTC, 0, c);
   }
}

// Gestor de TPs virtuals: tanca posicions del grid (no ancores) quan arriben al target
// Aixo permet no posar TP al broker -> chart net sense linies TP
void ManageVirtualTPs()
{
   if(!InpUseVirtualTP) return;
   double bid = BidNow();
   double ask = AskNow();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;

      // Saltem les ancores (el seu comment conte "_ANCH_")
      string cmt = g_pos.Comment();
      if(StringFind(cmt, "_ANCH_") >= 0) continue;

      double entry  = g_pos.PriceOpen();
      bool   is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);

      if(is_long)
      {
         double target = entry + InpFluidTPUSD;
         if(bid >= target) g_trade.PositionClose(g_pos.Ticket());
      }
      else
      {
         double target = entry - InpFluidTPUSD;
         if(ask <= target) g_trade.PositionClose(g_pos.Ticket());
      }
   }
}

void PlaceFullGrid()
{
   double mid  = MidNow();
   double base = SnapToGrid(mid);
   g_grid_anchor = base;

   for(int i = 1; i <= InpLevelsEachSide; i++)
   {
      double lvl_up = NormPrice(base + i * InpLevelSpacingUSD);
      if(lvl_up > mid) PlacePendingPair(lvl_up, true);

      double lvl_dn = NormPrice(base - i * InpLevelSpacingUSD);
      if(lvl_dn < mid) PlacePendingPair(lvl_dn, false);
   }

   if(InpVerboseLog)
      PrintFormat("[DGv2R] Grid full colocada al voltant de %.2f spacing=%.2f levels=%d",
                  base, InpLevelSpacingUSD, InpLevelsEachSide);
}

// Manté la finestra de N pendents per costat seguint el preu actual
void MaintainPendingWindow()
{
   if(g_killed) return;
   if(!SpreadOk() || !WeekendOk()) return;

   double mid  = MidNow();
   double snap = SnapToGrid(mid);

   double max_up = snap + (InpLevelsEachSide + 0.25) * InpLevelSpacingUSD;
   double min_dn = snap - (InpLevelsEachSide + 0.25) * InpLevelSpacingUSD;

   // Cancel.la pendents fora de la finestra
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!g_ord.SelectByIndex(i)) continue;
      if(g_ord.Symbol() != _Symbol) continue;
      if(g_ord.Magic()  != InpMagicNumber) continue;
      double p = g_ord.PriceOpen();
      if(p > max_up || p < min_dn)
         g_trade.OrderDelete(g_ord.Ticket());
   }

   // Afegeix pendents que falten dins la finestra
   for(int i = 1; i <= InpLevelsEachSide; i++)
   {
      double lvl_up = NormPrice(snap + i * InpLevelSpacingUSD);
      if(lvl_up > mid) PlacePendingPair(lvl_up, true);

      double lvl_dn = NormPrice(snap - i * InpLevelSpacingUSD);
      if(lvl_dn < mid) PlacePendingPair(lvl_dn, false);
   }

   g_grid_anchor = snap;
}

//+------------------------------------------------------------------+
//| Reset (la logica core)                                            |
//+------------------------------------------------------------------+
// Calcula el lot total de les posicions obertes d'una direccio
double SumLotByDir(int dir)
{
   double sum = 0.0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(!g_pos.SelectByIndex(i)) continue;
      if(g_pos.Symbol() != _Symbol) continue;
      if(g_pos.Magic()  != InpMagicNumber) continue;
      bool is_long = (g_pos.PositionType() == POSITION_TYPE_BUY);
      if((dir == DIR_LONG && is_long) || (dir == DIR_SHORT && !is_long))
         sum += g_pos.Volume();
   }
   return sum;
}

// Reobre l'ancora consolidada: 1 posicio amb tot el lot tancat
// (o N posicions de max_lot si el total supera el max del broker)
void ReopenConsolidatedAnchor(int dir, double total_lot)
{
   if(total_lot <= 0) return;

   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double step    = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;

   // Snap al step de volum
   total_lot = NormalizeDouble(MathFloor(total_lot / step + 1e-9) * step, 4);
   if(total_lot < min_lot)
   {
      PrintFormat("[DGv2R] Ancora consolidada %s: lot total %.4f < min %.4f. Skip.",
                  dir == DIR_LONG ? "LONG" : "SHORT", total_lot, min_lot);
      return;
   }

   string cmt = StringFormat("%s_ANCH_%s", InpComment, dir == DIR_LONG ? "L" : "S");
   double remaining = total_lot;
   int    chunks    = 0;

   while(remaining >= min_lot && chunks < 50)  // safety cap
   {
      double chunk = MathMin(remaining, max_lot);
      chunk = NormalizeDouble(MathFloor(chunk / step + 1e-9) * step, 4);
      if(chunk < min_lot) break;

      bool ok = false;
      if(dir == DIR_LONG) ok = g_trade.Buy (chunk, _Symbol, 0, 0, 0, cmt);
      else                ok = g_trade.Sell(chunk, _Symbol, 0, 0, 0, cmt);

      if(!ok)
      {
         PrintFormat("[DGv2R] Ancora %s FAIL chunk=%.4f err=%d",
                     dir == DIR_LONG ? "LONG" : "SHORT", chunk, g_trade.ResultRetcode());
         break;
      }
      remaining -= chunk;
      chunks++;
   }

   PrintFormat("[DGv2R] Ancora %s consolidada: %.4f lot total en %d chunk(s)",
               dir == DIR_LONG ? "LONG" : "SHORT", total_lot - remaining, chunks);
}

// Fallback: reobre N posicions petites (si InpConsolidateAnchor=false)
void ReopenAnchorsAsLots(int dir, double total_lot)
{
   if(total_lot <= 0) return;
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(InpLotSize < min_lot) return;

   int n = (int)MathRound(total_lot / InpLotSize);
   if(n <= 0) return;

   string cmt = StringFormat("%s_ANCH_%s", InpComment, dir == DIR_LONG ? "L" : "S");
   int placed = 0;
   for(int i = 0; i < n; i++)
   {
      bool ok = false;
      if(dir == DIR_LONG) ok = g_trade.Buy (InpLotSize, _Symbol, 0, 0, 0, cmt);
      else                ok = g_trade.Sell(InpLotSize, _Symbol, 0, 0, 0, cmt);
      if(ok) placed++;
   }
   PrintFormat("[DGv2R] Ancores %s petites: %d obertes (%.4f lot)",
               dir == DIR_LONG ? "LONG" : "SHORT", placed, placed * InpLotSize);
}

bool ResetSide(int dir)
{
   string dir_name = (dir == DIR_LONG ? "LONG" : "SHORT");
   double float_before = SumFloatantByDir(dir);
   int    count_before = CountPositionsByDir(dir);
   double lot_before   = SumLotByDir(dir);
   double price_now    = MidNow();

   PrintFormat("[DGv2R] === RESET %s === price=%.2f flotant=%+.2f positions=%d lot_total=%.4f",
               dir_name, price_now, float_before, count_before, lot_before);

   // Tanca totes les posicions del costat (ancores velles + fluids)
   if(!CloseAllPositionsByDir(dir))
   {
      PrintFormat("[DGv2R] Reset %s: tancaments fallits. Reintentem proxim tick.", dir_name);
      return false;
   }

   // Reobre ancora consolidada (1 posicio amb lot_before) o N petites segons input
   if(InpConsolidateAnchor)
      ReopenConsolidatedAnchor(dir, lot_before);
   else
      ReopenAnchorsAsLots(dir, lot_before);

   // Actualitza estat
   if(dir == DIR_LONG)
   {
      g_long_reset_count++;
      g_last_long_reset    = TimeCurrent();
      g_last_long_reset_px = price_now;
      g_long_state         = SIDE_STATE_ACTIVE;
   }
   else
   {
      g_short_reset_count++;
      g_last_short_reset    = TimeCurrent();
      g_last_short_reset_px = price_now;
      g_short_state         = SIDE_STATE_ACTIVE;
   }

   // Cycle baseline: opcional bump (composat) post-reset
   if(InpUpdateBaselineOnReset)
   {
      double eq_after = AccountInfoDouble(ACCOUNT_EQUITY);
      PrintFormat("[DGv2R] Baseline cycle: %.2f -> %.2f (+%.4f%%)",
                  g_cycle_baseline, eq_after,
                  (eq_after / g_cycle_baseline - 1.0) * 100.0);
      g_cycle_baseline = eq_after;
   }

   // Marca el balance + equity al inici del nou cicle (post-reset)
   double bal_after = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq_after_for_cycle = AccountInfoDouble(ACCOUNT_EQUITY);
   double cycle_profit_real  = bal_after - g_cycle_start_balance;
   double cycle_profit_total = eq_after_for_cycle - g_cycle_start_equity;
   PrintFormat("[DGv2R] %s cicle tancat: real=%+.2f $, total=%+.2f $",
               dir_name, cycle_profit_real, cycle_profit_total);

   // CICLES per-costat: actualitza nomes el del costat que acaba de resetejar
   if(dir == DIR_LONG)
   {
      double long_cycle_pnl = eq_after_for_cycle - g_long_cycle_start_equity;
      PrintFormat("[DGv2R] LONG cycle P&L: %+.2f $ (start_eq %.2f -> %.2f)",
                  long_cycle_pnl, g_long_cycle_start_equity, eq_after_for_cycle);
      g_long_cycle_start_equity  = eq_after_for_cycle;
      g_long_cycle_start_balance = bal_after;
   }
   else
   {
      double short_cycle_pnl = eq_after_for_cycle - g_short_cycle_start_equity;
      PrintFormat("[DGv2R] SHORT cycle P&L: %+.2f $ (start_eq %.2f -> %.2f)",
                  short_cycle_pnl, g_short_cycle_start_equity, eq_after_for_cycle);
      g_short_cycle_start_equity  = eq_after_for_cycle;
      g_short_cycle_start_balance = bal_after;
   }

   // Manté els legacy per dashboard (qualsevol reset actualitza el "shared cycle")
   g_cycle_start_balance = bal_after;
   g_cycle_start_equity  = eq_after_for_cycle;

   SaveState();
   return true;
}

void TryReset()
{
   if(g_killed) return;

   datetime last_any_reset = MathMax(g_last_long_reset, g_last_short_reset);
   if(TimeCurrent() - last_any_reset < InpMinSecBetweenResets) return;

   double lf = SumFloatantByDir(DIR_LONG);
   double sf = SumFloatantByDir(DIR_SHORT);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   // V-B: Cap del flotant a X% per costat — tanca NOMES posicions que excedeixen el cap.
   // Si l'altre costat esta en perdua, usa el profit excedent per tancar tambe les seves
   // pitjors posicions (compensa). Aixi no hi ha phantom profit: balance i equity creixen
   // igual i el grid es mante operatiu (anchor i resta de posicions continuen).
   if(InpHarvestWinnerPct > 0)
   {
      double cap_threshold = balance * (InpHarvestWinnerPct / 100.0);

      // Tria el costat amb flotant excedent
      int    winner_dir = 0;
      double winner_flot = 0;
      if(lf > cap_threshold && lf > sf)      { winner_dir = DIR_LONG;  winner_flot = lf; }
      else if(sf > cap_threshold && sf > lf) { winner_dir = DIR_SHORT; winner_flot = sf; }

      if(winner_dir != 0)
      {
         double excess = winner_flot - cap_threshold;
         double realized_profit = HarvestTopPositionsByDir(winner_dir, excess);
         PrintFormat("[DGv2R] === HARVEST EXCESS %s === flot=%+.2f cap=%+.2f, realitzat=%+.2f",
                     winner_dir == DIR_LONG ? "LONG" : "SHORT",
                     winner_flot, cap_threshold, realized_profit);

         // Compensa: si l'altre costat esta en negatiu, tanca les seves pitjors posicions
         // fins a la quantitat realitzada del guanyador. Net balance change ≈ 0 pero
         // exposure global redueix i el bot queda mes net.
         int other_dir = (winner_dir == DIR_LONG) ? DIR_SHORT : DIR_LONG;
         double other_flot = SumFloatantByDir(other_dir);
         if(other_flot < 0 && realized_profit > 0)
         {
            double realized_loss = HarvestWorstPositionsByDir(other_dir, realized_profit);
            PrintFormat("[DGv2R]     COMPENSACIO %s: tancat perdedor %.2f (de %+.2f budget)",
                        other_dir == DIR_LONG ? "LONG" : "SHORT",
                        realized_loss, realized_profit);
         }
         return;
      }
   }

   // V-C: Emergency reset si flotant d'un costat < -X% del balance (PRE-empta el kill switch)
   if(InpEmergencyResetLossPct > 0)
   {
      double emergency_threshold = -balance * (InpEmergencyResetLossPct / 100.0);
      if(lf < emergency_threshold)
      {
         PrintFormat("[DGv2R] !!! EMERGENCY RESET LONG !!! flot=%.2f < %.2f (V-C %.1f%%)",
                     lf, emergency_threshold, InpEmergencyResetLossPct);
         ResetSide(DIR_LONG);
         return;
      }
      if(sf < emergency_threshold)
      {
         PrintFormat("[DGv2R] !!! EMERGENCY RESET SHORT !!! flot=%.2f < %.2f (V-C %.1f%%)",
                     sf, emergency_threshold, InpEmergencyResetLossPct);
         ResetSide(DIR_SHORT);
         return;
      }
   }

   // MODEL CORRECTE (sustainable balance growth):
   //   captured  = balance - cycle_start_balance (SHARED, des de últim reset qualsevol)
   //   threshold = cycle_start_balance × X%
   //   LONG metric  = captured + LONG_flotant
   //   SHORT metric = captured + SHORT_flotant
   //   Trigger side: that_side_flotant < 0 AND its_metric > threshold
   //
   // Justificacio: després del reset, new_balance = cycle_start + side_metric.
   // Si metric > threshold -> new_balance > cycle_start + threshold -> balance creix.
   double captured  = balance - g_cycle_start_balance;
   double threshold = g_cycle_start_balance * (InpResetEquityPct / 100.0);

   double long_metric  = captured + lf;
   double short_metric = captured + sf;

   bool long_trigger  = (lf < 0) && (long_metric  > threshold);
   bool short_trigger = (sf < 0) && (short_metric > threshold);

   // PROT.BE: side is negative but its metric doesn't reach threshold
   if(lf < 0 && !long_trigger)  g_long_state  = SIDE_STATE_PROT_BE;
   if(sf < 0 && !short_trigger) g_short_state = SIDE_STATE_PROT_BE;

   if(!long_trigger && !short_trigger)
      return;

   // Si ambdos compleixen, tria el de metric mes alta (max growth)
   int dir_to_reset = 0;
   if(long_trigger && short_trigger)
      dir_to_reset = (long_metric > short_metric) ? DIR_LONG : DIR_SHORT;
   else if(long_trigger)
      dir_to_reset = DIR_LONG;
   else
      dir_to_reset = DIR_SHORT;

   ResetSide(dir_to_reset);
}

//+------------------------------------------------------------------+
//| Update side states (per dashboard)                                |
//+------------------------------------------------------------------+
void UpdateSideStates()
{
   if(g_killed)
   {
      g_long_state  = SIDE_STATE_KILLED;
      g_short_state = SIDE_STATE_KILLED;
      return;
   }

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double lf = SumFloatantByDir(DIR_LONG);
   double sf = SumFloatantByDir(DIR_SHORT);

   // Mateix model que TryReset: per-side metric = captured + side_flotant
   double captured  = balance - g_cycle_start_balance;
   double threshold = g_cycle_start_balance * (InpResetEquityPct / 100.0);
   double long_metric  = captured + lf;
   double short_metric = captured + sf;

   bool long_can_trigger  = (lf < 0) && (long_metric  > threshold);
   bool short_can_trigger = (sf < 0) && (short_metric > threshold);

   if(lf < 0 && !long_can_trigger)  g_long_state  = SIDE_STATE_PROT_BE;
   else                              g_long_state  = SIDE_STATE_ACTIVE;
   if(sf < 0 && !short_can_trigger) g_short_state = SIDE_STATE_PROT_BE;
   else                              g_short_state = SIDE_STATE_ACTIVE;
}

//+------------------------------------------------------------------+
//| Kill switch                                                        |
//+------------------------------------------------------------------+
void CheckKillSwitch()
{
   if(g_killed) return;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double kill_threshold = g_start_balance * (1.0 - InpMaxDrawdownPct / 100.0);
   if(equity < kill_threshold)
   {
      g_killed = true;
      PrintFormat("[DGv2R] !!! KILL SWITCH !!! equity=%.2f < %.2f (-%.1f%%)",
                  equity, kill_threshold, InpMaxDrawdownPct);
      CloseAllAndCancelAll();
      SaveState();
   }
}

//+------------------------------------------------------------------+
//| Progressive Trim — tanca posicio MES negativa si gap>X%           |
//| NO tanca tot, NO reset baseline, grid continua operant            |
//+------------------------------------------------------------------+
datetime g_last_trim = 0;
void CheckProgressiveTrim()
{
   if(g_killed) return;
   if(InpProgressiveTrimGapPct <= 0) return; // OFF

   datetime now = TimeCurrent();
   if(now - g_last_trim < InpProgressiveTrimMinSec) return; // cooldown

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance <= 0) return;

   double gap_pct = ((balance - equity) / balance) * 100.0;
   if(gap_pct <= InpProgressiveTrimGapPct) return; // no triggered

   // Cerca i tanca la posicio MES negativa (1 per tick max)
   int closed = 0;
   for(int it = 0; it < InpProgressiveTrimMaxPerTick; it++)
   {
      ulong worst_ticket = 0;
      double worst_pnl = 0;
      for(int i = PositionsTotal()-1; i >= 0; i--)
      {
         if(!g_pos.SelectByIndex(i)) continue;
         if(g_pos.Symbol() != _Symbol) continue;
         if(g_pos.Magic()  != InpMagicNumber) continue;
         double pnl = g_pos.Profit() + g_pos.Swap();
         if(pnl < worst_pnl)
         {
            worst_pnl = pnl;
            worst_ticket = g_pos.Ticket();
         }
      }
      if(worst_ticket == 0) break;
      if(g_trade.PositionClose(worst_ticket))
      {
         PrintFormat("[DGv2R TRIM] gap=%.2f%% -> tanca #%I64u pnl=%.2f",
                     gap_pct, worst_ticket, worst_pnl);
         closed++;
         g_last_trim = now;
         // Re-check gap (potser ja prou)
         balance = AccountInfoDouble(ACCOUNT_BALANCE);
         equity  = AccountInfoDouble(ACCOUNT_EQUITY);
         gap_pct = ((balance - equity) / balance) * 100.0;
         if(gap_pct <= InpProgressiveTrimGapPct) break;
      }
      else break;
   }
}

//+------------------------------------------------------------------+
//| Equity Gap Reset (EGR) — tanca tot si gap balance-equity gran     |
//| i equity encara en profit. Captura abans que phantom mati equity. |
//+------------------------------------------------------------------+
datetime g_last_egr = 0;
void CheckEquityGapReset()
{
   if(g_killed) return;
   if(InpEquityGapResetPct <= 0) return; // OFF

   datetime now = TimeCurrent();
   if(now - g_last_egr < InpEquityGapMinSec) return; // cooldown

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance <= 0) return;

   double gap = balance - equity;
   double gap_pct = (gap / balance) * 100.0;
   double equity_net_pct = ((equity - g_start_balance) / g_start_balance) * 100.0;

   if(gap_pct > InpEquityGapResetPct && equity_net_pct >= InpEquityGapMinProfitPct)
   {
      PrintFormat("[DGv2R EGR] gap=%.2f%% > %.2f%% AND equity_net=%.2f%% >= %.2f%% -> CLOSE ALL + RESET BASELINE",
                  gap_pct, InpEquityGapResetPct, equity_net_pct, InpEquityGapMinProfitPct);
      CloseAllAndCancelAll();
      // Reset baseline a equity actual (post-tancament balance = equity prev)
      double new_baseline = AccountInfoDouble(ACCOUNT_BALANCE);
      g_cycle_baseline = new_baseline;
      g_cycle_start_balance = new_baseline;
      g_cycle_start_equity  = new_baseline;
      g_long_cycle_start_equity  = new_baseline;
      g_long_cycle_start_balance = new_baseline;
      g_short_cycle_start_equity = new_baseline;
      g_short_cycle_start_balance= new_baseline;
      // Re-ancora grid al preu actual
      g_grid_anchor = MidNow();
      g_last_egr = now;
      SaveState();
   }
}

//+------------------------------------------------------------------+
//| Persistencia (GlobalVariables)                                    |
//+------------------------------------------------------------------+
string GvName(string suffix)  { return StringFormat("DGv2R_%d_%s", (int)InpMagicNumber, suffix); }

void SaveState()
{
   GlobalVariableSet(GvName("start_bal"),     g_start_balance);
   GlobalVariableSet(GvName("cycle_base"),    g_cycle_baseline);
   GlobalVariableSet(GvName("cycle_bal"),     g_cycle_start_balance);
   GlobalVariableSet(GvName("cycle_eq"),      g_cycle_start_equity);
   GlobalVariableSet(GvName("long_cyc_eq"),   g_long_cycle_start_equity);
   GlobalVariableSet(GvName("long_cyc_bal"),  g_long_cycle_start_balance);
   GlobalVariableSet(GvName("short_cyc_eq"),  g_short_cycle_start_equity);
   GlobalVariableSet(GvName("short_cyc_bal"), g_short_cycle_start_balance);
   GlobalVariableSet(GvName("grid_anchor"),   g_grid_anchor);
   GlobalVariableSet(GvName("last_long_px"),  g_last_long_reset_px);
   GlobalVariableSet(GvName("last_short_px"), g_last_short_reset_px);
   GlobalVariableSet(GvName("long_resets"),   (double)g_long_reset_count);
   GlobalVariableSet(GvName("short_resets"),  (double)g_short_reset_count);
   GlobalVariableSet(GvName("killed"),        g_killed ? 1.0 : 0.0);
   GlobalVariableSet(GvName("ts"),            (double)TimeCurrent());
}

bool LoadState()
{
   if(InpResetStateOnInit) return false;
   if(!GlobalVariableCheck(GvName("start_bal"))) return false;

   datetime saved_ts = (datetime)GlobalVariableGet(GvName("ts"));
   int age = (int)(TimeCurrent() - saved_ts);
   if(age > 30 * 24 * 3600)
   {
      PrintFormat("[DGv2R] Estat guardat massa antic (%d dies). Fresh.", age / 86400);
      return false;
   }

   g_start_balance       = GlobalVariableGet(GvName("start_bal"));
   g_cycle_baseline      = GlobalVariableCheck(GvName("cycle_base")) ? GlobalVariableGet(GvName("cycle_base")) : g_start_balance;
   g_cycle_start_balance = GlobalVariableCheck(GvName("cycle_bal"))  ? GlobalVariableGet(GvName("cycle_bal"))  : g_start_balance;
   g_long_cycle_start_equity   = GlobalVariableCheck(GvName("long_cyc_eq"))   ? GlobalVariableGet(GvName("long_cyc_eq"))   : g_start_balance;
   g_long_cycle_start_balance  = GlobalVariableCheck(GvName("long_cyc_bal"))  ? GlobalVariableGet(GvName("long_cyc_bal"))  : g_start_balance;
   g_short_cycle_start_equity  = GlobalVariableCheck(GvName("short_cyc_eq"))  ? GlobalVariableGet(GvName("short_cyc_eq"))  : g_start_balance;
   g_short_cycle_start_balance = GlobalVariableCheck(GvName("short_cyc_bal")) ? GlobalVariableGet(GvName("short_cyc_bal")) : g_start_balance;
   if(GlobalVariableCheck(GvName("cycle_eq")))
      g_cycle_start_equity = GlobalVariableGet(GvName("cycle_eq"));
   else if(g_cycle_baseline > 0 && MathAbs(g_cycle_baseline - g_start_balance) > 0.01)
      g_cycle_start_equity = g_cycle_baseline;  // migracio: el baseline ja s'havia actualitzat, sincronitzem
   else
      g_cycle_start_equity = g_start_balance;
   g_grid_anchor         = GlobalVariableGet(GvName("grid_anchor"));
   g_last_long_reset_px  = GlobalVariableGet(GvName("last_long_px"));
   g_last_short_reset_px = GlobalVariableGet(GvName("last_short_px"));
   g_long_reset_count    = (int)GlobalVariableGet(GvName("long_resets"));
   g_short_reset_count   = (int)GlobalVariableGet(GvName("short_resets"));
   g_killed              = (GlobalVariableGet(GvName("killed")) > 0.5);

   PrintFormat("[DGv2R] Estat restaurat: start_bal=%.2f anchor=%.2f resets L=%d S=%d killed=%s",
               g_start_balance, g_grid_anchor, g_long_reset_count, g_short_reset_count,
               g_killed ? "true" : "false");
   return true;
}

//+------------------------------------------------------------------+
//| Heartbeat JSON                                                    |
//+------------------------------------------------------------------+
void WriteHeartbeat()
{
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   double mar = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   double flo = eq - bal;

   double lf  = SumFloatantByDir(DIR_LONG);
   double sf  = SumFloatantByDir(DIR_SHORT);
   int    lc  = CountPositionsByDir(DIR_LONG);
   int    sc  = CountPositionsByDir(DIR_SHORT);
   int    lp  = CountPendingsByDir(DIR_LONG);
   int    sp  = CountPendingsByDir(DIR_SHORT);
   double lbe = AvgEntryByDir(DIR_LONG);
   double sbe = AvgEntryByDir(DIR_SHORT);

   double eq_vs_start = (g_start_balance > 0 ? (eq / g_start_balance - 1.0) * 100.0 : 0.0);

   string json = "{";
   json += StringFormat("\"ts\":%d,", (int)TimeCurrent());
   json += StringFormat("\"symbol\":\"%s\",", _Symbol);
   json += StringFormat("\"magic\":%d,", (int)InpMagicNumber);
   json += StringFormat("\"start_balance\":%.2f,", g_start_balance);
   json += StringFormat("\"cycle_baseline\":%.2f,", g_cycle_baseline);
   json += StringFormat("\"cycle_start_balance\":%.2f,", g_cycle_start_balance);
   json += StringFormat("\"cycle_start_equity\":%.2f,", g_cycle_start_equity);
   json += StringFormat("\"profit_total\":%.2f,", bal - g_start_balance);
   json += StringFormat("\"profit_cycle_real\":%.2f,", bal - g_cycle_start_balance);
   json += StringFormat("\"profit_cycle_user\":%.2f,", eq - g_cycle_start_balance);
   json += StringFormat("\"cycle_threshold_usd\":%.2f,", g_cycle_start_balance * (InpResetEquityPct / 100.0));
   // Per-side cycle data
   double long_thr  = g_long_cycle_start_equity  * (InpResetEquityPct / 100.0);
   double short_thr = g_short_cycle_start_equity * (InpResetEquityPct / 100.0);
   json += StringFormat("\"long_cycle_start_equity\":%.2f,",  g_long_cycle_start_equity);
   json += StringFormat("\"long_cycle_threshold\":%.2f,",     long_thr);
   json += StringFormat("\"long_cycle_pnl\":%.2f,",           eq - g_long_cycle_start_equity);
   json += StringFormat("\"short_cycle_start_equity\":%.2f,", g_short_cycle_start_equity);
   json += StringFormat("\"short_cycle_threshold\":%.2f,",    short_thr);
   json += StringFormat("\"short_cycle_pnl\":%.2f,",          eq - g_short_cycle_start_equity);
   json += StringFormat("\"balance\":%.2f,", bal);
   json += StringFormat("\"equity\":%.2f,", eq);
   json += StringFormat("\"margin_level\":%.2f,", mar);
   json += StringFormat("\"floating\":%.2f,", flo);
   json += StringFormat("\"equity_vs_start_pct\":%.4f,", eq_vs_start);
   json += StringFormat("\"grid_anchor\":%.2f,", g_grid_anchor);
   json += StringFormat("\"last_long_reset_px\":%.2f,", g_last_long_reset_px);
   json += StringFormat("\"last_short_reset_px\":%.2f,", g_last_short_reset_px);
   json += StringFormat("\"long_count\":%d,", lc);
   json += StringFormat("\"short_count\":%d,", sc);
   json += StringFormat("\"long_pending\":%d,", lp);
   json += StringFormat("\"short_pending\":%d,", sp);
   json += StringFormat("\"long_floatant\":%.2f,", lf);
   json += StringFormat("\"short_floatant\":%.2f,", sf);
   json += StringFormat("\"long_be\":%.2f,", lbe);
   json += StringFormat("\"short_be\":%.2f,", sbe);
   json += StringFormat("\"long_state\":\"%s\",", g_long_state);
   json += StringFormat("\"short_state\":\"%s\",", g_short_state);
   json += StringFormat("\"long_resets\":%d,", g_long_reset_count);
   json += StringFormat("\"short_resets\":%d,", g_short_reset_count);
   json += StringFormat("\"reset_equity_pct\":%.4f,", InpResetEquityPct);
   json += StringFormat("\"fluid_tp_usd\":%.2f,", InpFluidTPUSD);
   json += StringFormat("\"spacing\":%.2f,", InpLevelSpacingUSD);
   json += StringFormat("\"levels_each_side\":%d,", InpLevelsEachSide);
   json += StringFormat("\"consolidate_anchor\":%s,", InpConsolidateAnchor ? "true" : "false");
   json += StringFormat("\"killed\":%s,", g_killed ? "true" : "false");
   json += StringFormat("\"current_price\":%.2f", MidNow());
   json += "}";

   int h = FileOpen(InpHeartbeatFile, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

//+------------------------------------------------------------------+
//| Dashboard al chart                                                |
//+------------------------------------------------------------------+
void CreateLabel(string name, string txt, int x, int y, color clr, int fsize = 9, string font = "Consolas")
{
   string nm = "DGv2R_" + name;
   if(ObjectFind(0, nm) < 0)
   {
      ObjectCreate(0, nm, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, nm, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
      ObjectSetInteger(0, nm, OBJPROP_ANCHOR, ANCHOR_RIGHT_UPPER);
   }
   ObjectSetInteger(0, nm, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, nm, OBJPROP_YDISTANCE, y);
   ObjectSetString (0, nm, OBJPROP_TEXT,      txt);
   ObjectSetInteger(0, nm, OBJPROP_COLOR,     clr);
   ObjectSetInteger(0, nm, OBJPROP_FONTSIZE,  fsize);
   ObjectSetString (0, nm, OBJPROP_FONT,      font);
}

void DrawDashboard()
{
   double eq    = AccountInfoDouble(ACCOUNT_EQUITY);
   double mar   = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
   double eq_pct = (g_start_balance > 0 ? (eq / g_start_balance - 1.0) * 100.0 : 0.0);
   double cycle_pct = (g_cycle_baseline > 0 ? (eq / g_cycle_baseline - 1.0) * 100.0 : 0.0);
   int    lc    = CountPositionsByDir(DIR_LONG);
   int    sc    = CountPositionsByDir(DIR_SHORT);
   int    lp    = CountPendingsByDir(DIR_LONG);
   int    sp    = CountPendingsByDir(DIR_SHORT);
   double lf    = SumFloatantByDir(DIR_LONG);
   double sf    = SumFloatantByDir(DIR_SHORT);
   double lbe   = AvgEntryByDir(DIR_LONG);
   double sbe   = AvgEntryByDir(DIR_SHORT);

   int x = 15, y = 25;
   color cyan = clrAqua, white = clrWhite, gray = clrSilver;
   color green = clrLimeGreen, red = clrTomato, amber = clrGold;
   color long_clr  = (StringCompare(g_long_state,  SIDE_STATE_ACTIVE) == 0 ? green :
                      StringCompare(g_long_state,  SIDE_STATE_PROT_BE) == 0 ? amber : red);
   color short_clr = (StringCompare(g_short_state, SIDE_STATE_ACTIVE) == 0 ? green :
                      StringCompare(g_short_state, SIDE_STATE_PROT_BE) == 0 ? amber : red);

   double bal_now = AccountInfoDouble(ACCOUNT_BALANCE);

   // MODEL CORRECTE: shared cycle, per-side metric = captured + side_flotant
   double captured        = bal_now - g_cycle_start_balance;
   double threshold_usd   = g_cycle_start_balance * (InpResetEquityPct / 100.0);
   double long_metric     = captured + lf;
   double short_metric    = captured + sf;
   bool long_can_trigger  = (lf < 0) && (long_metric  > threshold_usd);
   bool short_can_trigger = (sf < 0) && (short_metric > threshold_usd);

   CreateLabel("title", "DUAL GRID v2 | shared cycle + per-side metric", x, y, cyan, 10); y += 22;

   // ===================== CICLE COMPARTIT =====================
   CreateLabel("cyc_hdr",   "CICLE COMPARTIT (des de últim reset qualsevol)",                    x, y, white, 10); y += 18;
   CreateLabel("cyc_start", StringFormat("Cycle start bal %.2f $", g_cycle_start_balance),       x, y, gray); y += 14;
   color cap_clr = (captured >= 0 ? green : red);
   CreateLabel("cyc_cap",   StringFormat("Capturat        %+.2f $", captured),                   x, y, cap_clr); y += 14;
   CreateLabel("cyc_thr",   StringFormat("Threshold       %+.2f $ (%.4f%%)", threshold_usd, InpResetEquityPct), x, y, gray); y += 20;

   // ===================== LONG =====================
   CreateLabel("long_hdr",   StringFormat("LONG  (%d pos, %d pend)", lc, lp),               x, y, white, 10); y += 18;
   CreateLabel("long_float", StringFormat("Flotant       %+.2f $", lf),                     x, y, lf >= 0 ? green : red); y += 14;
   CreateLabel("long_be",    StringFormat("BE            %.2f", lbe),                       x, y, gray); y += 14;
   color lm_clr = long_can_trigger ? green : (lf < 0 ? amber : gray);
   CreateLabel("long_metric",StringFormat("Metric (cap+L) %+.2f $ vs %+.2f", long_metric, threshold_usd), x, y, lm_clr); y += 14;
   CreateLabel("long_trig",  StringFormat("Trigger       %s  (neg=%s, metr>thr=%s)",
                                           long_can_trigger ? "ARMAT" : "no",
                                           lf < 0 ? "S" : "N",
                                           long_metric > threshold_usd ? "S" : "N"),
                                                                                            x, y, lm_clr); y += 14;
   CreateLabel("long_st",    StringFormat("Estat         %s (resets:%d)", g_long_state, g_long_reset_count), x, y, long_clr); y += 22;

   // ===================== SHORT =====================
   CreateLabel("short_hdr",   StringFormat("SHORT (%d pos, %d pend)", sc, sp),               x, y, white, 10); y += 18;
   CreateLabel("short_float", StringFormat("Flotant       %+.2f $", sf),                     x, y, sf >= 0 ? green : red); y += 14;
   CreateLabel("short_be",    StringFormat("BE            %.2f", sbe),                       x, y, gray); y += 14;
   color sm_clr = short_can_trigger ? green : (sf < 0 ? amber : gray);
   CreateLabel("short_metric",StringFormat("Metric (cap+S) %+.2f $ vs %+.2f", short_metric, threshold_usd), x, y, sm_clr); y += 14;
   CreateLabel("short_trig",  StringFormat("Trigger       %s  (neg=%s, metr>thr=%s)",
                                            short_can_trigger ? "ARMAT" : "no",
                                            sf < 0 ? "S" : "N",
                                            short_metric > threshold_usd ? "S" : "N"),
                                                                                             x, y, sm_clr); y += 14;
   CreateLabel("short_st",    StringFormat("Estat         %s (resets:%d)", g_short_state, g_short_reset_count), x, y, short_clr); y += 22;

   // ===================== GLOBAL =====================
   CreateLabel("glb_hdr",    "GLOBAL",                                                x, y, white, 10); y += 18;
   double profit_total = bal_now - g_start_balance;
   double flotant_total = eq - bal_now;
   color pt_clr = (profit_total >= 0 ? green : red);
   color ft_clr = (flotant_total >= 0 ? green : red);
   CreateLabel("glb_bal_ini", StringFormat("Start balance  %.2f $", g_start_balance), x, y, gray); y += 14;
   CreateLabel("glb_bal",     StringFormat("Balance        %.2f $", bal_now),         x, y, gray); y += 14;
   CreateLabel("glb_eq",      StringFormat("Equity         %.2f $", eq),              x, y, gray); y += 14;
   CreateLabel("glb_flo",     StringFormat("Flotant TOTAL  %+.2f $", flotant_total),  x, y, ft_clr); y += 14;
   CreateLabel("glb_profit",  StringFormat("Profit total   %+.2f $ (realitzat)", profit_total), x, y, pt_clr); y += 14;
   CreateLabel("glb_eq_pct",  StringFormat("Equity vs inici %+.4f %%", eq_pct),       x, y, gray); y += 18;

   // ===================== SAFETY =====================
   CreateLabel("sf_hdr",     "SAFETY",                                                x, y, white, 10); y += 18;
   CreateLabel("sf_thr",     StringFormat("Threshold cicle  %.4f %% per costat", InpResetEquityPct), x, y, gray); y += 14;
   CreateLabel("sf_mar",     StringFormat("Nivel margen     %.1f %%", mar),           x, y, gray); y += 14;
   CreateLabel("sf_kill",    StringFormat("Kill switch a    %.1f %%", -InpMaxDrawdownPct), x, y, red); y += 18;

   if(g_killed)
   {
      CreateLabel("killed", ">> KILL SWITCH ACTIU <<", x, y, red, 12); y += 20;
   }
}

void ClearDashboard() { ObjectsDeleteAll(0, "DGv2R_"); }

//+------------------------------------------------------------------+
//| OnInit                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetDeviationInPoints(50);
   g_point  = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(InpLotSize <= 0 || InpLevelsEachSide < 2 || InpLevelSpacingUSD <= 0 || InpFluidTPUSD <= 0)
   {
      Print("[DGv2R] Inputs invalids. Avorto.");
      return INIT_FAILED;
   }

   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(InpLotSize < min_lot)
   {
      PrintFormat("[DGv2R] LotSize %.4f < min %.4f. Avorto.", InpLotSize, min_lot);
      return INIT_FAILED;
   }

   if(InpCleanSlateOnInit)
   {
      Print("[DGv2R] >>> CLEAN SLATE <<<");
      CloseAllAndCancelAll();
      Sleep(500);
   }

   bool loaded = LoadState();
   double mid = MidNow();
   if(!loaded)
   {
      g_start_balance       = AccountInfoDouble(ACCOUNT_BALANCE);
      g_cycle_baseline      = g_start_balance;
      g_cycle_start_balance = g_start_balance;
      g_cycle_start_equity  = g_start_balance;
      // Cicles per-costat: ambdos comencen a start_balance (no positions, equity=balance)
      g_long_cycle_start_equity   = g_start_balance;
      g_long_cycle_start_balance  = g_start_balance;
      g_short_cycle_start_equity  = g_start_balance;
      g_short_cycle_start_balance = g_start_balance;
      g_grid_anchor         = mid;
      g_last_long_reset_px  = mid;
      g_last_short_reset_px = mid;
      g_long_reset_count    = 0;
      g_short_reset_count   = 0;
      g_killed              = false;
   }

   if(InpStartBalanceOverride > 0.0)
   {
      g_start_balance       = InpStartBalanceOverride;
      g_cycle_baseline      = InpStartBalanceOverride;
      g_cycle_start_balance = InpStartBalanceOverride;
      PrintFormat("[DGv2R] start_balance + cycle_baseline FORCATS: %.2f", g_start_balance);
   }

   PrintFormat("[DGv2R] Init OK | start_bal=%.2f anchor=%.2f mid=%.2f spacing=%.2f tp=%.2f killed=%s",
               g_start_balance, g_grid_anchor, mid, InpLevelSpacingUSD, InpFluidTPUSD, g_killed ? "true" : "false");

   if(!g_killed)
   {
      int existing = CountPendingsByDir(DIR_LONG) + CountPendingsByDir(DIR_SHORT);
      if(existing == 0) PlaceFullGrid();
      else PrintFormat("[DGv2R] Pendents ja existeixen (%d). Skip placement inicial.", existing);
   }

   SaveState();
   UpdateSideStates();
   if(InpDrawDashboard) DrawDashboard();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| OnDeinit                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ClearDashboard();
   SaveState();
   PrintFormat("[DGv2R] Deinit (reason=%d)", reason);
}

//+------------------------------------------------------------------+
//| OnTick                                                            |
//+------------------------------------------------------------------+
void OnTick()
{
   if(g_killed)
   {
      UpdateSideStates();
      if(InpDrawDashboard && TimeCurrent() - g_last_dashboard >= InpDashboardRefreshSec)
      {
         DrawDashboard();
         g_last_dashboard = TimeCurrent();
      }
      return;
   }

   // 1) Kill switch
   CheckKillSwitch();
   if(g_killed) return;

   // 1.5) Equity Gap Reset (nou)
   CheckEquityGapReset();
   if(g_killed) return;

   // 1.6) Progressive Trim (tanca pitjor 1 a 1)
   CheckProgressiveTrim();
   if(g_killed) return;

   // 1.7) Actualitza trend filter (nou)
   UpdateTrend();

   // 2) Manté el grid bidireccional centrat al voltant del preu
   MaintainPendingWindow();

   // 3) Gestor de TPs virtuals (si actiu)
   ManageVirtualTPs();

   // 4) Reset si toca
   TryReset();

   // 4) Estats
   UpdateSideStates();

   // 5) Dashboard
   if(InpDrawDashboard && TimeCurrent() - g_last_dashboard >= InpDashboardRefreshSec)
   {
      DrawDashboard();
      g_last_dashboard = TimeCurrent();
   }

   // 6) Heartbeat
   if(InpHeartbeatSec > 0 && TimeCurrent() - g_last_heartbeat >= InpHeartbeatSec)
   {
      WriteHeartbeat();
      g_last_heartbeat = TimeCurrent();
   }
}

void OnTrade() {}
//+------------------------------------------------------------------+
