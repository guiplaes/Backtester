//+------------------------------------------------------------------+
//| PivotExtHybrid_v9.mq5                                            |
//| Replica MQL5 de l'estrategia TV v9                                |
//| - Multi-level pivot crosses (R1/R2/R3/PP/S1/S2/S3)                |
//| - Stop & Reverse on opposite signal                               |
//| - TP1 = next pivot toward (50% qty)                               |
//| - TP2 = entry + (TP1 - entry) × multiplier (50% qty)              |
//| - SL = next pivot opposite                                        |
//| - SL → BE after TP1 hits                                          |
//| - Real broker bid/ask (no simulation needed)                      |
//+------------------------------------------------------------------+
#property copyright "Pivot Ext Hybrid v9"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//── INPUTS ─────────────────────────────────────────────────────────
input group "── Strategy ──"
input double InpTP2Mult       = 2.0;   // TP2 = entry + (TP1-entry) × this
input bool   InpMoveSLtoBE    = true;  // Move SL to breakeven after TP1 hit
input ENUM_TIMEFRAMES InpPivotTF = PERIOD_D1; // Pivot timeframe

input group "── Risk ──"
input double InpLot           = 0.10;  // Lot size per trade
input int    InpMagic         = 990901;// Magic number

input group "── Filters ──"
input bool   InpUseSession    = false; // Filter by UTC session
input int    InpSessionStartH = 7;     // Session start hour UTC
input int    InpSessionEndH   = 21;    // Session end hour UTC

input group "── Logging ──"
input bool   InpVerbose       = false; // Print debug logs

//── GLOBALS ────────────────────────────────────────────────────────
CTrade        trade;
CPositionInfo posInfo;
CSymbolInfo   symInfo;

double g_pp, g_r1, g_s1, g_r2, g_s2, g_r3, g_s3;
double g_prevH = 0, g_prevL = 0, g_prevC = 0;
datetime g_lastPivotDay = 0;
datetime g_lastBarTime = 0;

// Position state (for our trade)
bool   g_inPos = false;
int    g_posDir = 0;          // +1 long, -1 short, 0 none
double g_entryPx = 0;
double g_slPx = 0;
double g_tp1Px = 0;
double g_tp2Px = 0;
bool   g_tp1Hit = false;
ulong  g_posTicket = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetMarginMode();
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(20);

   symInfo.Name(_Symbol);
   symInfo.RefreshRates();

   Print("PivotExtHybrid v9 initialized for ", _Symbol, " on ", EnumToString(_Period));
   Print("Spread now: ", symInfo.Spread(), " points = ", symInfo.Spread() * _Point, " USD");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {}

//+------------------------------------------------------------------+
// Compute classic daily pivots from previous period H/L/C            |
//+------------------------------------------------------------------+
void UpdatePivots()
{
   // Get previous period's H/L/C
   double prevH[1], prevL[1], prevC[1];
   if (CopyHigh(_Symbol, InpPivotTF, 1, 1, prevH) <= 0) return;
   if (CopyLow (_Symbol, InpPivotTF, 1, 1, prevL) <= 0) return;
   if (CopyClose(_Symbol, InpPivotTF, 1, 1, prevC) <= 0) return;

   g_prevH = prevH[0];
   g_prevL = prevL[0];
   g_prevC = prevC[0];

   g_pp = (g_prevH + g_prevL + g_prevC) / 3.0;
   g_r1 = 2*g_pp - g_prevL;
   g_s1 = 2*g_pp - g_prevH;
   g_r2 = g_pp + (g_prevH - g_prevL);
   g_s2 = g_pp - (g_prevH - g_prevL);
   g_r3 = g_prevH + 2*(g_pp - g_prevL);
   g_s3 = g_prevL - 2*(g_prevH - g_pp);

   if (InpVerbose)
      PrintFormat("Pivots updated: PP=%.2f R1=%.2f R2=%.2f R3=%.2f S1=%.2f S2=%.2f S3=%.2f",
                  g_pp, g_r1, g_r2, g_r3, g_s1, g_s2, g_s3);
}

//+------------------------------------------------------------------+
// Find next pivot above / below                                      |
//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
// Detect crossings — using high/low of last closed bar vs prior bar   |
//+------------------------------------------------------------------+
bool LongSignalOnBar(double curHigh, double prevHigh)
{
   // ASK = bid + spread → use bid + spread for actual buy trigger
   double spread = symInfo.Spread() * _Point;
   double askH = curHigh + spread; // approximation: high is bid, add spread
   double askHp = prevHigh + spread;
   double levels[7] = {g_r1, g_r2, g_r3, g_pp, g_s1, g_s2, g_s3};
   for (int i = 0; i < 7; i++)
      if (askH > levels[i] && askHp <= levels[i])
         return true;
   return false;
}

bool ShortSignalOnBar(double curLow, double prevLow)
{
   // BID = ask - spread, but the low IS the bid-low, so use as-is
   double levels[7] = {g_s1, g_s2, g_s3, g_pp, g_r1, g_r2, g_r3};
   for (int i = 0; i < 7; i++)
      if (curLow < levels[i] && prevLow >= levels[i])
         return true;
   return false;
}

//+------------------------------------------------------------------+
// Session filter                                                     |
//+------------------------------------------------------------------+
bool InSession()
{
   if (!InpUseSession) return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return (dt.hour >= InpSessionStartH && dt.hour < InpSessionEndH);
}

//+------------------------------------------------------------------+
// Manage existing position: check TP1, move SL to BE, set TP2/SL      |
//+------------------------------------------------------------------+
void ManageOpenPosition()
{
   if (!g_inPos) return;
   if (!posInfo.SelectByTicket(g_posTicket)) {
      g_inPos = false;
      g_posDir = 0;
      return;
   }
   double curVol = posInfo.Volume();
   if (curVol == 0) {
      g_inPos = false;
      g_posDir = 0;
      return;
   }

   double bid = symInfo.Bid();
   double ask = symInfo.Ask();

   // Check TP1 — close 50% if hit
   if (!g_tp1Hit) {
      bool hit = (g_posDir > 0 && bid >= g_tp1Px) || (g_posDir < 0 && ask <= g_tp1Px);
      if (hit) {
         double closeVol = NormalizeDouble(curVol * 0.5, 2);
         if (closeVol >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN)) {
            if (trade.PositionClosePartial(g_posTicket, closeVol)) {
               g_tp1Hit = true;
               if (InpVerbose) Print("TP1 hit @", DoubleToString(g_posDir>0?bid:ask, _Digits));
               // Move SL to BE for remaining 50%
               if (InpMoveSLtoBE) {
                  double newSL = g_entryPx;
                  trade.PositionModify(g_posTicket, newSL, g_tp2Px);
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
// Open new position (with stop & reverse if needed)                  |
//+------------------------------------------------------------------+
void OpenPosition(int direction)
{
   // Close existing position if opposite direction
   if (g_inPos && g_posDir != direction) {
      if (trade.PositionClose(g_posTicket)) {
         g_inPos = false;
         g_posDir = 0;
         g_tp1Hit = false;
      }
   }
   if (g_inPos) return; // Same direction → don't reopen

   double bid = symInfo.Bid();
   double ask = symInfo.Ask();
   double entry = (direction > 0) ? ask : bid;

   double sl, tp1;
   if (direction > 0) {
      sl = FindNextBelow(entry);
      if (sl == EMPTY_VALUE) sl = entry * 0.99;
      tp1 = FindNextAbove(entry);
      if (tp1 == EMPTY_VALUE) tp1 = entry + (entry - sl);
   } else {
      sl = FindNextAbove(entry);
      if (sl == EMPTY_VALUE) sl = entry * 1.01;
      tp1 = FindNextBelow(entry);
      if (tp1 == EMPTY_VALUE) tp1 = entry - (sl - entry);
   }

   double tp2 = (direction > 0)
      ? entry + (tp1 - entry) * InpTP2Mult
      : entry - (entry - tp1) * InpTP2Mult;

   // Place order
   bool ok = false;
   if (direction > 0)
      ok = trade.Buy(InpLot, _Symbol, ask, sl, tp2, "L_v9");
   else
      ok = trade.Sell(InpLot, _Symbol, bid, sl, tp2, "S_v9");

   if (ok) {
      g_inPos = true;
      g_posDir = direction;
      g_entryPx = entry;
      g_slPx = sl;
      g_tp1Px = tp1;
      g_tp2Px = tp2;
      g_tp1Hit = false;
      g_posTicket = trade.ResultDeal();
      // Use position ticket instead of deal ticket
      if (PositionSelect(_Symbol)) g_posTicket = PositionGetInteger(POSITION_TICKET);

      if (InpVerbose)
         PrintFormat("%s opened @%.2f SL=%.2f TP1=%.2f TP2=%.2f",
                     direction>0?"LONG":"SHORT", entry, sl, tp1, tp2);
   } else {
      Print("Trade failed: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
// OnTick — called on every tick                                       |
//+------------------------------------------------------------------+
void OnTick()
{
   symInfo.RefreshRates();

   // Detect new daily bar → update pivots
   datetime today = iTime(_Symbol, InpPivotTF, 0);
   if (today != g_lastPivotDay) {
      UpdatePivots();
      g_lastPivotDay = today;
   }

   if (g_prevH == 0) {
      UpdatePivots();
      if (g_prevH == 0) return; // No data yet
   }

   // Manage open position on every tick
   if (g_inPos) {
      // Refresh position info — TP2 and SL fire automatically via OrderSend
      if (!PositionSelect(_Symbol)) {
         g_inPos = false;
         g_posDir = 0;
         g_tp1Hit = false;
      } else {
         ManageOpenPosition();
      }
   }

   // Detect signals only on NEW closed bar
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

//+------------------------------------------------------------------+
