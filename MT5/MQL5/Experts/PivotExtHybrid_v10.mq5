//+------------------------------------------------------------------+
//| PivotExtHybrid_v10.mq5                                            |
//| TP2 single-exit with BE protection on TP1                         |
//| - Primary pivots only (R1, S1, PP)                                 |
//| - Stop & Reverse                                                   |
//| - SL = prev opposite pivot                                         |
//| - TP1 (intermediate pivot) → triggers SL→BE (no close)             |
//| - TP2 = entry + (TP1-entry)×Mult → 100% close                      |
//| - Session filter 02-15 broker time (Asia+London, no NY)            |
//+------------------------------------------------------------------+
#property copyright "Pivot Ext v10 TP2-only with BE"
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

input group "── Strategy ──"
input ENUM_TIMEFRAMES InpPivotTF = PERIOD_D1;
input double InpTP2Mult       = 2.0;   // TP2 = entry + (TP1-entry) × this
input bool   InpMoveSLtoBE    = true;  // Move SL to BE when TP1 reached

input group "── Risk ──"
input double InpLot      = 0.10;
input int    InpMagic    = 991001;

input group "── Filters ──"
input bool   InpUseSession    = true;   // Filter by broker-time session (true = Asian+London only)
input int    InpSessionStartH = 2;      // Start hour (broker time) — 02:00 = late Asian
input int    InpSessionEndH   = 15;     // End hour (broker time, exclusive) — 15:00 = before NY momentum

input group "── Logging ──"
input bool   InpVerbose = false;

CTrade        trade;
CPositionInfo posInfo;
CSymbolInfo   symInfo;

double g_pp, g_r1, g_s1, g_r2, g_s2, g_r3, g_s3;
double g_prevH = 0, g_prevL = 0, g_prevC = 0;
datetime g_lastPivotDay = 0;
datetime g_lastBarTime = 0;

bool   g_inPos = false;
int    g_posDir = 0;
double g_entryPx = 0;
double g_slPx = 0;
double g_tp1Px = 0;
double g_tp2Px = 0;
bool   g_tp1Reached = false;   // tracks if intermediate pivot was touched (for BE move)
ulong  g_posTicket = 0;

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetMarginMode();
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(20);
   symInfo.Name(_Symbol);
   symInfo.RefreshRates();
   Print("PivotExt v10 All-TP1 init for ", _Symbol);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {}

void UpdatePivots()
{
   double pH[1], pL[1], pC[1];
   if (CopyHigh(_Symbol, InpPivotTF, 1, 1, pH) <= 0) return;
   if (CopyLow (_Symbol, InpPivotTF, 1, 1, pL) <= 0) return;
   if (CopyClose(_Symbol, InpPivotTF, 1, 1, pC) <= 0) return;
   g_prevH = pH[0]; g_prevL = pL[0]; g_prevC = pC[0];
   g_pp = (g_prevH + g_prevL + g_prevC) / 3.0;
   g_r1 = 2*g_pp - g_prevL;
   g_s1 = 2*g_pp - g_prevH;
   g_r2 = g_pp + (g_prevH - g_prevL);
   g_s2 = g_pp - (g_prevH - g_prevL);
   g_r3 = g_prevH + 2*(g_pp - g_prevL);
   g_s3 = g_prevL - 2*(g_prevH - g_pp);
}

double FindNextAbove(double price)
{
   double levels[7] = {g_pp, g_r1, g_r2, g_r3, g_s1, g_s2, g_s3};
   double best = EMPTY_VALUE;
   for (int i = 0; i < 7; i++)
      if (levels[i] > price && (best == EMPTY_VALUE || levels[i] < best))
         best = levels[i];
   return best;
}

double FindNextBelow(double price)
{
   double levels[7] = {g_pp, g_r1, g_r2, g_r3, g_s1, g_s2, g_s3};
   double best = EMPTY_VALUE;
   for (int i = 0; i < 7; i++)
      if (levels[i] < price && (best == EMPTY_VALUE || levels[i] > best))
         best = levels[i];
   return best;
}

// PRIMARY PIVOTS ONLY (R1, S1, PP) — filters out R2/R3/S2/S3 noise
bool LongSignalOnBar(double curHigh, double prevHigh)
{
   double spread = symInfo.Spread() * _Point;
   double askH = curHigh + spread;
   double askHp = prevHigh + spread;
   double levels[3] = {g_r1, g_pp, g_s1};
   for (int i = 0; i < 3; i++)
      if (askH > levels[i] && askHp <= levels[i])
         return true;
   return false;
}

bool ShortSignalOnBar(double curLow, double prevLow)
{
   double levels[3] = {g_s1, g_pp, g_r1};
   for (int i = 0; i < 3; i++)
      if (curLow < levels[i] && prevLow >= levels[i])
         return true;
   return false;
}

bool InSession()
{
   if (!InpUseSession) return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= InpSessionStartH && dt.hour < InpSessionEndH);
}

void OpenPosition(int direction)
{
   if (g_inPos && g_posDir != direction) {
      if (trade.PositionClose(_Symbol)) {
         g_inPos = false; g_posDir = 0; g_tp1Reached = false;
      }
   }
   if (g_inPos) return;

   double bid = symInfo.Bid();
   double ask = symInfo.Ask();
   double entry = (direction > 0) ? ask : bid;

   double sl, tp1, tp2;
   if (direction > 0) {
      sl = FindNextBelow(entry);
      if (sl == EMPTY_VALUE) sl = entry * 0.99;
      tp1 = FindNextAbove(entry);
      if (tp1 == EMPTY_VALUE) tp1 = entry + (entry - sl);
      tp2 = entry + (tp1 - entry) * InpTP2Mult;
   } else {
      sl = FindNextAbove(entry);
      if (sl == EMPTY_VALUE) sl = entry * 1.01;
      tp1 = FindNextBelow(entry);
      if (tp1 == EMPTY_VALUE) tp1 = entry - (sl - entry);
      tp2 = entry - (entry - tp1) * InpTP2Mult;
   }

   // Single exit at TP2 (full position), with SL at prev pivot
   bool ok = (direction > 0)
      ? trade.Buy(InpLot, _Symbol, ask, sl, tp2, "L_v10")
      : trade.Sell(InpLot, _Symbol, bid, sl, tp2, "S_v10");

   if (ok) {
      g_inPos = true;
      g_posDir = direction;
      g_entryPx = entry;
      g_slPx = sl;
      g_tp1Px = tp1;
      g_tp2Px = tp2;
      g_tp1Reached = false;
      if (PositionSelect(_Symbol)) g_posTicket = PositionGetInteger(POSITION_TICKET);
      if (InpVerbose)
         PrintFormat("%s @%.2f SL=%.2f TP1=%.2f TP2=%.2f", direction>0?"L":"S", entry, sl, tp1, tp2);
   } else {
      Print("Trade failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
   }
}

// Check intermediate pivot (TP1) — when reached, move SL to BE
void ManageBE()
{
   if (!g_inPos || g_tp1Reached || !InpMoveSLtoBE) return;
   double bid = symInfo.Bid();
   double ask = symInfo.Ask();
   bool reached = (g_posDir > 0 && bid >= g_tp1Px) || (g_posDir < 0 && ask <= g_tp1Px);
   if (reached) {
      g_tp1Reached = true;
      // Move SL to entry (breakeven)
      if (trade.PositionModify(_Symbol, g_entryPx, g_tp2Px)) {
         if (InpVerbose) PrintFormat("TP1 reached → SL → BE @ %.2f", g_entryPx);
      }
   }
}

void OnTick()
{
   symInfo.RefreshRates();

   datetime today = iTime(_Symbol, InpPivotTF, 0);
   if (today != g_lastPivotDay) {
      UpdatePivots();
      g_lastPivotDay = today;
   }
   if (g_prevH == 0) {
      UpdatePivots();
      if (g_prevH == 0) return;
   }

   if (!PositionSelect(_Symbol)) {
      g_inPos = false; g_posDir = 0; g_tp1Reached = false;
   } else {
      ManageBE();
   }

   datetime curBarTime = iTime(_Symbol, _Period, 0);
   if (curBarTime == g_lastBarTime) return;
   g_lastBarTime = curBarTime;

   if (!InSession()) return;

   double curHigh = iHigh(_Symbol, _Period, 1);
   double curLow  = iLow(_Symbol, _Period, 1);
   double prevHigh = iHigh(_Symbol, _Period, 2);
   double prevLow  = iLow(_Symbol, _Period, 2);

   bool longSig  = LongSignalOnBar(curHigh, prevHigh);
   bool shortSig = ShortSignalOnBar(curLow, prevLow);

   if (longSig)  OpenPosition(+1);
   if (shortSig) OpenPosition(-1);
}
