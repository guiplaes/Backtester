//+------------------------------------------------------------------+
//|                                          SimpleDD_Backtest.mq5   |
//| Backtest: averaging every $5, lot sized for 3.5% DD at SL        |
//| SL = next strong level (>=30 USD) + 15 USD                       |
//| Close only on TG signal close                                    |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Simple DD-based averaging backtest. Entry from CSV signals, avg every $5, lot for 3.5% DD at structural SL."

#include <Trade\Trade.mqh>

// === GENERAL ===
input ulong    MagicNumber       = 260501;
input int      SlippagePoints    = 30;

// === SIGNAL CSV ===
input string   SignalCsvFile     = "tg_signals_tt_last_2y_ea.csv";
input string   ChannelFilter     = "ALL";
input bool     SignalTimesAreUTC = false;
input int      SignalTimeOffsetMin = 0;
input double   MaxEntryDriftUSD  = 3.0;   // max price drift from signal to open

// === STRATEGY ===
input double   AvgStepUSD        = 5.0;    // averaging every $5
input double   SLDistanceUSD     = 40.0;   // SL fix a 40 USD de l'entrada
input double   DDTargetPct       = 3.5;    // target DD% at SL with all positions

// === SESSION FILTER ===
input bool     EnableSessionFilter = false;
input int      SessionStartHour  = 8;
input int      SessionEndHour    = 17;

// ============================================================
// GLOBALS
// ============================================================
CTrade trade;

struct SignalRow
{
   string   channel;
   int      direction;   // 1=BUY, -1=SELL
   datetime openTime;
   double   openPrice;
   datetime closeTime;
   bool     hasClose;
   string   closeReason;
};

SignalRow signals[];
int    signalCount = 0;
int    signalCursor = 0;
int    activeSignalIdx = -1;

// Basket state
int    bDir = 0;              // basket direction
double bEntryPrice = 0;       // signal entry price
double bSLPrice = 0;          // stop loss price
double bUnitLot = 0;          // lot per position
int    bMaxPositions = 0;     // max positions to open
int    bOpenCount = 0;        // currently open averaging count
double bEntryBalance = 0;     // balance at signal start
int    bId = 0;               // basket counter

// Stats
int    totalBaskets = 0;
int    totalWins = 0;
int    totalLosses = 0;
double totalProfit = 0;
double maxDDPct = 0;

// ============================================================
// CSV HELPERS
// ============================================================
string TrimStr(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

datetime ParseTime(string s)
{
   s = TrimStr(s);
   if(StringLen(s) < 19) return 0;
   string t = StringSubstr(s, 0, 19);
   StringReplace(t, "T", " ");
   return StringToTime(t);
}

bool ChannelAllowed(string ch)
{
   string f = ChannelFilter;
   StringToUpper(f);
   ch = TrimStr(ch);
   StringToUpper(ch);
   if(f == "ALL" || f == "*") return true;
   return (ch == f);
}

datetime SignalClock()
{
   return SignalTimesAreUTC ? TimeGMT() : TimeCurrent();
}

// ============================================================
// LOAD CSV
// ============================================================
bool LoadSignalCsv()
{
   ArrayResize(signals, 0);
   signalCount = 0;
   signalCursor = 0;
   activeSignalIdx = -1;

   int h = FileOpen(SignalCsvFile, FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI, ',');
   if(h == INVALID_HANDLE)
   {
      Print("!!! Cannot open CSV: ", SignalCsvFile, " err=", GetLastError());
      return false;
   }

   // Skip header
   if(!FileIsEnding(h))
   {
      for(int i = 0; i < 9; i++) FileReadString(h);
   }

   while(!FileIsEnding(h))
   {
      string ch   = FileReadString(h);
      string dir  = FileReadString(h);
      string oT   = FileReadString(h);
      string oP   = FileReadString(h);
      string cT   = FileReadString(h);
      string hC   = FileReadString(h);
      string cR   = FileReadString(h);
      string oM   = FileReadString(h);
      string cM   = FileReadString(h);

      if(StringLen(ch) == 0 || StringLen(dir) == 0 || StringLen(oT) == 0) continue;
      ch = TrimStr(ch);
      if(!ChannelAllowed(ch)) continue;

      SignalRow row;
      row.channel = ch;
      row.direction = (StringFind(dir, "BUY") >= 0) ? 1 : -1;
      row.openTime = ParseTime(oT);
      row.openPrice = StringToDouble(oP);
      row.closeTime = ParseTime(cT);
      if(SignalTimeOffsetMin != 0)
      {
         row.openTime += SignalTimeOffsetMin * 60;
         if(row.closeTime > 0) row.closeTime += SignalTimeOffsetMin * 60;
      }
      row.hasClose = (TrimStr(hC) == "1" && row.closeTime > 0);
      row.closeReason = TrimStr(cR);

      if(row.openTime <= 0) continue;

      int sz = signalCount + 1;
      ArrayResize(signals, sz);
      signals[signalCount] = row;
      signalCount = sz;
   }
   FileClose(h);
   Print("CSV loaded: ", signalCount, " signals from ", SignalCsvFile);
   return (signalCount > 0);
}

// ============================================================
// STRATEGY HELPERS
// ============================================================
double Bid() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double Ask() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

// Calculate unit lot so that max DD at SL = DDTargetPct% of balance
// SL fix a SLDistanceUSD de l'entrada
// Positions: entry + averaging every AvgStepUSD fins a SL-AvgStep
double CalcUnitLot(double balance, int &outMaxPos)
{
   double D = SLDistanceUSD;
   if(D <= 0) { outMaxPos = 1; return 0.01; }

   // N positions: at dist 0, AvgStep, 2*AvgStep, ..., up to < D
   int N = (int)MathFloor(D / AvgStepUSD);
   if(N < 1) N = 1;
   if(N > 200) N = 200;
   outMaxPos = N;

   // Total loss at SL: sum of (D - i*AvgStep) for i=0..N-1
   // = N*D - AvgStep * N*(N-1)/2
   double sumDist = (double)N * D - AvgStepUSD * (double)N * (double)(N - 1) / 2.0;
   if(sumDist <= 0) sumDist = D;

   // lot * 100 * sumDist = DDTargetPct/100 * balance
   double lot = (DDTargetPct / 100.0 * balance) / (100.0 * sumDist);
   lot = NormalizeDouble(lot, 2);
   if(lot < 0.01) lot = 0.01;
   if(lot > 5.0) lot = 5.0;

   return lot;
}

int CountBasketPositions()
{
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetTicket(i) > 0 &&
         PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == (long)MagicNumber)
         c++;
   }
   return c;
}

double GetBasketProfit()
{
   double p = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      p += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP)
         + PositionGetDouble(POSITION_COMMISSION) * 2.0; // commission is per-deal, x2 for round-trip estimate
   }
   return p;
}

double GetWeightedEntry()
{
   double wSum = 0, tLots = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
      double lots = PositionGetDouble(POSITION_VOLUME);
      wSum += PositionGetDouble(POSITION_PRICE_OPEN) * lots;
      tLots += lots;
   }
   return (tLots > 0) ? NormalizeDouble(wSum / tLots, _Digits) : 0;
}

void CloseAllBasket(string reason)
{
   double profit = GetBasketProfit();
   trade.SetAsyncMode(true);
   for(int retry = 0; retry < 3; retry++)
   {
      int closed = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tkt = PositionGetTicket(i);
         if(tkt == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != (long)MagicNumber) continue;
         trade.PositionClose(tkt);
         closed++;
      }
      if(closed == 0) break;
      Sleep(200);
   }
   trade.SetAsyncMode(false);

   totalBaskets++;
   totalProfit += profit;
   if(profit >= 0) totalWins++;
   else totalLosses++;

   Print("=== BASKET #", bId, " CLOSED [", reason, "] profit=$", DoubleToString(profit, 2),
         " positions=", bOpenCount, "/", bMaxPositions, " lot=", DoubleToString(bUnitLot, 2),
         " SL=$", DoubleToString(bSLPrice, 0),
         " W/L=", totalWins, "/", totalLosses, " total=$", DoubleToString(totalProfit, 2));

   bDir = 0;
   bEntryPrice = 0;
   bSLPrice = 0;
   bUnitLot = 0;
   bMaxPositions = 0;
   bOpenCount = 0;
   activeSignalIdx = -1;
}

// ============================================================
// SESSION FILTER
// ============================================================
bool IsSessionAllowed()
{
   if(!EnableSessionFilter) return true;
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);
   if(SessionStartHour < SessionEndHour)
      return (tm.hour >= SessionStartHour && tm.hour < SessionEndHour);
   return (tm.hour >= SessionStartHour || tm.hour < SessionEndHour);
}

// ============================================================
// ONINIT
// ============================================================
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   if(!LoadSignalCsv())
   {
      Print("!!! No signals loaded — stopping");
      return INIT_FAILED;
   }
   Print("=== SimpleDD Backtest === avg=$", AvgStepUSD,
         " SL=$", SLDistanceUSD,
         " ddTarget=", DDTargetPct, "%");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   // Close any remaining basket
   if(bDir != 0) CloseAllBasket("DEINIT");

   Print("=== FINAL STATS === baskets=", totalBaskets,
         " W=", totalWins, " L=", totalLosses,
         " profit=$", DoubleToString(totalProfit, 2),
         " maxDD=", DoubleToString(maxDDPct, 2), "%");
}

// ============================================================
// ONTICK — main loop
// ============================================================
void OnTick()
{
   datetime now = SignalClock();
   double bid = Bid(), ask = Ask();
   if(bid <= 0 || ask <= 0) return;

   // === 1. CHECK FOR NEW SIGNAL ===
   if(bDir == 0 && signalCursor < signalCount)
   {
      SignalRow sig = signals[signalCursor];
      if(now >= sig.openTime)
      {
         // Check drift
         double marketPrice = (sig.direction == 1) ? ask : bid;
         double drift = MathAbs(marketPrice - sig.openPrice);

         if(drift <= MaxEntryDriftUSD || MaxEntryDriftUSD <= 0)
         {
            if(!EnableSessionFilter || IsSessionAllowed())
               OpenNewBasket(sig, signalCursor, marketPrice);
            else
               Print("Signal #", signalCursor, " skipped (session filter)");
         }
         else
         {
            Print("Signal #", signalCursor, " skipped (drift=$", DoubleToString(drift, 1), ")");
         }
         signalCursor++;
      }
   }

   // === 2. MANAGE ACTIVE BASKET ===
   if(bDir != 0)
   {
      // 2a. Check signal close time
      if(activeSignalIdx >= 0 && activeSignalIdx < signalCount)
      {
         SignalRow sig = signals[activeSignalIdx];
         if(sig.hasClose && now >= sig.closeTime)
         {
            CloseAllBasket("SIGNAL_CLOSE");
            // Advance cursor past this signal
            if(signalCursor <= activeSignalIdx) signalCursor = activeSignalIdx + 1;
            return;
         }
      }

      // 2b. Check SL hit
      double currentPrice = (bDir == 1) ? bid : ask;
      bool slHit = false;
      if(bDir == 1 && currentPrice <= bSLPrice) slHit = true;
      if(bDir == -1 && currentPrice >= bSLPrice) slHit = true;

      if(slHit)
      {
         CloseAllBasket("SL_HIT");
         if(signalCursor <= activeSignalIdx) signalCursor = activeSignalIdx + 1;
         return;
      }

      // 2c. Averaging — open next position if price reached next $5 level
      ManageAveraging(currentPrice);

      // 2d. Track max DD
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
      if(balance > 0)
      {
         double ddPct = (balance - equity) / balance * 100.0;
         if(ddPct > maxDDPct) maxDDPct = ddPct;
      }
   }

   // === 3. SKIP PAST SIGNALS (if no basket and signal already passed) ===
   while(bDir == 0 && signalCursor < signalCount && now > signals[signalCursor].openTime)
      signalCursor++;
}

// ============================================================
// OPEN NEW BASKET
// ============================================================
void OpenNewBasket(SignalRow &sig, int sigIdx, double marketPrice)
{
   bDir = sig.direction;
   bEntryPrice = marketPrice;
   bEntryBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   activeSignalIdx = sigIdx;
   bId++;

   // SL fix a SLDistanceUSD
   if(bDir == 1)
      bSLPrice = NormalizeDouble(bEntryPrice - SLDistanceUSD, _Digits);
   else
      bSLPrice = NormalizeDouble(bEntryPrice + SLDistanceUSD, _Digits);

   // Calculate lot
   bUnitLot = CalcUnitLot(bEntryBalance, bMaxPositions);

   // Open first position (entry)
   string cm = "E_" + IntegerToString(bId);
   bool ok = (bDir == 1) ? trade.Buy(bUnitLot, _Symbol, 0, 0, 0, cm)
                         : trade.Sell(bUnitLot, _Symbol, 0, 0, 0, cm);

   if(ok && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
   {
      bOpenCount = 1;
      double fill = trade.ResultPrice();
      Print("=== BASKET #", bId, " OPEN === ", (bDir == 1 ? "BUY" : "SELL"),
            " @ $", DoubleToString(fill, 2),
            " SL=$", DoubleToString(bSLPrice, 2),
            " (", DoubleToString(SLDistanceUSD, 0), " USD)",
            " lot=", DoubleToString(bUnitLot, 2),
            " maxPos=", bMaxPositions,
            " bal=$", DoubleToString(bEntryBalance, 0));
   }
   else
   {
      Print("!!! BASKET OPEN FAILED: rc=", trade.ResultRetcode());
      bDir = 0;
   }
}

// ============================================================
// MANAGE AVERAGING — every $5
// ============================================================
void ManageAveraging(double currentPrice)
{
   if(bOpenCount >= bMaxPositions) return;

   // Distance from entry in adverse direction
   double dist = (bDir == 1) ? (bEntryPrice - currentPrice) : (currentPrice - bEntryPrice);
   if(dist <= 0) return;

   // Next averaging level
   int nextIdx = bOpenCount;  // 0=entry (already open), 1=first avg, etc.
   double requiredDist = nextIdx * AvgStepUSD;

   if(dist < requiredDist) return;

   // Open averaging position
   string cm = "A" + IntegerToString(nextIdx) + "_" + IntegerToString(bId);
   bool ok = (bDir == 1) ? trade.Buy(bUnitLot, _Symbol, 0, 0, 0, cm)
                         : trade.Sell(bUnitLot, _Symbol, 0, 0, 0, cm);

   if(ok && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
   {
      bOpenCount++;
      double fill = trade.ResultPrice();
      Print(">>> AVG #", bOpenCount, "/", bMaxPositions,
            " @ $", DoubleToString(fill, 2),
            " dist=$", DoubleToString(dist, 1),
            " lot=", DoubleToString(bUnitLot, 2));
   }
}

// ============================================================
// END
// ============================================================
