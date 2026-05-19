//+------------------------------------------------------------------+
//|                                     DFMO_Centenas_Backtest.mq5   |
//| Backtest: TT entries, avg at $50/$100 + DFMO M5 zone END         |
//| DD-based lot sizing, 4% DD hard stop                             |
//| Close on signal close or DD stop                                 |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Averaging at $50/$100 levels + DFMO M5 zone END confirmations"

#include <Trade\Trade.mqh>

// === GENERAL ===
input ulong    MagicNumber        = 260502;
input int      SlippagePoints     = 30;

// === SIGNAL CSV ===
input string   SignalCsvFile      = "tg_signals_tt_last_2y_ea.csv";
input string   ChannelFilter      = "ALL";
input bool     SignalTimesAreUTC  = false;
input int      SignalTimeOffsetMin = 0;
input double   MaxEntryDriftUSD   = 3.0;

// === FIXED AVERAGING LEVELS ===
input double   Avg1DistUSD        = 50.0;   // 1st average at $50 adverse
input double   Avg2DistUSD        = 100.0;  // 2nd average at $100 adverse

// === DFMO M5 AVERAGING ===
input bool     EnableDFMO         = true;    // Enable DFMO M5 zone END averaging
input int      DFMO_MaxAvgs       = 5;       // Max DFMO-triggered averages
input double   DFMO_MinGapUSD     = 15.0;    // Min gap between DFMO avgs (USD)
input int      DFMO_OB            = 75;      // M5 Overbought level
input int      DFMO_OS            = 25;      // M5 Oversold level
input int      DFMO_Lookback      = 4;       // Zone lookback (bars 2..2+N-1)

// === RISK ===
input double   SLDistanceUSD      = 150.0;   // SL for lot sizing (not placed)
input double   DDTargetPct        = 4.0;     // DD% for lot calculation
input double   DDStopPct          = 4.0;     // DD% hard stop (close all)

// === SESSION FILTER ===
input bool     EnableSessionFilter = false;
input int      SessionStartHour   = 8;
input int      SessionEndHour     = 17;

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
int    signalCount  = 0;
int    signalCursor = 0;
int    activeSignalIdx = -1;

// Basket state
int    bDir          = 0;         // basket direction (1=BUY, -1=SELL)
double bEntryPrice   = 0;        // signal entry price
double bUnitLot      = 0;        // lot per position
double bEntryBalance = 0;        // balance at signal start
int    bId           = 0;        // basket counter
int    bOpenCount    = 0;        // total open positions

// Fixed averaging flags
bool   avg1Opened    = false;
bool   avg2Opened    = false;

// DFMO averaging state
int      dfmoAvgCount     = 0;
double   dfmoLastAvgPrice = 0;     // price of last DFMO avg
datetime dfmoLastBarTime  = 0;     // last M5 bar time checked

// Indicator handles (M5)
int    hStoch_M5 = INVALID_HANDLE;
int    hRSI_M5   = INVALID_HANDLE;

// Stats
int    totalBaskets  = 0;
int    totalWins     = 0;
int    totalLosses   = 0;
double totalProfit   = 0;
double maxDDPct      = 0;
int    totalDFMOAvgs = 0;

// ============================================================
// CSV HELPERS (same as SimpleDD_Backtest)
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
   signalCount  = 0;
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
      row.channel   = ch;
      row.direction  = (StringFind(dir, "BUY") >= 0) ? 1 : -1;
      row.openTime   = ParseTime(oT);
      row.openPrice  = StringToDouble(oP);
      row.closeTime  = ParseTime(cT);
      if(SignalTimeOffsetMin != 0)
      {
         row.openTime += SignalTimeOffsetMin * 60;
         if(row.closeTime > 0) row.closeTime += SignalTimeOffsetMin * 60;
      }
      row.hasClose    = (TrimStr(hC) == "1" && row.closeTime > 0);
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
// PRICE HELPERS
// ============================================================
double Bid() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double Ask() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

// ============================================================
// LOT CALCULATION
// DD sized for 3 known positions: entry, $50 avg, $100 avg
// ============================================================
double CalcUnitLot(double balance)
{
   double D = SLDistanceUSD;
   if(D <= 0) return 0.01;

   // 3 positions: entry at 0, avg1 at Avg1DistUSD, avg2 at Avg2DistUSD
   // At SL (D adverse), each position's loss:
   //   entry: D * lot * 100
   //   avg1:  (D - Avg1DistUSD) * lot * 100
   //   avg2:  (D - Avg2DistUSD) * lot * 100
   // Total = (3*D - Avg1 - Avg2) * lot * 100
   double sumDist = 3.0 * D - Avg1DistUSD - Avg2DistUSD;
   if(sumDist <= 0) sumDist = D;

   double lot = (DDTargetPct / 100.0 * balance) / (100.0 * sumDist);
   lot = NormalizeDouble(lot, 2);
   if(lot < 0.01) lot = 0.01;
   if(lot > 5.0)  lot = 5.0;

   return lot;
}

// ============================================================
// BASKET HELPERS
// ============================================================
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
         + PositionGetDouble(POSITION_COMMISSION) * 2.0;
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
      wSum  += PositionGetDouble(POSITION_PRICE_OPEN) * lots;
      tLots += lots;
   }
   return (tLots > 0) ? NormalizeDouble(wSum / tLots, _Digits) : 0;
}

void CloseAllBasket(string reason)
{
   double profit = GetBasketProfit();
   int positions = CountBasketPositions();
   int dfmoInBasket = dfmoAvgCount;

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
         " pos=", positions, " (fixed:", (avg1Opened?1:0)+(avg2Opened?1:0),
         " dfmo:", dfmoInBasket, ")",
         " lot=", DoubleToString(bUnitLot, 2),
         " W/L=", totalWins, "/", totalLosses,
         " total=$", DoubleToString(totalProfit, 2));

   // Reset basket
   bDir = 0;
   bEntryPrice = 0;
   bUnitLot = 0;
   bOpenCount = 0;
   bEntryBalance = 0;
   activeSignalIdx = -1;
   avg1Opened = false;
   avg2Opened = false;
   dfmoAvgCount = 0;
   dfmoLastAvgPrice = 0;
   dfmoLastBarTime = 0;
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
// DFMO M5 ZONE END DETECTION
// Uses built-in iStochastic(25,4,4) + iRSI(3) on M5
// ============================================================
bool CheckDFMO_M5_ZoneEnd()
{
   if(!EnableDFMO) return false;
   if(dfmoAvgCount >= DFMO_MaxAvgs) return false;

   // Only check once per new M5 bar
   datetime m5Time[];
   if(CopyTime(_Symbol, PERIOD_M5, 1, 1, m5Time) < 1) return false;
   if(m5Time[0] <= dfmoLastBarTime) return false;
   dfmoLastBarTime = m5Time[0];   // Mark as checked

   // Get StochK (buffer 0) and RSI for bars 1..5+1 (need lookback)
   int need = DFMO_Lookback + 2;   // bars 1..lookback+1
   double stochK[], rsi[];
   ArraySetAsSeries(stochK, true);
   ArraySetAsSeries(rsi, true);

   if(CopyBuffer(hStoch_M5, 0, 0, need + 1, stochK) < need + 1) return false;
   if(CopyBuffer(hRSI_M5,   0, 0, need + 1, rsi)    < need + 1) return false;

   // Bar 1 = last closed M5 candle
   // Check zone END: any of bars 2..(2+Lookback-1) in zone, bar 1 NOT in zone
   bool bar1InZone = false;
   bool anyRecentInZone = false;

   if(bDir == 1)  // BUY signal -> need OS zone END (was oversold, now leaving)
   {
      bar1InZone = (stochK[1] < DFMO_OS && rsi[1] < DFMO_OS);
      for(int b = 2; b <= 1 + DFMO_Lookback; b++)
      {
         if(stochK[b] < DFMO_OS && rsi[b] < DFMO_OS)
         {
            anyRecentInZone = true;
            break;
         }
      }
   }
   else if(bDir == -1)  // SELL signal -> need OB zone END (was overbought, now leaving)
   {
      bar1InZone = (stochK[1] > DFMO_OB && rsi[1] > DFMO_OB);
      for(int b = 2; b <= 1 + DFMO_Lookback; b++)
      {
         if(stochK[b] > DFMO_OB && rsi[b] > DFMO_OB)
         {
            anyRecentInZone = true;
            break;
         }
      }
   }

   // Zone END = recent bar(s) were in zone, bar 1 is NOT
   if(anyRecentInZone && !bar1InZone)
   {
      Print(">>> DFMO M5 ZONE END: dir=", bDir,
            " StochK[1]=", DoubleToString(stochK[1], 1),
            " RSI[1]=", DoubleToString(rsi[1], 1),
            " StochK[2]=", DoubleToString(stochK[2], 1),
            " RSI[2]=", DoubleToString(rsi[2], 1));
      return true;
   }

   return false;
}

// ============================================================
// MANAGE AVERAGING
// ============================================================
void ManageAveraging(double currentPrice)
{
   // Distance from entry in adverse direction
   double dist = (bDir == 1) ? (bEntryPrice - currentPrice) : (currentPrice - bEntryPrice);

   // === FIXED LEVELS: $50 and $100 ===
   if(!avg1Opened && dist >= Avg1DistUSD)
   {
      if(OpenAvgPosition("AVG50"))
         avg1Opened = true;
   }

   if(!avg2Opened && dist >= Avg2DistUSD)
   {
      if(OpenAvgPosition("AVG100"))
         avg2Opened = true;
   }

   // === DFMO M5 ZONE END ===
   // Only when price is adverse (losing)
   if(dist > 0 && EnableDFMO && dfmoAvgCount < DFMO_MaxAvgs)
   {
      // Min gap from last DFMO avg
      if(dfmoLastAvgPrice > 0)
      {
         double gapFromLast = MathAbs(currentPrice - dfmoLastAvgPrice);
         if(gapFromLast < DFMO_MinGapUSD) return;
      }

      // Also min gap from fixed levels (don't stack on top of $50/$100)
      if(avg1Opened && MathAbs(dist - Avg1DistUSD) < DFMO_MinGapUSD) return;
      if(avg2Opened && MathAbs(dist - Avg2DistUSD) < DFMO_MinGapUSD) return;

      if(CheckDFMO_M5_ZoneEnd())
      {
         string cm = "DFMO" + IntegerToString(dfmoAvgCount + 1);
         if(OpenAvgPosition(cm))
         {
            dfmoAvgCount++;
            totalDFMOAvgs++;
            dfmoLastAvgPrice = currentPrice;
            Print(">>> DFMO AVG #", dfmoAvgCount, "/", DFMO_MaxAvgs,
                  " @ $", DoubleToString(currentPrice, 2),
                  " dist=$", DoubleToString(dist, 1));
         }
      }
   }
}

bool OpenAvgPosition(string label)
{
   string cm = label + "_" + IntegerToString(bId);
   bool ok = (bDir == 1) ? trade.Buy(bUnitLot, _Symbol, 0, 0, 0, cm)
                          : trade.Sell(bUnitLot, _Symbol, 0, 0, 0, cm);

   if(ok && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
   {
      bOpenCount++;
      double fill = trade.ResultPrice();
      double dist = (bDir == 1) ? (bEntryPrice - fill) : (fill - bEntryPrice);
      Print(">>> ", label, " @ $", DoubleToString(fill, 2),
            " dist=$", DoubleToString(dist, 1),
            " lot=", DoubleToString(bUnitLot, 2),
            " total=", bOpenCount);
      return true;
   }
   else
   {
      Print("!!! ", label, " FAILED: rc=", trade.ResultRetcode());
      return false;
   }
}

// ============================================================
// DD STOP
// ============================================================
bool CheckDDStop()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance <= 0) return false;

   double ddPct = (balance - equity) / balance * 100.0;
   if(ddPct > maxDDPct) maxDDPct = ddPct;

   if(ddPct >= DDStopPct)
   {
      Print("!!! DD STOP: ", DoubleToString(ddPct, 2), "% >= ", DoubleToString(DDStopPct, 1), "%");
      CloseAllBasket("DD_STOP");
      return true;
   }
   return false;
}

// ============================================================
// OPEN NEW BASKET
// ============================================================
void OpenNewBasket(SignalRow &sig, int sigIdx, double marketPrice)
{
   bDir          = sig.direction;
   bEntryPrice   = marketPrice;
   bEntryBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   activeSignalIdx = sigIdx;
   bId++;

   // Reset averaging state
   avg1Opened       = false;
   avg2Opened       = false;
   dfmoAvgCount     = 0;
   dfmoLastAvgPrice = 0;
   dfmoLastBarTime  = 0;

   // Calculate lot (sized for 3 positions at SL distance)
   bUnitLot = CalcUnitLot(bEntryBalance);

   // Open first position (entry)
   string cm = "ENTRY_" + IntegerToString(bId);
   bool ok = (bDir == 1) ? trade.Buy(bUnitLot, _Symbol, 0, 0, 0, cm)
                          : trade.Sell(bUnitLot, _Symbol, 0, 0, 0, cm);

   if(ok && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
   {
      bOpenCount = 1;
      double fill = trade.ResultPrice();
      bEntryPrice = fill;   // Use actual fill price

      Print("=== BASKET #", bId, " OPEN === ", (bDir == 1 ? "BUY" : "SELL"),
            " @ $", DoubleToString(fill, 2),
            " lot=", DoubleToString(bUnitLot, 2),
            " SLCalc=$", DoubleToString(SLDistanceUSD, 0),
            " DDTarget=", DoubleToString(DDTargetPct, 1), "%",
            " bal=$", DoubleToString(bEntryBalance, 0));
   }
   else
   {
      Print("!!! BASKET OPEN FAILED: rc=", trade.ResultRetcode());
      bDir = 0;
   }
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
      Print("!!! No signals loaded");
      return INIT_FAILED;
   }

   // Create DFMO indicator handles on M5
   if(EnableDFMO)
   {
      // Stochastic(25, 4, 4) on M5
      hStoch_M5 = iStochastic(_Symbol, PERIOD_M5, 25, 4, 4, MODE_SMA, STO_LOWHIGH);
      if(hStoch_M5 == INVALID_HANDLE)
      {
         Print("!!! Failed to create M5 Stochastic handle");
         return INIT_FAILED;
      }

      // RSI(3) on M5
      hRSI_M5 = iRSI(_Symbol, PERIOD_M5, 3, PRICE_CLOSE);
      if(hRSI_M5 == INVALID_HANDLE)
      {
         Print("!!! Failed to create M5 RSI handle");
         return INIT_FAILED;
      }
      Print("DFMO M5 handles created: Stoch(25,4,4) + RSI(3) | OB=", DFMO_OB, " OS=", DFMO_OS);
   }

   Print("=== DFMO Centenas Backtest ===",
         " Avg1=$", Avg1DistUSD, " Avg2=$", Avg2DistUSD,
         " DFMO=", EnableDFMO, " MaxDFMO=", DFMO_MaxAvgs,
         " SLCalc=$", SLDistanceUSD, " DD=", DDTargetPct, "%",
         " DDStop=", DDStopPct, "%");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(bDir != 0) CloseAllBasket("DEINIT");

   if(hStoch_M5 != INVALID_HANDLE) { IndicatorRelease(hStoch_M5); hStoch_M5 = INVALID_HANDLE; }
   if(hRSI_M5   != INVALID_HANDLE) { IndicatorRelease(hRSI_M5);  hRSI_M5   = INVALID_HANDLE; }

   Print("=== FINAL STATS ===",
         " baskets=", totalBaskets,
         " W=", totalWins, " L=", totalLosses,
         " profit=$", DoubleToString(totalProfit, 2),
         " maxDD=", DoubleToString(maxDDPct, 2), "%",
         " dfmoAvgs=", totalDFMOAvgs);
}

// ============================================================
// ONTICK
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
         double marketPrice = (sig.direction == 1) ? ask : bid;
         double drift = MathAbs(marketPrice - sig.openPrice);

         if(drift <= MaxEntryDriftUSD || MaxEntryDriftUSD <= 0)
         {
            if(!EnableSessionFilter || IsSessionAllowed())
               OpenNewBasket(sig, signalCursor, marketPrice);
            else
               Print("Signal #", signalCursor, " skipped (session)");
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
            if(signalCursor <= activeSignalIdx) signalCursor = activeSignalIdx + 1;
            return;
         }
      }

      // 2b. Check DD hard stop
      if(CheckDDStop())
      {
         if(signalCursor <= activeSignalIdx) signalCursor = activeSignalIdx + 1;
         return;
      }

      // 2c. Averaging (fixed levels + DFMO M5)
      double currentPrice = (bDir == 1) ? bid : ask;
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

   // === 3. SKIP PAST SIGNALS ===
   while(bDir == 0 && signalCursor < signalCount && now > signals[signalCursor].openTime)
      signalCursor++;
}

// ============================================================
// END
// ============================================================
