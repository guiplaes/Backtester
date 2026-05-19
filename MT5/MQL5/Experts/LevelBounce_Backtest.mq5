//+------------------------------------------------------------------+
//|                                       LevelBounce_Backtest.mq5   |
//| Bounce at $50/$100 levels: counter-trend entry at $1 from level  |
//| TP $10, SL $8, cooldown $50 before level re-arms                 |
//| v2.0 — Full analytics: hour, session, dow, level type, CSV       |
//+------------------------------------------------------------------+
#property strict
#property version   "2.00"
#property description "Mean-reversion at round $50 levels — Full Analytics"

#include <Trade\Trade.mqh>

// === GENERAL ===
input ulong    MagicNumber     = 270401;
input int      SlippagePoints  = 30;

// === STRATEGY ===
input double   LotSize         = 0.04;    // Lot per trade
input double   LevelStepUSD    = 50.0;    // Level spacing ($50 = includes $100)
input double   EntryDistUSD    = 1.0;     // Entry at $1 from level
input double   EntryTolUSD     = 0.50;    // Tolerance (zone = 0.5 to 1.5)
input double   TPUSD           = 10.0;    // Take Profit in USD
input double   SLUSD           = 8.0;     // Stop Loss in USD
input double   CooldownUSD     = 50.0;    // Min distance before level re-arms
input int      MaxOpenTrades   = 10;      // Max simultaneous positions

// === SESSION FILTER ===
input bool     EnableSession   = false;
input int      SessionStart    = 8;       // Start hour (server time)
input int      SessionEnd      = 17;      // End hour (server time)

// === ANALYTICS ===
input bool     WriteCSV        = true;    // Write trades CSV file
input string   CSVFileName     = "LevelBounce_trades.csv";

// ============================================================
// TRADE RECORD — stores everything about each trade
// ============================================================
struct TradeRecord
{
   ulong    ticket;
   int      direction;      // +1 = BUY, -1 = SELL
   double   entryPrice;
   double   slPrice;
   double   tpPrice;
   double   level;
   bool     isCentena;      // $100 level vs $50
   datetime openTime;
   int      openHour;
   int      openDow;        // 0=Sun...6=Sat
   int      openMonth;
   datetime closeTime;
   double   closePrice;
   double   profit;
   double   mfe;            // Max Favorable Excursion (USD price move)
   double   mae;            // Max Adverse Excursion (USD price move)
   double   holdingSec;
   bool     isTP;           // closed by TP
   bool     isSL;           // closed by SL
   bool     closed;
};

// ============================================================
// GLOBALS
// ============================================================
CTrade trade;

#define CD_MAX 200
double cdLevels[CD_MAX];
int    cdCount = 0;

#define MAX_TRADES 5000
TradeRecord trades[MAX_TRADES];
int tradeCount = 0;

// Live tracking (open positions)
#define MAX_LIVE 50
struct LivePos
{
   ulong    ticket;
   int      idx;        // index into trades[]
   double   mfe;
   double   mae;
};
LivePos livePos[MAX_LIVE];
int     liveCount = 0;

// Global stats
double peakBalance  = 0;
double maxDDPct     = 0;
double maxDDUSD     = 0;

// ============================================================
// COOLDOWN MANAGEMENT
// ============================================================
bool IsOnCooldown(double level)
{
   for(int i = 0; i < cdCount; i++)
      if(MathAbs(cdLevels[i] - level) < 1.0) return true;
   return false;
}

void AddCooldown(double level)
{
   if(cdCount < CD_MAX)
   {
      cdLevels[cdCount] = NormalizeDouble(level, 0);
      cdCount++;
   }
}

void CleanCooldowns(double price)
{
   for(int i = cdCount - 1; i >= 0; i--)
   {
      if(MathAbs(price - cdLevels[i]) >= CooldownUSD)
      {
         cdLevels[i] = cdLevels[cdCount - 1];
         cdCount--;
      }
   }
}

// ============================================================
// HELPERS
// ============================================================
double Bid() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double Ask() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

int CountMyPositions()
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

bool IsSessionOK()
{
   if(!EnableSession) return true;
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);
   if(SessionStart < SessionEnd)
      return (tm.hour >= SessionStart && tm.hour < SessionEnd);
   return (tm.hour >= SessionStart || tm.hour < SessionEnd);
}

// Find live position by ticket
int FindLive(ulong ticket)
{
   for(int i = 0; i < liveCount; i++)
      if(livePos[i].ticket == ticket) return i;
   return -1;
}

void RemoveLive(int idx)
{
   if(idx >= 0 && idx < liveCount)
   {
      livePos[idx] = livePos[liveCount - 1];
      liveCount--;
   }
}

// Determine session name
string GetSessionName(int hour)
{
   if(hour >= 0 && hour < 3)    return "Sydney";
   if(hour >= 3 && hour < 8)    return "Asian";
   if(hour >= 8 && hour < 13)   return "London";
   if(hour >= 13 && hour < 17)  return "NY";
   if(hour >= 17 && hour < 22)  return "NY_Late";
   return "Sydney";
}

// ============================================================
// ONINIT
// ============================================================
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   peakBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   tradeCount = 0;
   liveCount = 0;
   cdCount = 0;

   Print("=== LevelBounce v2.0 Analytics ===",
         " Step=$", LevelStepUSD,
         " Entry=$", EntryDistUSD, "+-", EntryTolUSD,
         " TP=$", TPUSD, " SL=$", SLUSD,
         " Cooldown=$", CooldownUSD,
         " Lot=", LotSize,
         " Session=", (EnableSession ? IntegerToString(SessionStart)+"-"+IntegerToString(SessionEnd) : "OFF"));

   return INIT_SUCCEEDED;
}

// ============================================================
// WRITE CSV + FULL REPORT ON DEINIT
// ============================================================
void OnDeinit(const int reason)
{
   // === WRITE CSV ===
   if(WriteCSV && tradeCount > 0)
   {
      int fh = FileOpen(CSVFileName, FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
      if(fh != INVALID_HANDLE)
      {
         FileWrite(fh, "Ticket", "Direction", "Level", "IsCentena",
                   "EntryPrice", "ClosePrice", "SL", "TP",
                   "OpenTime", "CloseTime", "HoldingSec",
                   "Hour", "DOW", "Month", "Session",
                   "Profit", "MFE", "MAE", "IsTP", "IsSL",
                   "DistFromLevel");
         for(int i = 0; i < tradeCount; i++)
         {
            if(!trades[i].closed) continue;
            string dir = (trades[i].direction > 0) ? "BUY" : "SELL";
            string sess = GetSessionName(trades[i].openHour);
            string cent = trades[i].isCentena ? "100" : "50";
            double distFromLevel = MathAbs(trades[i].entryPrice - trades[i].level);
            string dowNames[] = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};

            FileWrite(fh,
               (string)trades[i].ticket,
               dir,
               DoubleToString(trades[i].level, 0),
               cent,
               DoubleToString(trades[i].entryPrice, 2),
               DoubleToString(trades[i].closePrice, 2),
               DoubleToString(trades[i].slPrice, 2),
               DoubleToString(trades[i].tpPrice, 2),
               TimeToString(trades[i].openTime),
               TimeToString(trades[i].closeTime),
               DoubleToString(trades[i].holdingSec, 0),
               IntegerToString(trades[i].openHour),
               dowNames[trades[i].openDow],
               IntegerToString(trades[i].openMonth),
               sess,
               DoubleToString(trades[i].profit, 2),
               DoubleToString(trades[i].mfe, 2),
               DoubleToString(trades[i].mae, 2),
               (trades[i].isTP ? "TP" : ""),
               (trades[i].isSL ? "SL" : ""),
               DoubleToString(distFromLevel, 2));
         }
         FileClose(fh);
         Print(">>> CSV written: ", CSVFileName, " (", tradeCount, " trades)");
      }
   }

   // === COUNT CLOSED TRADES ===
   int totalClosed = 0;
   int totalWins = 0, totalLosses = 0;
   double totalProfit = 0;
   int buyCount = 0, sellCount = 0;
   int buyWins = 0, sellWins = 0;
   double buyProfit = 0, sellProfit = 0;
   int centCount = 0, fiftyCount = 0;
   int centWins = 0, fiftyWins = 0;
   double centProfit = 0, fiftyProfit = 0;
   int tpCount = 0, slCount = 0;
   double totalMFE = 0, totalMAE = 0;
   double totalHolding = 0;
   double maxMFE = 0, maxMAE = 0;

   // By hour (0-23)
   int    hourTrades[24], hourWins[24];
   double hourProfit[24];
   ArrayInitialize(hourTrades, 0);
   ArrayInitialize(hourWins, 0);
   ArrayInitialize(hourProfit, 0.0);

   // By DOW (0-6)
   int    dowTrades[7], dowWins[7];
   double dowProfit[7];
   ArrayInitialize(dowTrades, 0);
   ArrayInitialize(dowWins, 0);
   ArrayInitialize(dowProfit, 0.0);

   // By month (1-12)
   int    monTrades[13], monWins[13];
   double monProfit[13];
   ArrayInitialize(monTrades, 0);
   ArrayInitialize(monWins, 0);
   ArrayInitialize(monProfit, 0.0);

   // By session
   string sessNames[] = {"Sydney","Asian","London","NY","NY_Late"};
   int    sessTrades[5], sessWins[5];
   double sessProfit[5];
   ArrayInitialize(sessTrades, 0);
   ArrayInitialize(sessWins, 0);
   ArrayInitialize(sessProfit, 0.0);

   // Streaks
   int curStreak = 0, maxWinStreak = 0, maxLossStreak = 0;

   for(int i = 0; i < tradeCount; i++)
   {
      if(!trades[i].closed) continue;
      totalClosed++;
      totalProfit += trades[i].profit;
      bool win = (trades[i].profit >= 0);
      if(win) totalWins++;
      else totalLosses++;

      // Streak
      if(win) { curStreak = (curStreak > 0) ? curStreak + 1 : 1; if(curStreak > maxWinStreak) maxWinStreak = curStreak; }
      else    { curStreak = (curStreak < 0) ? curStreak - 1 : -1; if(-curStreak > maxLossStreak) maxLossStreak = -curStreak; }

      // Direction
      if(trades[i].direction > 0) { buyCount++; buyProfit += trades[i].profit; if(win) buyWins++; }
      else                        { sellCount++; sellProfit += trades[i].profit; if(win) sellWins++; }

      // Level type
      if(trades[i].isCentena) { centCount++; centProfit += trades[i].profit; if(win) centWins++; }
      else                     { fiftyCount++; fiftyProfit += trades[i].profit; if(win) fiftyWins++; }

      // TP/SL
      if(trades[i].isTP) tpCount++;
      if(trades[i].isSL) slCount++;

      // MFE/MAE
      totalMFE += trades[i].mfe;
      totalMAE += trades[i].mae;
      if(trades[i].mfe > maxMFE) maxMFE = trades[i].mfe;
      if(trades[i].mae > maxMAE) maxMAE = trades[i].mae;

      // Holding
      totalHolding += trades[i].holdingSec;

      // By hour
      int h = trades[i].openHour;
      hourTrades[h]++;
      hourProfit[h] += trades[i].profit;
      if(win) hourWins[h]++;

      // By DOW
      int d = trades[i].openDow;
      dowTrades[d]++;
      dowProfit[d] += trades[i].profit;
      if(win) dowWins[d]++;

      // By month
      int m = trades[i].openMonth;
      if(m >= 1 && m <= 12)
      {
         monTrades[m]++;
         monProfit[m] += trades[i].profit;
         if(win) monWins[m]++;
      }

      // By session
      int si = -1;
      string sn = GetSessionName(h);
      for(int j = 0; j < 5; j++) if(sn == sessNames[j]) { si = j; break; }
      if(si >= 0)
      {
         sessTrades[si]++;
         sessProfit[si] += trades[i].profit;
         if(win) sessWins[si]++;
      }
   }

   if(totalClosed == 0) { Print("=== NO TRADES ==="); return; }

   double avgHoldMin = totalHolding / totalClosed / 60.0;
   double avgMFE = totalMFE / totalClosed;
   double avgMAE = totalMAE / totalClosed;
   double winRate = (double)totalWins / totalClosed * 100.0;

   // === PRINT FULL REPORT ===
   Print("================================================================");
   Print("=== LEVELBOUNCE v2.0 — FULL ANALYTICS REPORT ===");
   Print("================================================================");
   Print("OVERALL: trades=", totalClosed,
         " W=", totalWins, " L=", totalLosses,
         " WR=", DoubleToString(winRate, 1), "%",
         " Net=$", DoubleToString(totalProfit, 2),
         " PF=", (totalLosses > 0 && totalProfit > 0 ? DoubleToString(
            (double)totalWins * MathAbs(totalProfit / totalWins) /
            ((double)totalLosses * MathAbs((totalProfit - totalWins * (totalProfit/totalWins > 0 ? totalProfit/totalWins : 0)) / (totalLosses > 0 ? totalLosses : 1)))
            , 2) : "N/A"));
   Print("TP hits=", tpCount, " (", DoubleToString((double)tpCount/totalClosed*100, 1), "%)",
         " SL hits=", slCount, " (", DoubleToString((double)slCount/totalClosed*100, 1), "%)");
   Print("Max DD: ", DoubleToString(maxDDPct, 3), "% ($", DoubleToString(maxDDUSD, 2), ")");
   Print("Streaks: maxWin=", maxWinStreak, " maxLoss=", maxLossStreak);

   Print("--- MFE/MAE ---");
   Print("Avg MFE=$", DoubleToString(avgMFE, 2),
         " Max MFE=$", DoubleToString(maxMFE, 2),
         " Avg MAE=$", DoubleToString(avgMAE, 2),
         " Max MAE=$", DoubleToString(maxMAE, 2));

   Print("--- HOLDING TIME ---");
   Print("Avg=", DoubleToString(avgHoldMin, 1), " min",
         " Max=", DoubleToString(totalHolding > 0 ? totalHolding / 60.0 : 0, 1), " min total");

   // Find min/max holding
   double minHold = 999999, maxHold = 0;
   for(int i = 0; i < tradeCount; i++)
   {
      if(!trades[i].closed) continue;
      if(trades[i].holdingSec < minHold) minHold = trades[i].holdingSec;
      if(trades[i].holdingSec > maxHold) maxHold = trades[i].holdingSec;
   }
   Print("Min hold=", DoubleToString(minHold/60, 1), " min  Max hold=", DoubleToString(maxHold/60, 1), " min");

   Print("--- DIRECTION ---");
   Print("BUY:  n=", buyCount, " W=", buyWins, " WR=", (buyCount > 0 ? DoubleToString((double)buyWins/buyCount*100, 1) : "0"), "% P=$", DoubleToString(buyProfit, 2));
   Print("SELL: n=", sellCount, " W=", sellWins, " WR=", (sellCount > 0 ? DoubleToString((double)sellWins/sellCount*100, 1) : "0"), "% P=$", DoubleToString(sellProfit, 2));

   Print("--- LEVEL TYPE ---");
   Print("$100: n=", centCount, " W=", centWins, " WR=", (centCount > 0 ? DoubleToString((double)centWins/centCount*100, 1) : "0"), "% P=$", DoubleToString(centProfit, 2),
         " avgP=$", (centCount > 0 ? DoubleToString(centProfit/centCount, 3) : "0"));
   Print("$50:  n=", fiftyCount, " W=", fiftyWins, " WR=", (fiftyCount > 0 ? DoubleToString((double)fiftyWins/fiftyCount*100, 1) : "0"), "% P=$", DoubleToString(fiftyProfit, 2),
         " avgP=$", (fiftyCount > 0 ? DoubleToString(fiftyProfit/fiftyCount, 3) : "0"));

   Print("--- BY HOUR (server time) ---");
   for(int h = 0; h < 24; h++)
   {
      if(hourTrades[h] == 0) continue;
      Print("  H", (h < 10 ? "0" : ""), h, ": n=", hourTrades[h],
            " WR=", DoubleToString((double)hourWins[h]/hourTrades[h]*100, 1), "%",
            " P=$", DoubleToString(hourProfit[h], 2),
            " avgP=$", DoubleToString(hourProfit[h]/hourTrades[h], 3));
   }

   Print("--- BY SESSION ---");
   for(int s = 0; s < 5; s++)
   {
      if(sessTrades[s] == 0) continue;
      Print("  ", sessNames[s], ": n=", sessTrades[s],
            " WR=", DoubleToString((double)sessWins[s]/sessTrades[s]*100, 1), "%",
            " P=$", DoubleToString(sessProfit[s], 2),
            " avgP=$", DoubleToString(sessProfit[s]/sessTrades[s], 3));
   }

   Print("--- BY DAY OF WEEK ---");
   string dowN[] = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};
   for(int d = 0; d < 7; d++)
   {
      if(dowTrades[d] == 0) continue;
      Print("  ", dowN[d], ": n=", dowTrades[d],
            " WR=", DoubleToString((double)dowWins[d]/dowTrades[d]*100, 1), "%",
            " P=$", DoubleToString(dowProfit[d], 2),
            " avgP=$", DoubleToString(dowProfit[d]/dowTrades[d], 3));
   }

   Print("--- BY MONTH ---");
   string monN[] = {"","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
   for(int m = 1; m <= 12; m++)
   {
      if(monTrades[m] == 0) continue;
      Print("  ", monN[m], ": n=", monTrades[m],
            " WR=", DoubleToString((double)monWins[m]/monTrades[m]*100, 1), "%",
            " P=$", DoubleToString(monProfit[m], 2),
            " avgP=$", DoubleToString(monProfit[m]/monTrades[m], 3));
   }

   // Best/worst hours
   int bestH = -1, worstH = -1;
   double bestHP = -999999, worstHP = 999999;
   for(int h = 0; h < 24; h++)
   {
      if(hourTrades[h] < 5) continue;  // min 5 trades for significance
      double ap = hourProfit[h] / hourTrades[h];
      if(ap > bestHP) { bestHP = ap; bestH = h; }
      if(ap < worstHP) { worstHP = ap; worstH = h; }
   }
   Print("--- BEST/WORST (min 5 trades) ---");
   if(bestH >= 0)
      Print("Best hour: H", (bestH<10?"0":""), bestH, " avgP=$", DoubleToString(bestHP, 3), " (n=", hourTrades[bestH], ")");
   if(worstH >= 0)
      Print("Worst hour: H", (worstH<10?"0":""), worstH, " avgP=$", DoubleToString(worstHP, 3), " (n=", hourTrades[worstH], ")");

   // Best/worst session
   int bestS = -1, worstS = -1;
   double bestSP = -999999, worstSP = 999999;
   for(int s = 0; s < 5; s++)
   {
      if(sessTrades[s] < 5) continue;
      double ap = sessProfit[s] / sessTrades[s];
      if(ap > bestSP) { bestSP = ap; bestS = s; }
      if(ap < worstSP) { worstSP = ap; worstS = s; }
   }
   if(bestS >= 0)
      Print("Best session: ", sessNames[bestS], " avgP=$", DoubleToString(bestSP, 3), " (n=", sessTrades[bestS], ")");
   if(worstS >= 0)
      Print("Worst session: ", sessNames[worstS], " avgP=$", DoubleToString(worstSP, 3), " (n=", sessTrades[worstS], ")");

   Print("================================================================");
   Print("=== END REPORT ===");
   Print("================================================================");
}

// ============================================================
// ONTICK
// ============================================================
void OnTick()
{
   double bid = Bid(), ask = Ask();
   if(bid <= 0 || ask <= 0) return;
   double mid = (bid + ask) / 2.0;

   // --- Track DD ---
   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   double eq  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(bal > peakBalance) peakBalance = bal;
   if(peakBalance > 0)
   {
      double ddUSD = peakBalance - eq;
      double ddPct = ddUSD / peakBalance * 100.0;
      if(ddPct > maxDDPct) { maxDDPct = ddPct; maxDDUSD = ddUSD; }
   }

   // --- Update MFE/MAE for open positions ---
   for(int i = 0; i < liveCount; i++)
   {
      int tidx = livePos[i].idx;
      if(tidx < 0 || tidx >= tradeCount) continue;

      double entry = trades[tidx].entryPrice;
      double excursion;
      if(trades[tidx].direction > 0)   // BUY
         excursion = mid - entry;       // positive = favorable
      else                              // SELL
         excursion = entry - mid;       // positive = favorable

      if(excursion > livePos[i].mfe) livePos[i].mfe = excursion;
      if(-excursion > livePos[i].mae) livePos[i].mae = -excursion;  // MAE stored as positive
   }

   // --- Clean expired cooldowns ---
   CleanCooldowns(mid);

   // --- Session filter ---
   if(!IsSessionOK()) return;

   // --- Max positions check ---
   if(CountMyPositions() >= MaxOpenTrades) return;

   if(tradeCount >= MAX_TRADES) return;

   // --- Calculate nearest levels ---
   double levelBelow = MathFloor(mid / LevelStepUSD) * LevelStepUSD;
   double levelAbove = levelBelow + LevelStepUSD;

   double distBelow = mid - levelBelow;
   double distAbove = levelAbove - mid;

   double zoneMin = EntryDistUSD - EntryTolUSD;   // 0.5
   double zoneMax = EntryDistUSD + EntryTolUSD;    // 1.5

   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);

   // === BUY: price near level from above → bounce up ===
   if(distBelow >= zoneMin && distBelow <= zoneMax)
   {
      if(!IsOnCooldown(levelBelow))
      {
         double sl = NormalizeDouble(ask - SLUSD, _Digits);
         double tp = NormalizeDouble(ask + TPUSD, _Digits);
         string cm = "B_" + DoubleToString(levelBelow, 0);

         if(trade.Buy(LotSize, _Symbol, 0, sl, tp, cm))
         {
            if(trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008)
            {
               AddCooldown(levelBelow);
               double fill = trade.ResultPrice();
               ulong ticket = trade.ResultOrder();

               // Record trade
               int idx = tradeCount;
               trades[idx].ticket     = ticket;
               trades[idx].direction  = 1;
               trades[idx].entryPrice = fill;
               trades[idx].slPrice    = sl;
               trades[idx].tpPrice    = tp;
               trades[idx].level      = levelBelow;
               trades[idx].isCentena  = (MathMod(levelBelow, 100.0) < 1.0);
               trades[idx].openTime   = TimeCurrent();
               trades[idx].openHour   = tm.hour;
               trades[idx].openDow    = tm.day_of_week;
               trades[idx].openMonth  = tm.mon;
               trades[idx].closeTime  = 0;
               trades[idx].closePrice = 0;
               trades[idx].profit     = 0;
               trades[idx].mfe        = 0;
               trades[idx].mae        = 0;
               trades[idx].holdingSec = 0;
               trades[idx].isTP       = false;
               trades[idx].isSL       = false;
               trades[idx].closed     = false;
               tradeCount++;

               // Add to live tracking
               if(liveCount < MAX_LIVE)
               {
                  livePos[liveCount].ticket = ticket;
                  livePos[liveCount].idx    = idx;
                  livePos[liveCount].mfe    = 0;
                  livePos[liveCount].mae    = 0;
                  liveCount++;
               }

               Print(">>> BUY @ $", DoubleToString(fill, 2),
                     " lvl=$", DoubleToString(levelBelow, 0),
                     (trades[idx].isCentena ? " [100]" : " [50]"),
                     " H=", tm.hour, " ", GetSessionName(tm.hour));
            }
         }
      }
   }

   // === SELL: price near level from below → bounce down ===
   if(distAbove >= zoneMin && distAbove <= zoneMax)
   {
      if(!IsOnCooldown(levelAbove))
      {
         double sl = NormalizeDouble(bid + SLUSD, _Digits);
         double tp = NormalizeDouble(bid - TPUSD, _Digits);
         string cm = "S_" + DoubleToString(levelAbove, 0);

         if(trade.Sell(LotSize, _Symbol, 0, sl, tp, cm))
         {
            if(trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008)
            {
               AddCooldown(levelAbove);
               double fill = trade.ResultPrice();
               ulong ticket = trade.ResultOrder();

               int idx = tradeCount;
               trades[idx].ticket     = ticket;
               trades[idx].direction  = -1;
               trades[idx].entryPrice = fill;
               trades[idx].slPrice    = sl;
               trades[idx].tpPrice    = tp;
               trades[idx].level      = levelAbove;
               trades[idx].isCentena  = (MathMod(levelAbove, 100.0) < 1.0);
               trades[idx].openTime   = TimeCurrent();
               trades[idx].openHour   = tm.hour;
               trades[idx].openDow    = tm.day_of_week;
               trades[idx].openMonth  = tm.mon;
               trades[idx].closeTime  = 0;
               trades[idx].closePrice = 0;
               trades[idx].profit     = 0;
               trades[idx].mfe        = 0;
               trades[idx].mae        = 0;
               trades[idx].holdingSec = 0;
               trades[idx].isTP       = false;
               trades[idx].isSL       = false;
               trades[idx].closed     = false;
               tradeCount++;

               if(liveCount < MAX_LIVE)
               {
                  livePos[liveCount].ticket = ticket;
                  livePos[liveCount].idx    = idx;
                  livePos[liveCount].mfe    = 0;
                  livePos[liveCount].mae    = 0;
                  liveCount++;
               }

               Print(">>> SELL @ $", DoubleToString(fill, 2),
                     " lvl=$", DoubleToString(levelAbove, 0),
                     (trades[idx].isCentena ? " [100]" : " [50]"),
                     " H=", tm.hour, " ", GetSessionName(tm.hour));
            }
         }
      }
   }
}

// ============================================================
// ONTRADE — track closes, record profit, MFE/MAE
// ============================================================
void OnTrade()
{
   // Check for newly closed positions — scan history
   datetime from = 0;
   HistorySelect(from, TimeCurrent());
   int deals = HistoryDealsTotal();

   for(int i = deals - 1; i >= 0; i--)
   {
      ulong dticket = HistoryDealGetTicket(i);
      if(dticket == 0) continue;
      if(HistoryDealGetString(dticket, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(dticket, DEAL_MAGIC) != (long)MagicNumber) continue;
      if(HistoryDealGetInteger(dticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;

      // Find matching live position by order
      ulong orderTicket = HistoryDealGetInteger(dticket, DEAL_ORDER);
      // The deal comment tells us if TP or SL
      string comment = HistoryDealGetString(dticket, DEAL_COMMENT);
      double profit = HistoryDealGetDouble(dticket, DEAL_PROFIT)
                    + HistoryDealGetDouble(dticket, DEAL_SWAP)
                    + HistoryDealGetDouble(dticket, DEAL_COMMISSION);
      double closePrice = HistoryDealGetDouble(dticket, DEAL_PRICE);
      datetime closeTime = (datetime)HistoryDealGetInteger(dticket, DEAL_TIME);

      // Find by matching the position ticket in our live tracker
      ulong posId = HistoryDealGetInteger(dticket, DEAL_POSITION_ID);

      for(int li = liveCount - 1; li >= 0; li--)
      {
         int tidx = livePos[li].idx;
         if(tidx < 0 || tidx >= tradeCount) continue;
         if(trades[tidx].closed) continue;

         // Match by ticket (position ID matches order ticket for market orders)
         if(livePos[li].ticket == posId || livePos[li].ticket == orderTicket)
         {
            // Record close data
            trades[tidx].closeTime  = closeTime;
            trades[tidx].closePrice = closePrice;
            trades[tidx].profit     = profit;
            trades[tidx].mfe        = livePos[li].mfe;
            trades[tidx].mae        = livePos[li].mae;
            trades[tidx].holdingSec = (double)(closeTime - trades[tidx].openTime);
            trades[tidx].closed     = true;

            // Detect TP or SL from comment
            string cmLower = comment;
            StringToLower(cmLower);
            if(StringFind(cmLower, "tp") >= 0)
               trades[tidx].isTP = true;
            else if(StringFind(cmLower, "sl") >= 0)
               trades[tidx].isSL = true;
            else
            {
               // Fallback: check by profit
               if(profit >= 0) trades[tidx].isTP = true;
               else trades[tidx].isSL = true;
            }

            Print("<<< CLOSE ", (trades[tidx].direction > 0 ? "BUY" : "SELL"),
                  " @ $", DoubleToString(closePrice, 2),
                  " P=$", DoubleToString(profit, 2),
                  (trades[tidx].isTP ? " [TP]" : " [SL]"),
                  " MFE=$", DoubleToString(trades[tidx].mfe, 2),
                  " MAE=$", DoubleToString(trades[tidx].mae, 2),
                  " hold=", DoubleToString(trades[tidx].holdingSec/60, 1), "min");

            RemoveLive(li);
            break;
         }
      }
   }
}

// ============================================================
// END
// ============================================================
