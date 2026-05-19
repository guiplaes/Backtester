//+------------------------------------------------------------------+
//|                                             DFMO_Centenas_Backtest_MT5.mq5 |
//| Standalone MT5 EA for backtesting round-hundred DFMO entries     |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Backtest EA: entry on hundred levels with DFMO confluence, round-level averaging, basket BE + 50% partial."

#include <Trade\Trade.mqh>

input group "General"
input ulong   MagicNumber                 = 260403;
input int     SlippagePoints              = 30;
input ENUM_TIMEFRAMES SignalTimeframe     = PERIOD_M5;
input bool    UseDynamicRiskModel         = true;
input double  BaseUnitLot                 = 0.055;
input double  UnitLotMultiplier           = 100.0;

input group "Signal Backtest"
input bool    UseTelegramSignalCsvEntries = true;
input string  TelegramSignalCsvFile       = "tg_signals_vikingo_last_6m_ea.csv";
input string  TelegramSignalChannelFilter = "ALL";
input bool    CloseOnTelegramSignalClose  = true;
input bool    TelegramSignalTimesAreUTC   = false;
input int     TelegramSignalTimeOffsetMinutes = 0;
input double  MaxSignalEntryPriceDriftUSD = 0.0;

input group "Entry: Centenas + DFMO"
input double  EntryRoundStepUSD           = 100.0;
input double  EntryWindowUSD              = 5.0;
input bool    AllowBuyFromOversoldBand    = true;
input bool    AllowSellFromOverboughtBand = true;
input int     MinSecondsBetweenEntries    = 60;

input group "Entry Session Filter"
input bool    EnableEntrySessionFilter    = true;
input int     EntrySessionStartHour       = 8;
input int     EntrySessionEndHour         = 14;

input group "Real-Like Risk Model"
input string  ChannelMode                 = "T";
input double  DynamicDDTargetPct          = 5.0;
input bool    EnableHardCloseDD           = false;
input double  HardCloseDDPct              = 3.5;
input double  SessionCoefficient          = 1.0;
input double  NewsCoefficient             = 1.0;
input double  BoostCoefficient            = 1.0;
input bool    UseDynamicR                 = true;
input double  FixedRangeVikingo           = 100.0;
input double  FixedRangeTrueTrading       = 120.0;
input int     ATRPeriod                   = 14;
input ENUM_TIMEFRAMES ATRTimeframe        = PERIOD_M1;

input group "Basket Risk"
input bool    EnablePriceBasketStop       = true;
input double  BasketStopDistanceUSD       = 60.0;
input double  BreakEvenOffsetUSD          = 0.0;

input group "Averaging Levels"
input double  AveragingStepUSD            = 25.0;
input double  MinGapFromLastAverageUSD    = 20.0;
input bool    RequireDFMOForAveraging     = false;

input group "Profit Management"
input double  ProfitStepUSD               = 25.0;
input double  PartialClosePercent         = 50.0;
input double  ProfitSecureDistanceUSD     = 15.0;
input double  MinGlobalProfitUSDForBE     = 0.01;
input double  LevelTouchWindowUSD         = 5.0;

input group "DFMO Parameters"
input int     StochKPeriod                = 25;
input int     StochSmoothing              = 4;
input int     StochDPeriod                = 4;
input int     RSIPeriod                   = 3;
input int     OverboughtLvl               = 80;
input int     OversoldLvl                 = 20;

CTrade trade;
int    hDFMO = INVALID_HANDLE;
string TraceFileName = "";
string TraceRunId = "";
string BasketSummaryFileName = "";
string RunSummaryFileName = "";
string SignalFeatureFileName = "";
string TradeEventFileName = "";

struct TelegramSignalRow
{
   string   channelTag;
   int      direction;
   datetime openTime;
   double   openPrice;
   datetime closeTime;
   bool     hasClose;
   string   closeReason;
   long     openMessageId;
   long     closeMessageId;
};

int      basketDirection = 0;       // 1=BUY, -1=SELL
double   basketAnchorPrice = 0.0;
double   basketStopPrice = 0.0;
double   lastAvgReferencePrice = 0.0;
double   nextProfitTriggerPrice = 0.0;
datetime lastEntryTime = 0;
datetime lastManagedBarTime = 0;
long     lastEntryRoundId = LONG_MIN;
int      lastEntrySignal = 0;
bool     profitActionArmed = false;
int      profitStage = 0;
double   basketEntryEquity = 0.0;
double   basketEntryBalance = 0.0;
bool     hardDDTriggered = false;
bool     basketPendingFirstLevel = false;
double   cachedLotBase = 0.01;
double   cachedRange = 120.0;
double   cachedLotPerDollar = 0.0;
double   cachedUnitLot = 0.01;
double   cachedEntryLotWeak = 0.01;
double   cachedEntryLotMid = 0.01;
double   cachedEntryLotStrong = 0.01;
double   cachedAvgLotWeak = 0.01;
double   cachedAvgLotMid = 0.01;
double   cachedAvgLotStrong = 0.01;
int      basketId = 0;
datetime basketStartTime = 0;
string   basketCloseReason = "";
bool     recoveredManagedFlag = false;
double   lastClosedRecoveredLevel = 0.0;
double   basketMaxDDPct = 0.0;
double   basketMaxDDAmount = 0.0;
double   basketMaxAdverseDistanceUSD = 0.0;
double   basketWorstPrice = 0.0;
TelegramSignalRow tgSignals[];

// Forced tester config for Telegram-signal runs so MT5 cached profiles
// cannot silently revert the intended settings.
double EffectiveBaseUnitLot()
{
   if(UseTelegramSignalCsvEntries)
      return 0.092;
   return BaseUnitLot;
}

double EffectiveUnitLotMultiplier()
{
   if(UseTelegramSignalCsvEntries)
      return 100.0;
   return UnitLotMultiplier;
}

bool EffectiveEnableHardCloseDD()
{
   if(UseTelegramSignalCsvEntries)
      return false;
   return EnableHardCloseDD;
}

bool EffectiveEnablePriceBasketStop()
{
   if(UseTelegramSignalCsvEntries)
      return true;
   return EnablePriceBasketStop;
}

string EffectiveTelegramSignalCsvFile()
{
   if(UseTelegramSignalCsvEntries)
      return "tg_signals_tt_last_2y_ea.csv";
   return TelegramSignalCsvFile;
}

bool UseCompactBacktestLogging()
{
   return UseTelegramSignalCsvEntries;
}

bool ShouldTraceEvent(string eventType)
{
   if(!UseCompactBacktestLogging())
      return true;

   return false;
}

double EffectiveBasketStopDistanceUSD()
{
   if(UseTelegramSignalCsvEntries)
      return 50.0;
   return BasketStopDistanceUSD;
}

double EffectiveProfitSecureDistanceUSD()
{
   if(UseTelegramSignalCsvEntries)
      return 10.0;
   return ProfitSecureDistanceUSD;
}

double EffectiveMaxSignalEntryPriceDriftUSD()
{
   if(UseTelegramSignalCsvEntries)
      return 3.0;
   return MaxSignalEntryPriceDriftUSD;
}

double EffectiveStructuralBreakConfirmUSD()
{
   if(UseTelegramSignalCsvEntries)
      return 5.0;
   return 15.0;
}

bool IsEntrySessionAllowed(datetime whenTime)
{
   if(!EnableEntrySessionFilter)
      return true;

   MqlDateTime tm;
   TimeToStruct(whenTime, tm);
   int hour = tm.hour;

   if(EntrySessionStartHour == EntrySessionEndHour)
      return true;

   if(EntrySessionStartHour < EntrySessionEndHour)
      return (hour >= EntrySessionStartHour && hour < EntrySessionEndHour);

   return (hour >= EntrySessionStartHour || hour < EntrySessionEndHour);
}
int      tgSignalCount = 0;
int      tgSignalCursor = 0;
int      activeTelegramSignalIndex = -1;
datetime activeTelegramSignalCloseTime = 0;
bool     activeTelegramSignalHasClose = false;
long     activeTelegramSignalCloseMessageId = 0;
datetime runStartTime = 0;
double   runStartBalance = 0.0;
double   runStartEquity = 0.0;
double   runPeakEquity = 0.0;
double   runMaxDDPct = 0.0;
double   runMaxDDAmount = 0.0;
string   basketEntryLevelType = "";
double   basketEntryLevelPrice = 0.0;
double   basketSignalPrice = 0.0;
bool     basketFromSignal = false;
bool     basketLateSessionEntry = false;
string   basketSignalChannel = "";
long     basketSignalOpenMessageId = 0;
long     basketSignalCloseMessageId = 0;
double   basketSignalEntryDrift = 0.0;
int      basketAverageCount = 0;
int      basketRecoveredCount = 0;
int      basketPartialCount = 0;
int      basketAvgWeakCount = 0;
int      basketAvgMidCount = 0;
int      basketAvgStrongCount = 0;
bool     pendingStrongAvgReject = false;
double   pendingStrongAvgLevel = 0.0;
datetime basketSignalOpenTime = 0;
datetime basketSignalDeclaredCloseTime = 0;
int      basketSignalDeclaredDurationSec = 0;
int      basketEntryHour = -1;
int      basketEntryMinute = -1;
double   basketEntryMarketPrice = 0.0;
double   basketEntrySpreadUSD = 0.0;
double   basketEntryDfmoStoch = 0.0;
double   basketEntryDfmoRsi = 0.0;
bool     basketEntryDfmoOB = false;
bool     basketEntryDfmoOS = false;
double   basketEntryAtrM1 = 0.0;
double   basketEntryAtrM5 = 0.0;
double   basketEntryTickVolM1 = 0.0;
double   basketEntryTickVolRatio20 = 0.0;
double   basketEntryMove3m = 0.0;
double   basketEntryMove5m = 0.0;
double   basketEntryMove15m = 0.0;
double   basketEntryMoveAgainst3m = 0.0;
double   basketEntryMoveAgainst5m = 0.0;
double   basketEntryMoveAgainst15m = 0.0;
double   basketEntryDistanceToLevel = 0.0;
double   basketSignalDistanceToLevel = 0.0;
double   basketSignalDistanceToStrong = 0.0;
int      basketLastAdverseMilestoneUSD = 0;
int      basketLastTimeMilestoneMin = 0;
double   basketMaxFavorableDistanceUSD = 0.0;
bool     basketFavorableMilestonesHit[10];
bool     basketAdverse10Captured = false;
int      basketAdverse10TimeMin = -1;
double   basketMaxFavorableBeforeAdverse10USD = 0.0;
bool     basketFirstAvgCaptured = false;
int      basketFirstAvgTimeMin = -1;
string   basketFirstAvgLevelType = "";
double   basketFirstAvgLevelPrice = 0.0;
double   basketMaxFavorableBeforeFirstAvgUSD = 0.0;
int      basketFirstAvgAgainstCount10m = 0;
int      basketFirstAvgAgainstCount20m = 0;
int      basketFirstAvgAgainstStreak10m = 0;
int      basketFirstAvgAgainstStreak20m = 0;

double BidPrice() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double AskPrice() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

double EntryWeightWeak()   { return 2.0; }
double EntryWeightMid()    { return 1.0; }
double EntryWeightStrong() { return 3.0; }

double AvgWeightWeak()     { return 2.0; }
double AvgWeightMid()      { return 2.0; }
double AvgWeightStrong()   { return 4.0; }

double EffectiveAverageLevelTouchWindow(double levelPrice)
{
   if(UseTelegramSignalCsvEntries)
      return 0.5;

   return MathMax(0.0, GetLevelTouchWindow(levelPrice));
}

double GetLevelTouchWindow(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   int mod50  = (int)MathRound(MathMod(levelPrice, 50.0));

   if(mod100 == 0)
      return 5.0;
   if(mod50 == 0)
      return 4.0;
   return 3.0;
}

bool IsHundredLevel(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   return (mod100 == 0);
}

bool IsFiftyLevel(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   return (mod100 == 50);
}

bool IsStrongLevel(double levelPrice)
{
   return IsHundredLevel(levelPrice) || IsFiftyLevel(levelPrice);
}

bool IsWeakLevel(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   return (mod100 == 25 || mod100 == 75);
}

double StrongAverageRejectOvershootUSD()
{
   if(UseTelegramSignalCsvEntries)
      return 3.0;
   return 3.0;
}

double StrongAverageRejectConfirmUSD()
{
   if(UseTelegramSignalCsvEntries)
      return 2.0;
   return 2.0;
}

double GetBasketLevelStep()
{
   if(AveragingStepUSD > 0.0)
      return AveragingStepUSD;
   return 25.0;
}

double ShiftAdverseLevel(double levelPrice, int direction, int stepCount = 1)
{
   double step = GetBasketLevelStep();
   double delta = (direction == 1 ? -step : step) * stepCount;
   return NormalizePrice(levelPrice + delta);
}

double GetBasketStructuralReferenceLevel()
{
   // Entry doesn't count — use first averaging level as structural reference
   if(basketFirstAvgLevelPrice > 0.0)
      return NormalizePrice(basketFirstAvgLevelPrice);
   // Fallback to entry level until first avg opens (so we still have protection)
   if(basketEntryLevelPrice > 0.0)
      return NormalizePrice(basketEntryLevelPrice);

   double step = GetBasketLevelStep();
   if(step <= 0.0 || basketAnchorPrice <= 0.0)
      return 0.0;

   return NormalizePrice(MathRound(basketAnchorPrice / step) * step);
}

double GetNextAdverseStrongLevelFrom(double referenceLevel, int direction)
{
   if(referenceLevel <= 0.0 || direction == 0)
      return 0.0;

   double level = NormalizePrice(referenceLevel);
   for(int guard = 0; guard < 20; ++guard)
   {
      level = ShiftAdverseLevel(level, direction);
      if(IsStrongLevel(level))
         return level;
   }
   return 0.0;
}

double GetNextAdverseWeakLevelFrom(double referenceLevel, int direction)
{
   if(referenceLevel <= 0.0 || direction == 0)
      return 0.0;

   double level = NormalizePrice(referenceLevel);
   for(int guard = 0; guard < 20; ++guard)
   {
      level = ShiftAdverseLevel(level, direction);
      if(IsWeakLevel(level))
         return level;
   }
   return 0.0;
}

bool IsStrongLevelBrokenByM5Close(double strongLevel, int direction)
{
   if(strongLevel <= 0.0 || direction == 0)
      return false;

   double closedPrice = iClose(_Symbol, PERIOD_M5, 1);
   if(closedPrice <= 0.0)
      return false;

   double tol = GetLevelTouchWindow(strongLevel);
   if(direction == 1)
      return (closedPrice <= (strongLevel - tol));
   if(direction == -1)
      return (closedPrice >= (strongLevel + tol));
   return false;
}

string TrimCsvValue(string value)
{
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

datetime ParseSignalCsvTime(string value)
{
   value = TrimCsvValue(value);
   if(StringLen(value) < 19)
      return 0;

   string timePart = StringSubstr(value, 0, 19);
   StringReplace(timePart, "T", " ");
   return StringToTime(timePart);
}

int GetServerUtcOffsetSeconds()
{
   return (int)(TimeCurrent() - TimeGMT());
}

datetime GetSignalClockTime()
{
   if(TelegramSignalTimesAreUTC)
      return TimeGMT();
   return TimeCurrent();
}

string GetSignalTimeBasisLabel()
{
   if(TelegramSignalTimesAreUTC)
      return "UTC";
   return "SERVER";
}

int GetCurrentServerUtcOffsetMinutes()
{
   return (int)((TimeCurrent() - TimeGMT()) / 60);
}

string BuildSignalTimingDiag(TelegramSignalRow &row, datetime signalClockNow, double marketPrice)
{
   datetime utcNow = TimeGMT();
   datetime serverNow = TimeCurrent();
   int offsetMin = GetCurrentServerUtcOffsetMinutes();
   int deltaToSignalClockSec = (int)(signalClockNow - row.openTime);
   int deltaToUtcSec = (int)(utcNow - row.openTime);
   int deltaToServerSec = (int)(serverNow - row.openTime);

   string details =
      "channel=" + row.channelTag +
      ",open_message_id=" + IntegerToString((int)row.openMessageId) +
      ",direction=" + string(row.direction == 1 ? "BUY" : "SELL") +
      ",time_basis=" + GetSignalTimeBasisLabel() +
      ",signal_open_time=" + TimeToString(row.openTime, TIME_DATE|TIME_SECONDS) +
      ",clock_time=" + TimeToString(signalClockNow, TIME_DATE|TIME_SECONDS) +
      ",clock_utc=" + TimeToString(utcNow, TIME_DATE|TIME_SECONDS) +
      ",clock_server=" + TimeToString(serverNow, TIME_DATE|TIME_SECONDS) +
      ",server_utc_offset_min=" + IntegerToString(offsetMin) +
      ",delta_signal_clock_sec=" + IntegerToString(deltaToSignalClockSec) +
      ",delta_utc_sec=" + IntegerToString(deltaToUtcSec) +
      ",delta_server_sec=" + IntegerToString(deltaToServerSec) +
      ",signal_price=" + DoubleToString(row.openPrice, _Digits) +
      ",market_price=" + DoubleToString(marketPrice, _Digits);

   return details;
}

bool SignalChannelAllowed(string channelTag)
{
   string filter = TelegramSignalChannelFilter;
   StringToUpper(filter);
   channelTag = TrimCsvValue(channelTag);
   StringToUpper(channelTag);

   if(filter == "ALL" || filter == "*")
      return true;
   return (channelTag == filter);
}

bool IsLateEntryWindow(datetime whenTime)
{
   return false;
}

bool IsNyOpenBlockedWindow(datetime whenTime)
{
   MqlDateTime tm;
   TimeToStruct(whenTime, tm);
   return (tm.hour >= 14 && tm.hour < 16);
}

double ResolveLateSignalEntryLevel(int direction, double currentPrice)
{
   if(currentPrice <= 0.0 || direction == 0)
      return 0.0;

   double nearestLevel = NormalizePrice(MathRound(currentPrice / AveragingStepUSD) * AveragingStepUSD);
   double tolerance = GetLevelTouchWindow(nearestLevel);
   if(MathAbs(currentPrice - nearestLevel) > tolerance)
      return 0.0;

   int mod100 = (int)MathRound(MathMod(nearestLevel, 100.0));
   if(mod100 == 0)
      return nearestLevel;
   return 0.0;
}

bool OpenPendingTelegramSignalEntry(TelegramSignalRow &row, double entryLevelPrice, double marketPrice)
{
   if(entryLevelPrice <= 0.0)
      return false;

   double drift = MathAbs(marketPrice - row.openPrice);
   if(OpenInitialBasketEntry(row.direction, entryLevelPrice))
   {
      basketFromSignal = true;
      basketLateSessionEntry = true;
      basketEntryLevelType = "late_100";
      basketEntryLevelPrice = entryLevelPrice;
      basketSignalChannel = row.channelTag;
      basketSignalOpenMessageId = row.openMessageId;
      basketSignalPrice = row.openPrice;
      basketSignalEntryDrift = drift;
      basketStopPrice = NormalizePrice(entryLevelPrice + (row.direction == 1 ? -15.0 : 15.0));
      CaptureSignalFeatureSnapshot(row, marketPrice, entryLevelPrice, basketEntryLevelType);
      WriteTradeEventRow("entry_late");
      return true;
   }
   return false;
}

double ResolveSignalEntryLevel(double rawPrice)
{
   if(rawPrice <= 0.0)
      return NormalizePrice(rawPrice);

   double nearestLevel = MathRound(rawPrice / AveragingStepUSD) * AveragingStepUSD;
   nearestLevel = NormalizePrice(nearestLevel);
   double tolerance = GetLevelTouchWindow(nearestLevel);

   if(MathAbs(rawPrice - nearestLevel) <= tolerance)
      return nearestLevel;

   return NormalizePrice(MathRound(rawPrice / 50.0) * 50.0);
}

void RebuildLevelLotsFromBase(double baseMidLot)
{
   double unitLot = NormalizeVolume(EffectiveBaseUnitLot() * EffectiveUnitLotMultiplier());
   if(unitLot <= 0.0)
      unitLot = NormalizeVolume(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN));

   cachedUnitLot = unitLot;
   cachedEntryLotWeak = NormalizeVolume(unitLot * EntryWeightWeak());
   cachedEntryLotMid = NormalizeVolume(unitLot * EntryWeightMid());
   cachedEntryLotStrong = NormalizeVolume(unitLot * EntryWeightStrong());
   cachedAvgLotWeak = NormalizeVolume(unitLot * AvgWeightWeak());
   cachedAvgLotMid = NormalizeVolume(unitLot * AvgWeightMid());
   cachedAvgLotStrong = NormalizeVolume(unitLot * AvgWeightStrong());
   cachedLotBase = cachedEntryLotMid;
}

bool LoadTelegramSignalCsv()
{
   string signalCsvFile = EffectiveTelegramSignalCsvFile();

   ArrayResize(tgSignals, 0);
   tgSignalCount = 0;
   tgSignalCursor = 0;
   activeTelegramSignalIndex = -1;
   activeTelegramSignalCloseTime = 0;
   activeTelegramSignalHasClose = false;
   activeTelegramSignalCloseMessageId = 0;

   int handle = FileOpen(signalCsvFile, FILE_READ|FILE_CSV|FILE_COMMON|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("Cannot open Telegram signal CSV: ", signalCsvFile, " error=", GetLastError());
      return false;
   }

   if(!FileIsEnding(handle))
   {
      FileReadString(handle); // channel_tag
      FileReadString(handle); // direction
      FileReadString(handle); // open_time
      FileReadString(handle); // open_price
      FileReadString(handle); // close_time
      FileReadString(handle); // has_close
      FileReadString(handle); // close_reason
      FileReadString(handle); // open_message_id
      FileReadString(handle); // close_message_id
   }

   while(!FileIsEnding(handle))
   {
      string channelTag     = FileReadString(handle);
      string directionStr   = FileReadString(handle);
      string openTimeStr    = FileReadString(handle);
      string openPriceStr   = FileReadString(handle);
      string closeTimeStr   = FileReadString(handle);
      string hasCloseStr    = FileReadString(handle);
      string closeReason    = FileReadString(handle);
      string openMsgStr     = FileReadString(handle);
      string closeMsgStr    = FileReadString(handle);

      if(StringLen(channelTag) == 0 || StringLen(directionStr) == 0 || StringLen(openTimeStr) == 0)
         continue;

      channelTag = TrimCsvValue(channelTag);
      if(!SignalChannelAllowed(channelTag))
         continue;

      TelegramSignalRow row;
      row.channelTag = channelTag;
      row.direction = (StringFind(directionStr, "BUY") >= 0) ? 1 : -1;
      row.openTime = ParseSignalCsvTime(openTimeStr);
      row.openPrice = StringToDouble(openPriceStr);
      row.closeTime = ParseSignalCsvTime(closeTimeStr);
      if(TelegramSignalTimeOffsetMinutes != 0)
      {
         row.openTime += TelegramSignalTimeOffsetMinutes * 60;
         if(row.closeTime > 0)
            row.closeTime += TelegramSignalTimeOffsetMinutes * 60;
      }
      row.hasClose = (TrimCsvValue(hasCloseStr) == "1" && row.closeTime > 0);
      row.closeReason = TrimCsvValue(closeReason);
      row.openMessageId = (long)StringToInteger(openMsgStr);
      row.closeMessageId = (long)StringToInteger(closeMsgStr);

      if(row.openTime <= 0)
         continue;

      int newSize = tgSignalCount + 1;
      ArrayResize(tgSignals, newSize);
      tgSignals[tgSignalCount] = row;
      tgSignalCount = newSize;
   }

   FileClose(handle);
   TraceEvent("SIGNAL_FILE_LOADED",
              "file=" + signalCsvFile +
              ",count=" + IntegerToString(tgSignalCount) +
              ",filter=" + TelegramSignalChannelFilter);
   Print("Telegram signal CSV loaded: file=", signalCsvFile,
         " count=", tgSignalCount,
         " filter=", TelegramSignalChannelFilter);
   return (tgSignalCount > 0);
}

bool IsFavorableLevelTouched(int direction, double currentPrice, double levelPrice)
{
   double tol = MathMax(0.0, GetLevelTouchWindow(levelPrice));
   if(direction == 1)
      return currentPrice >= (levelPrice - tol);
   if(direction == -1)
      return currentPrice <= (levelPrice + tol);
   return false;
}

bool IsAdverseLevelTouched(int direction, double currentPrice, double levelPrice)
{
   double tol = MathMax(0.0, GetLevelTouchWindow(levelPrice));
   if(direction == 1)
      return currentPrice <= (levelPrice + tol);
   if(direction == -1)
      return currentPrice >= (levelPrice - tol);
   return false;
}

bool IsRecoveredLevelReachedExact(int direction, double currentPrice, double levelPrice)
{
   if(direction == 1)
      return currentPrice >= levelPrice;
   if(direction == -1)
      return currentPrice <= levelPrice;
   return false;
}

void TraceEvent(string eventType, string details)
{
   if(!ShouldTraceEvent(eventType))
      return;

   if(StringLen(TraceFileName) == 0)
      return;

   int handle = FileOpen(TraceFileName, FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(handle == INVALID_HANDLE)
      handle = FileOpen(TraceFileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   string line = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," +
                 TraceRunId + "," + eventType + "," + details;
   FileWriteString(handle, line + "\r\n");
   FileClose(handle);
}

void AppendTextLine(string fileName, string line)
{
   if(StringLen(fileName) == 0)
      return;

   int handle = FileOpen(fileName, FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(handle == INVALID_HANDLE)
      handle = FileOpen(fileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   FileWriteString(handle, line + "\r\n");
   FileClose(handle);
}

string CsvSafe(string value)
{
   string out = value;
   StringReplace(out, "\"", "'");
   StringReplace(out, ",", ";");
   return out;
}

void WriteBasketSummaryRow(datetime closeTime, double realizedPnL, string reason,
                           double pctBalance, double pctEquity, double endBalance, double endEquity)
{
   if(UseCompactBacktestLogging())
      return;

   int durationMin = 0;
   if(basketStartTime > 0 && closeTime >= basketStartTime)
      durationMin = (int)((closeTime - basketStartTime) / 60);

   string line = TraceRunId + "," +
                 IntegerToString(basketId) + "," +
                 (basketDirection == 1 ? "BUY" : "SELL") + "," +
                 TimeToString(basketStartTime, TIME_DATE|TIME_SECONDS) + "," +
                 TimeToString(closeTime, TIME_DATE|TIME_SECONDS) + "," +
                 IntegerToString(durationMin) + "," +
                 (basketFromSignal ? "1" : "0") + "," +
                 CsvSafe(basketSignalChannel) + "," +
                 IntegerToString((int)basketSignalOpenMessageId) + "," +
                 IntegerToString((int)basketSignalCloseMessageId) + "," +
                 DoubleToString(basketSignalPrice, _Digits) + "," +
                 DoubleToString(basketSignalEntryDrift, _Digits) + "," +
                 CsvSafe(basketEntryLevelType) + "," +
                 DoubleToString(basketEntryLevelPrice, _Digits) + "," +
                 DoubleToString(basketAnchorPrice, _Digits) + "," +
                 DoubleToString(cachedUnitLot, 2) + "," +
                 IntegerToString(basketAverageCount) + "," +
                 IntegerToString(basketRecoveredCount) + "," +
                 IntegerToString(basketPartialCount) + "," +
                 CsvSafe(reason) + "," +
                 DoubleToString(realizedPnL, 2) + "," +
                 DoubleToString(pctBalance, 3) + "," +
                 DoubleToString(pctEquity, 3) + "," +
                 DoubleToString(basketMaxAdverseDistanceUSD, _Digits) + "," +
                 DoubleToString(basketWorstPrice, _Digits) + "," +
                 DoubleToString(basketMaxDDPct, 3) + "," +
                 DoubleToString(basketMaxDDAmount, 2) + "," +
                 DoubleToString(basketEntryBalance, 2) + "," +
                 DoubleToString(endBalance, 2) + "," +
                 DoubleToString(basketEntryEquity, 2) + "," +
                 DoubleToString(endEquity, 2);
   AppendTextLine(BasketSummaryFileName, line);
}

void WriteRunSummary(string status)
{
   double endBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double endEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   string line = TraceRunId + "," +
                 CsvSafe(status) + "," +
                 TimeToString(runStartTime, TIME_DATE|TIME_SECONDS) + "," +
                 TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," +
                 DoubleToString(runStartBalance, 2) + "," +
                 DoubleToString(runStartEquity, 2) + "," +
                 DoubleToString(runPeakEquity, 2) + "," +
                 DoubleToString(endBalance, 2) + "," +
                 DoubleToString(endEquity, 2) + "," +
                 DoubleToString(runMaxDDPct, 3) + "," +
                 DoubleToString(runMaxDDAmount, 2);
   AppendTextLine(RunSummaryFileName, line);
}

double ComputeBasketRealizedPnL(datetime fromTime, datetime toTime)
{
   if(fromTime <= 0) return 0.0;
   if(toTime < fromTime) toTime = fromTime;
   if(!HistorySelect(fromTime - 60, toTime + 60))
      return 0.0;

   double pnl = 0.0;
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; ++i)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
      if((ulong)HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;

      long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entryType != DEAL_ENTRY_OUT && entryType != DEAL_ENTRY_OUT_BY)
         continue;

      datetime dealTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(dealTime < fromTime || dealTime > toTime + 60) continue;

      pnl += HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(dealTicket, DEAL_SWAP);
      pnl += HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
   }
   return pnl;
}

double ComputeCurrentBasketGlobalProfit()
{
   double weighted = 0.0;
   double totalLots = 0.0;
   double openProfit = 0.0;
   if(!GetBasketStats(weighted, totalLots, openProfit))
      openProfit = 0.0;

   double realized = 0.0;
   if(basketStartTime > 0)
      realized = ComputeBasketRealizedPnL(basketStartTime, TimeCurrent());

   return openProfit + realized;
}

string InferBasketCloseReason()
{
   if(StringLen(basketCloseReason) > 0)
      return basketCloseReason;

   if(basketStartTime <= 0)
      return "unknown";

   if(!HistorySelect(basketStartTime - 60, TimeCurrent() + 60))
      return "unknown";

   bool sawSL = false;
   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; ++i)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;
      if(HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
      if((ulong)HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != MagicNumber) continue;

      long entryType = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if(entryType != DEAL_ENTRY_OUT && entryType != DEAL_ENTRY_OUT_BY)
         continue;

      datetime dealTime = (datetime)HistoryDealGetInteger(dealTicket, DEAL_TIME);
      if(dealTime < basketStartTime) continue;

      long reason = HistoryDealGetInteger(dealTicket, DEAL_REASON);
      if(reason == DEAL_REASON_SL)
         sawSL = true;
   }

   if(sawSL) return "stop_loss";
   return "unknown";
}

void FinalizeBasket()
{
   if(basketStartTime <= 0 || basketId <= 0) return;

   datetime closeTime = TimeCurrent();
   double realizedPnL = ComputeBasketRealizedPnL(basketStartTime, closeTime);
   double endBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   double endEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   string reason = InferBasketCloseReason();
   double pctBalance = (basketEntryBalance > 0.0) ? (realizedPnL / basketEntryBalance * 100.0) : 0.0;
   double pctEquity = (basketEntryEquity > 0.0) ? (realizedPnL / basketEntryEquity * 100.0) : 0.0;

   TraceEvent("BASKET_CLOSED",
              "id=" + IntegerToString(basketId) +
              ",reason=" + reason +
              ",start=" + TimeToString(basketStartTime, TIME_DATE|TIME_SECONDS) +
              ",end=" + TimeToString(closeTime, TIME_DATE|TIME_SECONDS) +
              ",pnl=" + DoubleToString(realizedPnL, 2) +
              ",pct_balance=" + DoubleToString(pctBalance, 3) +
              ",pct_equity=" + DoubleToString(pctEquity, 3) +
              ",max_adverse_usd=" + DoubleToString(basketMaxAdverseDistanceUSD, _Digits) +
              ",max_adverse_price=" + DoubleToString(basketWorstPrice, _Digits) +
              ",max_dd_pct=" + DoubleToString(basketMaxDDPct, 3) +
              ",max_dd_amount=" + DoubleToString(basketMaxDDAmount, 2) +
              ",entry_balance=" + DoubleToString(basketEntryBalance, 2) +
              ",end_balance=" + DoubleToString(endBalance, 2) +
              ",entry_equity=" + DoubleToString(basketEntryEquity, 2) +
              ",end_equity=" + DoubleToString(endEquity, 2));

   WriteBasketSummaryRow(closeTime, realizedPnL, reason, pctBalance, pctEquity, endBalance, endEquity);
   WriteSignalFeatureRow(closeTime, realizedPnL, reason, pctBalance, pctEquity);
   WriteTradeEventRow("closed_" + reason);

   basketEntryBalance = 0.0;
   basketEntryEquity = 0.0;
   basketStartTime = 0;
   basketCloseReason = "";
}

double NormalizePrice(double price)
{
   return NormalizeDouble(price, _Digits);
}

double NormalizeVolume(double volume)
{
   double volMin  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double volMax  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double volStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(volStep <= 0.0) volStep = 0.01;
   volume = MathMax(volMin, MathMin(volMax, volume));
   volume = MathFloor(volume / volStep) * volStep;
   return NormalizeDouble(volume, 2);
}

string GetChannelTag()
{
   string tag = ChannelMode;
   StringToUpper(tag);
   if(tag != "V")
      tag = "T";
   return tag;
}

double GetAtrValue()
{
   int handle = iATR(_Symbol, ATRTimeframe, ATRPeriod);
   if(handle == INVALID_HANDLE)
      return 0.0;

   double atrBuf[2];
   double atr = 0.0;
   if(CopyBuffer(handle, 0, 0, 2, atrBuf) > 0)
      atr = atrBuf[0];
   IndicatorRelease(handle);
   return atr;
}

double GetAtrValueForTimeframe(ENUM_TIMEFRAMES timeframe)
{
   int handle = iATR(_Symbol, timeframe, ATRPeriod);
   if(handle == INVALID_HANDLE)
      return 0.0;

   double atrBuf[2];
   double atr = 0.0;
   if(CopyBuffer(handle, 0, 0, 2, atrBuf) > 0)
      atr = atrBuf[0];
   IndicatorRelease(handle);
   return atr;
}

void RefreshRiskModel()
{
   string tag = GetChannelTag();
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity <= 0.0) equity = AccountInfoDouble(ACCOUNT_BALANCE);
   if(equity <= 0.0) equity = 1000.0;

   int K = (tag == "V") ? 5 : 6;
   double marketMult = (tag == "V") ? 1.232 : 1.179;
   double avgMult = (tag == "V") ? 5.75 : 5.50;

   double atr = GetAtrValue();
   double dynamicR = K * atr * SessionCoefficient * NewsCoefficient;
   dynamicR = MathMax(65.0, MathMin(200.0, dynamicR));
   dynamicR = MathMin(200.0, dynamicR * BoostCoefficient);

   cachedRange = UseDynamicR ? dynamicR : ((tag == "V") ? FixedRangeVikingo : FixedRangeTrueTrading);
   if(cachedRange <= 0.0) cachedRange = (tag == "V") ? 100.0 : 120.0;

   double ddBudget = (DynamicDDTargetPct / 100.0) * equity;
   double ddPerLotBase = marketMult * cachedRange * 100.0 + avgMult * cachedRange * 50.0;
   if(ddPerLotBase <= 0.0)
      ddPerLotBase = cachedRange * 400.0;

   double modelLotBase = NormalizeVolume(ddBudget / ddPerLotBase);
   if(modelLotBase <= 0.0)
      modelLotBase = NormalizeVolume(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN));

   cachedLotPerDollar = (cachedRange > 0.0) ? ((modelLotBase * avgMult) / cachedRange) : 0.0;
   RebuildLevelLotsFromBase(modelLotBase);
}

bool IsOurBasketPosition()
{
   if(PositionGetString(POSITION_SYMBOL) != _Symbol) return false;
   if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber) return false;
   return true;
}

double ExtractLevelFromComment(string comment)
{
   int firstSep = StringFind(comment, "_");
   if(firstSep < 0) return 0.0;

   int secondSep = StringFind(comment, "_", firstSep + 1);
   if(secondSep < 0)
      return StringToDouble(StringSubstr(comment, firstSep + 1));

   return StringToDouble(StringSubstr(comment, firstSep + 1, secondSep - firstSep - 1));
}

int CountBasketPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;
      count++;
   }
   return count;
}

bool GetOpenLevelBounds(double &bestLevel, double &worstLevel)
{
   bool found = false;
   bestLevel = 0.0;
   worstLevel = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      // Skip ENTRY positions — they don't occupy averaging levels
      string comment = PositionGetString(POSITION_COMMENT);
      if(StringFind(comment, "ENTRY") == 0) continue;

      double level = ExtractLevelFromComment(comment);
      if(level <= 0.0) continue;

      if(!found)
      {
         bestLevel = level;
         worstLevel = level;
         found = true;
      }
      else
      {
         if(level < bestLevel) bestLevel = level;
         if(level > worstLevel) worstLevel = level;
      }
   }

   return found;
}

bool GetBasketStats(double &weightedPrice, double &totalLots, double &openProfit)
{
   weightedPrice = 0.0;
   totalLots = 0.0;
   openProfit = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      double lots = PositionGetDouble(POSITION_VOLUME);
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      weightedPrice += open * lots;
      totalLots += lots;
      openProfit += PositionGetDouble(POSITION_PROFIT);
   }

   if(totalLots <= 0.0)
      return false;

   weightedPrice /= totalLots;
   return true;
}

bool GetBasketUnitState(int &positiveCount, int &negativeCount, double &leastFavorablePositiveEntry)
{
   positiveCount = 0;
   negativeCount = 0;
   leastFavorablePositiveEntry = 0.0;
   bool foundPositive = false;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      double profit = PositionGetDouble(POSITION_PROFIT);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);

      if(profit > 0.0)
      {
         positiveCount++;
         if(!foundPositive)
         {
            leastFavorablePositiveEntry = openPrice;
            foundPositive = true;
         }
         else
         {
            if(basketDirection == 1 && openPrice > leastFavorablePositiveEntry)
               leastFavorablePositiveEntry = openPrice;
            if(basketDirection == -1 && openPrice < leastFavorablePositiveEntry)
               leastFavorablePositiveEntry = openPrice;
         }
      }
      else if(profit < 0.0)
      {
         negativeCount++;
      }
   }

   return (positiveCount + negativeCount) > 0;
}


bool ReadDFMOState(bool &isOB, bool &isOS)
{
   isOB = false;
   isOS = false;
   if(hDFMO == INVALID_HANDLE)
      return false;

   double stochK[2];
   double rsi[2];

   if(CopyBuffer(hDFMO, 0, 0, 2, stochK) < 1) return false;
   if(CopyBuffer(hDFMO, 2, 0, 2, rsi) < 1) return false;

   isOB = (stochK[0] > OverboughtLvl && rsi[0] > OverboughtLvl);
   isOS = (stochK[0] < OversoldLvl && rsi[0] < OversoldLvl);
   return true;
}

int CreateDFMOHandleForTimeframe(ENUM_TIMEFRAMES timeframe)
{
   return iCustom(_Symbol, timeframe, "DFMO_DualFrameMomentumOscillator",
                  "", StochKPeriod, StochSmoothing, StochDPeriod,
                  "", RSIPeriod,
                  "", OverboughtLvl, OversoldLvl,
                  "", true, clrTomato, clrLimeGreen,
                  "", true, clrRed, clrDodgerBlue,
                  "", false, false);
}

bool ReadDFMOSnapshot(double &stochValue, double &rsiValue, bool &isOB, bool &isOS)
{
   stochValue = 0.0;
   rsiValue = 0.0;
   isOB = false;
   isOS = false;

   int handle = hDFMO;
   bool releaseHandle = false;
   if(handle == INVALID_HANDLE)
   {
      handle = CreateDFMOHandleForTimeframe(SignalTimeframe);
      if(handle == INVALID_HANDLE)
         return false;
      releaseHandle = true;
   }

   double stochK[2];
   double rsi[2];
   bool ok = (CopyBuffer(handle, 0, 0, 2, stochK) >= 1 &&
              CopyBuffer(handle, 2, 0, 2, rsi) >= 1);
   if(ok)
   {
      stochValue = stochK[0];
      rsiValue = rsi[0];
      isOB = (stochValue > OverboughtLvl && rsiValue > OverboughtLvl);
      isOS = (stochValue < OversoldLvl && rsiValue < OversoldLvl);
   }

   if(releaseHandle)
      IndicatorRelease(handle);
   return ok;
}

double GetTickVolumeAt(ENUM_TIMEFRAMES timeframe, int shift)
{
   long volume = iVolume(_Symbol, timeframe, shift);
   if(volume < 0)
      return 0.0;
   return (double)volume;
}

double GetAverageTickVolume(ENUM_TIMEFRAMES timeframe, int startShift, int count)
{
   if(count <= 0)
      return 0.0;

   double total = 0.0;
   int used = 0;
   for(int i = 0; i < count; ++i)
   {
      double v = GetTickVolumeAt(timeframe, startShift + i);
      if(v <= 0.0)
         continue;
      total += v;
      used++;
   }

   if(used <= 0)
      return 0.0;
   return total / used;
}

double GetRawMoveMinutes(int minutes, double currentMid)
{
   if(minutes <= 0 || currentMid <= 0.0)
      return 0.0;

   datetime lookbackTime = TimeCurrent() - (minutes * 60);
   int shift = iBarShift(_Symbol, PERIOD_M1, lookbackTime, false);
   if(shift < 0)
      return 0.0;

   double pastClose = iClose(_Symbol, PERIOD_M1, shift);
   if(pastClose <= 0.0)
      return 0.0;

   return NormalizePrice(currentMid - pastClose);
}

double GetAgainstSignalMove(int direction, double rawMove)
{
   if(direction == 1)
      return -rawMove;
   if(direction == -1)
      return rawMove;
   return 0.0;
}

double GetDistanceToNearestStrongLevel(double price)
{
   if(price <= 0.0)
      return 0.0;

   double strong = NormalizePrice(MathRound(price / 50.0) * 50.0);
   return MathAbs(price - strong);
}

void CountAgainstM1ClosesFromEntry(int windowMinutes, int &againstCount, int &maxAgainstStreak)
{
   againstCount = 0;
   maxAgainstStreak = 0;

   if(basketStartTime <= 0 || basketDirection == 0 || windowMinutes <= 0)
      return;

   datetime fromTime = basketStartTime;
   datetime minFrom = TimeCurrent() - (windowMinutes * 60);
   if(fromTime < minFrom)
      fromTime = minFrom;

   int startShift = iBarShift(_Symbol, PERIOD_M1, fromTime, false);
   if(startShift < 1)
      startShift = 1;

   int endShift = 1;
   int currentStreak = 0;
   for(int shift = startShift; shift >= endShift; --shift)
   {
      double closeCurr = iClose(_Symbol, PERIOD_M1, shift);
      double closePrev = iClose(_Symbol, PERIOD_M1, shift + 1);
      if(closeCurr <= 0.0 || closePrev <= 0.0)
         continue;

      bool against = false;
      if(basketDirection == 1)
         against = (closeCurr < closePrev);
      else if(basketDirection == -1)
         against = (closeCurr > closePrev);

      if(against)
      {
         againstCount++;
         currentStreak++;
         if(currentStreak > maxAgainstStreak)
            maxAgainstStreak = currentStreak;
      }
      else
      {
         currentStreak = 0;
      }
   }
}

long GetNearestRoundId(double price, double step)
{
   if(step <= 0.0) return 0;
   return (long)MathRound(price / step);
}

double GetProfitStepSize()
{
   double roundStep = ProfitStepUSD;
   if(roundStep <= 0.0) roundStep = AveragingStepUSD;
   if(roundStep <= 0.0) roundStep = 25.0;
   return roundStep;
}

double GetNextFavorableLevelPrice(double referencePrice)
{
   double roundStep = GetProfitStepSize();
   double nextLevel = 0.0;

   if(basketDirection == 1)
      nextLevel = MathCeil(referencePrice / roundStep) * roundStep;
   else
      nextLevel = MathFloor(referencePrice / roundStep) * roundStep;

   if(MathAbs(nextLevel - referencePrice) < 0.10)
   {
      if(basketDirection == 1) nextLevel += roundStep;
      else                     nextLevel -= roundStep;
   }

   for(int guard = 0; guard < 20; ++guard)
   {
      if(!IsFavorableLevelTouched(basketDirection, referencePrice, nextLevel))
         break;

      if(basketDirection == 1) nextLevel += roundStep;
      else                     nextLevel -= roundStep;
   }

   return NormalizePrice(nextLevel);
}

double ComputeBasketProjectedProfitAtPrice(double targetPrice)
{
   double totalProfit = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      ENUM_POSITION_TYPE posType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      ENUM_ORDER_TYPE orderType = (posType == POSITION_TYPE_BUY) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
      double volume = PositionGetDouble(POSITION_VOLUME);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double profit = 0.0;

      if(OrderCalcProfit(orderType, _Symbol, volume, openPrice, targetPrice, profit))
         totalProfit += profit;
   }

   return totalProfit;
}

double ComputeRequiredProfitForOneFullLevel()
{
   double weighted = 0.0;
   double totalLots = 0.0;
   double openProfit = 0.0;
   if(!GetBasketStats(weighted, totalLots, openProfit))
      return MinGlobalProfitUSDForBE;

   double breakEven = ComputeBreakEvenPrice();
   if(breakEven <= 0.0)
      breakEven = weighted;

   double nextLevel = GetNextFavorableLevelPrice(breakEven);
   double nextTol = GetLevelTouchWindow(nextLevel);
   double touchPrice = (basketDirection == 1)
      ? (nextLevel - nextTol)
      : (nextLevel + nextTol);
   touchPrice = NormalizePrice(touchPrice);

   double required = ComputeBasketProjectedProfitAtPrice(touchPrice);
   if(required < MinGlobalProfitUSDForBE)
      required = MinGlobalProfitUSDForBE;

   return required;
}

double GetRoundPriceFromId(long roundId, double step)
{
   return roundId * step;
}

void ResetBasketState()
{
   for(int i = 0; i < ArraySize(basketFavorableMilestonesHit); ++i)
      basketFavorableMilestonesHit[i] = false;

   basketDirection = 0;
   basketAnchorPrice = 0.0;
   basketStopPrice = 0.0;
   lastAvgReferencePrice = 0.0;
   nextProfitTriggerPrice = 0.0;
   profitActionArmed = false;
   profitStage = 0;
   basketEntryEquity = 0.0;
   basketEntryBalance = 0.0;
   hardDDTriggered = false;
   basketPendingFirstLevel = false;
   basketStartTime = 0;
   basketCloseReason = "";
   lastClosedRecoveredLevel = 0.0;
   basketMaxDDPct = 0.0;
   basketMaxDDAmount = 0.0;
   basketMaxAdverseDistanceUSD = 0.0;
   basketWorstPrice = 0.0;
   basketEntryLevelType = "";
   basketEntryLevelPrice = 0.0;
   basketSignalPrice = 0.0;
   basketFromSignal = false;
   basketLateSessionEntry = false;
   basketSignalChannel = "";
   basketSignalOpenMessageId = 0;
   basketSignalCloseMessageId = 0;
   basketSignalEntryDrift = 0.0;
   basketAverageCount = 0;
   basketRecoveredCount = 0;
   basketPartialCount = 0;
   basketAvgWeakCount = 0;
   basketAvgMidCount = 0;
   basketAvgStrongCount = 0;
   pendingStrongAvgReject = false;
   pendingStrongAvgLevel = 0.0;
   basketSignalOpenTime = 0;
   basketSignalDeclaredCloseTime = 0;
   basketSignalDeclaredDurationSec = 0;
   basketEntryHour = -1;
   basketEntryMinute = -1;
   basketEntryMarketPrice = 0.0;
   basketEntrySpreadUSD = 0.0;
   basketEntryDfmoStoch = 0.0;
   basketEntryDfmoRsi = 0.0;
   basketEntryDfmoOB = false;
   basketEntryDfmoOS = false;
   basketEntryAtrM1 = 0.0;
   basketEntryAtrM5 = 0.0;
   basketEntryTickVolM1 = 0.0;
   basketEntryTickVolRatio20 = 0.0;
   basketEntryMove3m = 0.0;
   basketEntryMove5m = 0.0;
   basketEntryMove15m = 0.0;
   basketEntryMoveAgainst3m = 0.0;
   basketEntryMoveAgainst5m = 0.0;
   basketEntryMoveAgainst15m = 0.0;
   basketEntryDistanceToLevel = 0.0;
   basketSignalDistanceToLevel = 0.0;
   basketSignalDistanceToStrong = 0.0;
   basketLastAdverseMilestoneUSD = 0;
   basketLastTimeMilestoneMin = 0;
   basketMaxFavorableDistanceUSD = 0.0;
   basketAdverse10Captured = false;
   basketAdverse10TimeMin = -1;
   basketMaxFavorableBeforeAdverse10USD = 0.0;
   basketFirstAvgCaptured = false;
   basketFirstAvgTimeMin = -1;
   basketFirstAvgLevelType = "";
   basketFirstAvgLevelPrice = 0.0;
   basketMaxFavorableBeforeFirstAvgUSD = 0.0;
   basketFirstAvgAgainstCount10m = 0;
   basketFirstAvgAgainstCount20m = 0;
   basketFirstAvgAgainstStreak10m = 0;
   basketFirstAvgAgainstStreak20m = 0;
}

void CaptureSignalFeatureSnapshot(TelegramSignalRow &row, double marketPrice, double entryLevelPrice, string entryLevelType)
{
   basketSignalOpenTime = row.openTime;
   basketSignalDeclaredCloseTime = row.closeTime;
   basketSignalDeclaredDurationSec = (row.hasClose && row.closeTime > row.openTime)
      ? (int)(row.closeTime - row.openTime)
      : 0;
   basketEntryMarketPrice = marketPrice;
   basketEntrySpreadUSD = MathMax(0.0, AskPrice() - BidPrice());

   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);
   basketEntryHour = tm.hour;
   basketEntryMinute = tm.min;

   double stochValue = 0.0;
   double rsiValue = 0.0;
   bool isOB = false;
   bool isOS = false;
   if(ReadDFMOSnapshot(stochValue, rsiValue, isOB, isOS))
   {
      basketEntryDfmoStoch = stochValue;
      basketEntryDfmoRsi = rsiValue;
      basketEntryDfmoOB = isOB;
      basketEntryDfmoOS = isOS;
   }

   basketEntryAtrM1 = GetAtrValueForTimeframe(PERIOD_M1);
   basketEntryAtrM5 = GetAtrValueForTimeframe(PERIOD_M5);
   basketEntryTickVolM1 = GetTickVolumeAt(PERIOD_M1, 0);
   double avgVol20 = GetAverageTickVolume(PERIOD_M1, 1, 20);
   basketEntryTickVolRatio20 = (avgVol20 > 0.0) ? (basketEntryTickVolM1 / avgVol20) : 0.0;

   double currentMid = NormalizePrice((BidPrice() + AskPrice()) * 0.5);
   basketEntryMove3m = GetRawMoveMinutes(3, currentMid);
   basketEntryMove5m = GetRawMoveMinutes(5, currentMid);
   basketEntryMove15m = GetRawMoveMinutes(15, currentMid);
   basketEntryMoveAgainst3m = GetAgainstSignalMove(row.direction, basketEntryMove3m);
   basketEntryMoveAgainst5m = GetAgainstSignalMove(row.direction, basketEntryMove5m);
   basketEntryMoveAgainst15m = GetAgainstSignalMove(row.direction, basketEntryMove15m);

   basketEntryDistanceToLevel = MathAbs(marketPrice - entryLevelPrice);
   basketSignalDistanceToLevel = MathAbs(row.openPrice - entryLevelPrice);
   basketSignalDistanceToStrong = GetDistanceToNearestStrongLevel(row.openPrice);

   TraceEvent("SIGNAL_FEATURES",
              "msg=" + IntegerToString((int)row.openMessageId) +
              ",entry_level_type=" + entryLevelType +
              ",entry_level=" + DoubleToString(entryLevelPrice, _Digits) +
              ",spread=" + DoubleToString(basketEntrySpreadUSD, _Digits) +
              ",dfmo_stoch=" + DoubleToString(basketEntryDfmoStoch, 2) +
              ",dfmo_rsi=" + DoubleToString(basketEntryDfmoRsi, 2) +
              ",atr_m1=" + DoubleToString(basketEntryAtrM1, _Digits) +
              ",atr_m5=" + DoubleToString(basketEntryAtrM5, _Digits) +
              ",tick_vol_m1=" + DoubleToString(basketEntryTickVolM1, 0) +
              ",tick_vol_ratio20=" + DoubleToString(basketEntryTickVolRatio20, 3) +
              ",move_3m=" + DoubleToString(basketEntryMove3m, _Digits) +
              ",move_5m=" + DoubleToString(basketEntryMove5m, _Digits) +
              ",move_15m=" + DoubleToString(basketEntryMove15m, _Digits) +
              ",against_3m=" + DoubleToString(basketEntryMoveAgainst3m, _Digits) +
              ",against_5m=" + DoubleToString(basketEntryMoveAgainst5m, _Digits) +
              ",against_15m=" + DoubleToString(basketEntryMoveAgainst15m, _Digits));
}

void WriteSignalFeatureRow(datetime closeTime, double realizedPnL, string reason,
                           double pctBalance, double pctEquity)
{
   if(!basketFromSignal)
      return;

   int durationMin = 0;
   if(basketStartTime > 0 && closeTime >= basketStartTime)
      durationMin = (int)((closeTime - basketStartTime) / 60);

   int declaredDurationMin = (basketSignalDeclaredDurationSec > 0)
      ? (basketSignalDeclaredDurationSec / 60)
      : 0;

   int lossFlag = (realizedPnL < 0.0) ? 1 : 0;
   int severeLossFlag = (pctEquity <= -1.0 || realizedPnL <= -250.0) ? 1 : 0;

   string line = TraceRunId + "," +
                 IntegerToString(basketId) + "," +
                 (basketDirection == 1 ? "BUY" : "SELL") + "," +
                 TimeToString(basketStartTime, TIME_DATE|TIME_SECONDS) + "," +
                 TimeToString(closeTime, TIME_DATE|TIME_SECONDS) + "," +
                 IntegerToString(durationMin) + "," +
                 CsvSafe(reason) + "," +
                 CsvSafe(basketSignalChannel) + "," +
                 IntegerToString((int)basketSignalOpenMessageId) + "," +
                 IntegerToString((int)basketSignalCloseMessageId) + "," +
                 TimeToString(basketSignalOpenTime, TIME_DATE|TIME_SECONDS) + "," +
                 TimeToString(basketSignalDeclaredCloseTime, TIME_DATE|TIME_SECONDS) + "," +
                 IntegerToString(declaredDurationMin) + "," +
                 IntegerToString(basketEntryHour) + "," +
                 IntegerToString(basketEntryMinute) + "," +
                 DoubleToString(basketSignalPrice, _Digits) + "," +
                 DoubleToString(basketEntryMarketPrice, _Digits) + "," +
                 DoubleToString(basketSignalEntryDrift, _Digits) + "," +
                 CsvSafe(basketEntryLevelType) + "," +
                 DoubleToString(basketEntryLevelPrice, _Digits) + "," +
                 DoubleToString(basketEntryDistanceToLevel, _Digits) + "," +
                 DoubleToString(basketSignalDistanceToLevel, _Digits) + "," +
                 DoubleToString(basketSignalDistanceToStrong, _Digits) + "," +
                 DoubleToString(basketEntrySpreadUSD, _Digits) + "," +
                 DoubleToString(basketEntryDfmoStoch, 2) + "," +
                 DoubleToString(basketEntryDfmoRsi, 2) + "," +
                 IntegerToString(basketEntryDfmoOB ? 1 : 0) + "," +
                 IntegerToString(basketEntryDfmoOS ? 1 : 0) + "," +
                 DoubleToString(basketEntryAtrM1, _Digits) + "," +
                 DoubleToString(basketEntryAtrM5, _Digits) + "," +
                 DoubleToString(basketEntryTickVolM1, 0) + "," +
                 DoubleToString(basketEntryTickVolRatio20, 3) + "," +
                 DoubleToString(basketEntryMove3m, _Digits) + "," +
                 DoubleToString(basketEntryMove5m, _Digits) + "," +
                 DoubleToString(basketEntryMove15m, _Digits) + "," +
                 DoubleToString(basketEntryMoveAgainst3m, _Digits) + "," +
                 DoubleToString(basketEntryMoveAgainst5m, _Digits) + "," +
                 DoubleToString(basketEntryMoveAgainst15m, _Digits) + "," +
                 IntegerToString(basketAdverse10Captured ? 1 : 0) + "," +
                 IntegerToString(basketAdverse10TimeMin) + "," +
                 DoubleToString(basketMaxFavorableBeforeAdverse10USD, _Digits) + "," +
                 IntegerToString(basketFirstAvgCaptured ? 1 : 0) + "," +
                 IntegerToString(basketFirstAvgTimeMin) + "," +
                 CsvSafe(basketFirstAvgLevelType) + "," +
                 DoubleToString(basketFirstAvgLevelPrice, _Digits) + "," +
                 DoubleToString(basketMaxFavorableBeforeFirstAvgUSD, _Digits) + "," +
                 IntegerToString(basketFirstAvgAgainstCount10m) + "," +
                 IntegerToString(basketFirstAvgAgainstCount20m) + "," +
                 IntegerToString(basketFirstAvgAgainstStreak10m) + "," +
                 IntegerToString(basketFirstAvgAgainstStreak20m) + "," +
                 IntegerToString(basketAverageCount) + "," +
                 IntegerToString(basketAvgWeakCount) + "," +
                 IntegerToString(basketAvgMidCount) + "," +
                 IntegerToString(basketAvgStrongCount) + "," +
                 IntegerToString(basketRecoveredCount) + "," +
                 IntegerToString(basketPartialCount) + "," +
                 DoubleToString(realizedPnL, 2) + "," +
                 DoubleToString(pctBalance, 3) + "," +
                 DoubleToString(pctEquity, 3) + "," +
                 DoubleToString(basketMaxAdverseDistanceUSD, _Digits) + "," +
                 DoubleToString(basketWorstPrice, _Digits) + "," +
                 DoubleToString(basketMaxDDPct, 3) + "," +
                 DoubleToString(basketMaxDDAmount, 2) + "," +
                 IntegerToString(lossFlag) + "," +
                 IntegerToString(severeLossFlag);
   AppendTextLine(SignalFeatureFileName, line);
}

void WriteTradeEventRow(string eventType)
{
   if(CountBasketPositions() <= 0 || basketStartTime <= 0)
      return;

   double weighted = 0.0;
   double totalLots = 0.0;
   double openProfit = 0.0;
   if(!GetBasketStats(weighted, totalLots, openProfit))
      return;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   double globalProfit = ComputeCurrentBasketGlobalProfit();
   int durationMin = (int)((TimeCurrent() - basketStartTime) / 60);

   double stochValue = 0.0;
   double rsiValue = 0.0;
   bool isOB = false;
   bool isOS = false;
   ReadDFMOSnapshot(stochValue, rsiValue, isOB, isOS);

   double atrM1 = GetAtrValueForTimeframe(PERIOD_M1);
   double atrM5 = GetAtrValueForTimeframe(PERIOD_M5);
   double tickVolM1 = GetTickVolumeAt(PERIOD_M1, 0);
   double avgVol20 = GetAverageTickVolume(PERIOD_M1, 1, 20);
   double tickVolRatio20 = (avgVol20 > 0.0) ? (tickVolM1 / avgVol20) : 0.0;

   double currentMid = NormalizePrice((BidPrice() + AskPrice()) * 0.5);
   double move3m = GetRawMoveMinutes(3, currentMid);
   double move5m = GetRawMoveMinutes(5, currentMid);
   double move15m = GetRawMoveMinutes(15, currentMid);
   double against3m = GetAgainstSignalMove(basketDirection, move3m);
   double against5m = GetAgainstSignalMove(basketDirection, move5m);
   double against15m = GetAgainstSignalMove(basketDirection, move15m);

   string line = TraceRunId + "," +
                 IntegerToString(basketId) + "," +
                 CsvSafe(eventType) + "," +
                 TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," +
                 IntegerToString(durationMin) + "," +
                 (basketDirection == 1 ? "BUY" : "SELL") + "," +
                 CsvSafe(basketEntryLevelType) + "," +
                 DoubleToString(basketEntryLevelPrice, _Digits) + "," +
                 DoubleToString(currentPrice, _Digits) + "," +
                 DoubleToString(weighted, _Digits) + "," +
                 DoubleToString(totalLots, 2) + "," +
                 DoubleToString(openProfit, 2) + "," +
                 DoubleToString(globalProfit, 2) + "," +
                 DoubleToString(basketMaxAdverseDistanceUSD, _Digits) + "," +
                 DoubleToString(basketWorstPrice, _Digits) + "," +
                 IntegerToString(basketAverageCount) + "," +
                 IntegerToString(basketAvgWeakCount) + "," +
                 IntegerToString(basketAvgMidCount) + "," +
                 IntegerToString(basketAvgStrongCount) + "," +
                 IntegerToString(basketRecoveredCount) + "," +
                 IntegerToString(basketPartialCount) + "," +
                 DoubleToString(stochValue, 2) + "," +
                 DoubleToString(rsiValue, 2) + "," +
                 IntegerToString(isOB ? 1 : 0) + "," +
                 IntegerToString(isOS ? 1 : 0) + "," +
                 DoubleToString(atrM1, _Digits) + "," +
                 DoubleToString(atrM5, _Digits) + "," +
                 DoubleToString(tickVolM1, 0) + "," +
                 DoubleToString(tickVolRatio20, 3) + "," +
                 DoubleToString(move3m, _Digits) + "," +
                 DoubleToString(move5m, _Digits) + "," +
                 DoubleToString(move15m, _Digits) + "," +
                 DoubleToString(against3m, _Digits) + "," +
                 DoubleToString(against5m, _Digits) + "," +
                 DoubleToString(against15m, _Digits);
   AppendTextLine(TradeEventFileName, line);
}

void TrackBasketDeteriorationMilestones()
{
   if(CountBasketPositions() <= 0 || basketStartTime <= 0)
      return;

   int durationMin = (int)((TimeCurrent() - basketStartTime) / 60);
   int timeMarks[6] = {15, 30, 60, 90, 120, 180};
   for(int i = 0; i < 6; ++i)
   {
      if(durationMin >= timeMarks[i] && basketLastTimeMilestoneMin < timeMarks[i])
      {
         basketLastTimeMilestoneMin = timeMarks[i];
         WriteTradeEventRow("time_" + IntegerToString(timeMarks[i]) + "m");
      }
   }

   int adverseMarks[6] = {10, 20, 30, 40, 50, 60};
   int currentMilestone = basketLastAdverseMilestoneUSD;
   for(int i = 0; i < 6; ++i)
   {
      if(basketMaxAdverseDistanceUSD >= adverseMarks[i] && currentMilestone < adverseMarks[i])
      {
         currentMilestone = adverseMarks[i];
         basketLastAdverseMilestoneUSD = adverseMarks[i];
         WriteTradeEventRow("adverse_" + IntegerToString(adverseMarks[i]) + "usd");
      }
   }
}

void TrackBasketFavorableMilestones()
{
   if(CountBasketPositions() <= 0 || basketStartTime <= 0 || basketDirection == 0)
      return;

   double weighted = 0.0;
   double totalLots = 0.0;
   double openProfit = 0.0;
   if(!GetBasketStats(weighted, totalLots, openProfit))
      return;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   double favorableDist = (basketDirection == 1)
      ? (currentPrice - weighted)
      : (weighted - currentPrice);

   int favorableMarks[10] = {2, 3, 4, 5, 6, 8, 10, 12, 15, 20};
   for(int i = 0; i < 10; ++i)
   {
      if(!basketFavorableMilestonesHit[i] && favorableDist >= favorableMarks[i])
      {
         basketFavorableMilestonesHit[i] = true;
         WriteTradeEventRow("fav_" + IntegerToString(favorableMarks[i]) + "usd");
      }
   }
}

double GetProfitSecureTriggerPrice(double referencePrice)
{
   if(basketDirection == 0) return 0.0;
   if(basketDirection == 1)
      return NormalizePrice(referencePrice + EffectiveProfitSecureDistanceUSD());
   return NormalizePrice(referencePrice - EffectiveProfitSecureDistanceUSD());
}

void ArmSecureProfitAction(double referencePrice)
{
   if(basketDirection == 0) return;
   nextProfitTriggerPrice = GetProfitSecureTriggerPrice(referencePrice);
   profitActionArmed = true;
}

void ArmNextLevelProfitAction(double referencePrice)
{
   if(basketDirection == 0) return;
   nextProfitTriggerPrice = GetNextFavorableLevelPrice(referencePrice);
   profitActionArmed = true;
}

void UpdateBasketStops(double slPrice)
{
   double tp = 0.0;
   double stopLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double adjustedSL = slPrice;
      if(type == POSITION_TYPE_BUY && BidPrice() - adjustedSL < stopLevel)
         adjustedSL = BidPrice() - stopLevel - (_Point * 2);
      if(type == POSITION_TYPE_SELL && adjustedSL - AskPrice() < stopLevel)
         adjustedSL = AskPrice() + stopLevel + (_Point * 2);

      adjustedSL = NormalizePrice(adjustedSL);
      if(adjustedSL <= 0.0) continue;
      trade.PositionModify(ticket, adjustedSL, tp);
   }
}

double ComputeBreakEvenPrice()
{
   double weighted = 0.0;
   double lots = 0.0;
   double profit = 0.0;
   if(!GetBasketStats(weighted, lots, profit))
      return 0.0;

   if(basketDirection == 1)
      return NormalizePrice(weighted + BreakEvenOffsetUSD);
   return NormalizePrice(weighted - BreakEvenOffsetUSD);
}

void ClosePartialBasket()
{
   double closeFactor = PartialClosePercent / 100.0;
   if(closeFactor <= 0.0) return;
   if(closeFactor > 1.0) closeFactor = 1.0;
   bool changed = false;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      double volume = PositionGetDouble(POSITION_VOLUME);
      double partial = NormalizeVolume(volume * closeFactor);
      double minVol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

      if(partial < minVol)
      {
         if(closeFactor >= 0.99)
         {
            trade.PositionClose(ticket);
            changed = true;
         }
         continue;
      }

      if(partial >= volume)
      {
         trade.PositionClose(ticket);
         changed = true;
      }
      else
      {
         trade.PositionClosePartial(ticket, partial);
         changed = true;
      }
   }

   if(changed)
   {
      basketPartialCount++;
      WriteTradeEventRow("partial_close");
   }
}

void ApplyInitialBasketStop()
{
   if(!EffectiveEnablePriceBasketStop()) return;
   if(basketDirection == 0 || basketAnchorPrice <= 0.0) return;

   if(basketDirection == 1)
      basketStopPrice = NormalizePrice(basketAnchorPrice - EffectiveBasketStopDistanceUSD());
   else
      basketStopPrice = NormalizePrice(basketAnchorPrice + EffectiveBasketStopDistanceUSD());
}

void RegisterNewBasketOrder(double fillPrice, int direction)
{
   if(basketDirection == 0)
   {
      basketDirection = direction;
      basketId++;
      basketStartTime = TimeCurrent();
      basketAnchorPrice = fillPrice;
      basketEntryEquity = AccountInfoDouble(ACCOUNT_EQUITY);
      basketEntryBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      basketWorstPrice = fillPrice;
      basketMaxAdverseDistanceUSD = 0.0;
      ApplyInitialBasketStop();
      TraceEvent("BASKET_OPENED",
                 "id=" + IntegerToString(basketId) +
                 ",direction=" + string(direction == 1 ? "BUY" : "SELL") +
                 ",anchor=" + DoubleToString(fillPrice, _Digits) +
                 ",entry_balance=" + DoubleToString(basketEntryBalance, 2) +
                 ",entry_equity=" + DoubleToString(basketEntryEquity, 2));
   }
   lastAvgReferencePrice = fillPrice;
   ArmSecureProfitAction(fillPrice);
}

void UpdateBasketAdverseDistance()
{
   if(basketDirection == 0 || basketAnchorPrice <= 0.0) return;
   if(CountBasketPositions() <= 0) return;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   double adverseDist = (basketDirection == 1)
      ? (basketAnchorPrice - currentPrice)
      : (currentPrice - basketAnchorPrice);
   double favorableDist = (basketDirection == 1)
      ? (currentPrice - basketAnchorPrice)
      : (basketAnchorPrice - currentPrice);

   if(favorableDist > basketMaxFavorableDistanceUSD)
      basketMaxFavorableDistanceUSD = favorableDist;

   if(!basketFirstAvgCaptured && favorableDist > basketMaxFavorableBeforeFirstAvgUSD)
      basketMaxFavorableBeforeFirstAvgUSD = favorableDist;

   if(!basketAdverse10Captured && favorableDist > basketMaxFavorableBeforeAdverse10USD)
      basketMaxFavorableBeforeAdverse10USD = favorableDist;

   if(basketWorstPrice <= 0.0)
      basketWorstPrice = basketAnchorPrice;

   bool newWorst = false;
   if(basketDirection == 1 && currentPrice < basketWorstPrice)
      newWorst = true;
   if(basketDirection == -1 && currentPrice > basketWorstPrice)
      newWorst = true;

   if(adverseDist > basketMaxAdverseDistanceUSD || newWorst)
   {
      if(adverseDist > basketMaxAdverseDistanceUSD)
         basketMaxAdverseDistanceUSD = MathMax(0.0, adverseDist);
      if(newWorst)
         basketWorstPrice = currentPrice;

      if(!basketAdverse10Captured && basketMaxAdverseDistanceUSD >= 10.0)
      {
         basketAdverse10Captured = true;
         basketAdverse10TimeMin = (int)((TimeCurrent() - basketStartTime) / 60);
         WriteTradeEventRow("first_adverse10");
      }

      TraceEvent("ADVERSE_UPDATE",
                 "anchor=" + DoubleToString(basketAnchorPrice, _Digits) +
                 ",current_price=" + DoubleToString(currentPrice, _Digits) +
                 ",max_adverse_usd=" + DoubleToString(basketMaxAdverseDistanceUSD, _Digits) +
                 ",worst_price=" + DoubleToString(basketWorstPrice, _Digits) +
                 ",direction=" + string(basketDirection == 1 ? "BUY" : "SELL"));
   }
}

void DetermineRoundLevelForLots(double levelPrice,
                                double weakLot,
                                double midLot,
                                double strongLot,
                                double &lot,
                                string &levelType)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   int mod50  = (int)MathRound(MathMod(levelPrice, 50.0));

   if(mod100 == 0)
   {
      lot = strongLot;
      levelType = "strong";
   }
   else if(mod50 == 0)
   {
      lot = midLot;
      levelType = "mid";
   }
   else
   {
      lot = weakLot;
      levelType = "weak";
   }
}

void DetermineEntryRoundLevel(double levelPrice, double &lot, string &levelType)
{
   DetermineRoundLevelForLots(levelPrice,
                              cachedEntryLotWeak,
                              cachedEntryLotMid,
                              cachedEntryLotStrong,
                              lot,
                              levelType);
}

void DetermineAverageRoundLevel(double levelPrice, double &lot, string &levelType)
{
   DetermineRoundLevelForLots(levelPrice,
                              cachedAvgLotWeak,
                              cachedAvgLotMid,
                              cachedAvgLotStrong,
                              lot,
                              levelType);
}

double GetNextAdverseLevelPrice(double referencePrice, int direction)
{
   if(direction == -1)
   {
      double nextRound = MathCeil(referencePrice / AveragingStepUSD) * AveragingStepUSD;
      if(MathAbs(nextRound - referencePrice) < 0.10) nextRound += AveragingStepUSD;
      return NormalizePrice(nextRound);
   }

   double nextRound = MathFloor(referencePrice / AveragingStepUSD) * AveragingStepUSD;
   if(MathAbs(nextRound - referencePrice) < 0.10) nextRound -= AveragingStepUSD;
   return NormalizePrice(nextRound);
}

bool OpenBasketOrder(int direction, double lots, string comment)
{
   lots = NormalizeVolume(lots);
   if(lots <= 0.0) return false;

   bool ok = (direction == 1)
      ? trade.Buy(lots, _Symbol, 0.0, 0.0, 0.0, comment)
      : trade.Sell(lots, _Symbol, 0.0, 0.0, 0.0, comment);
   if(!ok) return false;

   uint rc = trade.ResultRetcode();
   if(rc != 10008 && rc != 10009) return false;

   double fillPrice = trade.ResultPrice();
   RegisterNewBasketOrder(fillPrice, direction);
   ApplyInitialBasketStop();
   return true;
}

int OpenBasketOrderUnits(int direction, double totalLot, string commentPrefix)
{
   totalLot = NormalizeVolume(totalLot);
   if(totalLot <= 0.0) return 0;

   double unitLot = cachedUnitLot;
   if(unitLot <= 0.0)
      unitLot = NormalizeVolume(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN));
   if(unitLot <= 0.0) return 0;

   int targetOrders = (int)MathRound(totalLot / unitLot);
   if(targetOrders < 1) targetOrders = 1;

   double remaining = totalLot;
   int opened = 0;

   for(int i = 0; i < targetOrders; ++i)
   {
      double chunk = (i < targetOrders - 1) ? unitLot : NormalizeVolume(remaining);
      if(chunk <= 0.0) continue;

      string comment = commentPrefix + "_" + IntegerToString(i + 1);
      if(OpenBasketOrder(direction, chunk, comment))
      {
         opened++;
         remaining = NormalizeVolume(remaining - chunk);
      }
   }

   return opened;
}

bool OpenInitialBasketEntry(int direction, double entryLevelPrice)
{
   if(UseDynamicRiskModel)
      RefreshRiskModel();
   else
   {
      RebuildLevelLotsFromBase(0.0);
      cachedRange = (GetChannelTag() == "V") ? FixedRangeVikingo : FixedRangeTrueTrading;
   }

   double totalLot = 0.0;
   string entryLevelType = "";
   DetermineEntryRoundLevel(entryLevelPrice, totalLot, entryLevelType);
   if(entryLevelType == "strong")
      totalLot = cachedAvgLotWeak;
   if(totalLot <= 0.0) return false;

   double unitLot = cachedUnitLot;
   if(unitLot <= 0.0) unitLot = NormalizeVolume(totalLot / 3.0);
   int opened = OpenBasketOrderUnits(direction, totalLot, "MKT_" + DoubleToString(entryLevelPrice, 0));

   if(opened > 0)
   {
      basketEntryLevelPrice = entryLevelPrice;
      basketEntryLevelType = entryLevelType;
      if(!UseCompactBacktestLogging())
         Print("INITIAL BASKET ", (direction == 1 ? "BUY" : "SELL"),
               " level_type=", entryLevelType,
               " level=", DoubleToString(entryLevelPrice, 0),
               " total_lot=", DoubleToString(totalLot, 2),
               " orders=", IntegerToString(opened),
               " unit=", DoubleToString(unitLot, 2),
               " entry_weak=", DoubleToString(cachedEntryLotWeak, 2),
               " entry_mid=", DoubleToString(cachedEntryLotMid, 2),
               " entry_strong=", DoubleToString(cachedEntryLotStrong, 2),
               " avg_weak=", DoubleToString(cachedAvgLotWeak, 2),
               " avg_mid=", DoubleToString(cachedAvgLotMid, 2),
               " avg_strong=", DoubleToString(cachedAvgLotStrong, 2),
               " R=", DoubleToString(cachedRange, 1));
      TraceEvent("INITIAL_BASKET",
                 (direction == 1 ? "BUY" : "SELL") +
                 ",level_type=" + entryLevelType +
                 ",level=" + DoubleToString(entryLevelPrice, 0) +
                 ",total_lot=" + DoubleToString(totalLot, 2) +
                 ",orders=" + IntegerToString(opened) +
                 ",entry_weak=" + DoubleToString(cachedEntryLotWeak, 2) +
                 ",entry_mid=" + DoubleToString(cachedEntryLotMid, 2) +
                 ",entry_strong=" + DoubleToString(cachedEntryLotStrong, 2));
      return true;
   }

   return false;
}

bool OpenInitialSignalBasketEntry(int direction, double signalPrice, string channelTag, long messageId)
{
   double marketPrice = (direction == 1) ? AskPrice() : BidPrice();
   double drift = MathAbs(marketPrice - signalPrice);
   double maxDrift = EffectiveMaxSignalEntryPriceDriftUSD();
   if(signalPrice > 0.0 && maxDrift > 0.0 && drift > maxDrift)
   {
      TraceEvent("SIGNAL_ENTRY_SKIPPED_DRIFT",
                 "channel=" + channelTag +
                 ",message_id=" + IntegerToString((int)messageId) +
                 ",direction=" + string(direction == 1 ? "BUY" : "SELL") +
                 ",signal_price=" + DoubleToString(signalPrice, _Digits) +
                 ",market_price=" + DoubleToString(marketPrice, _Digits) +
                 ",drift=" + DoubleToString(drift, _Digits) +
                 ",max_drift=" + DoubleToString(maxDrift, 2));
      if(!UseCompactBacktestLogging())
         Print("SIGNAL ENTRY SKIPPED drift msg=", messageId,
               " signal=", DoubleToString(signalPrice, _Digits),
               " market=", DoubleToString(marketPrice, _Digits),
               " drift=", DoubleToString(drift, _Digits));
      return false;
   }

   // Initialize risk model (needed for cachedUnitLot and avg lots)
   if(UseDynamicRiskModel)
      RefreshRiskModel();
   else
   {
      RebuildLevelLotsFromBase(0.0);
      cachedRange = (GetChannelTag() == "V") ? FixedRangeVikingo : FixedRangeTrueTrading;
   }

   // NO trade opened — just set up basket state, wait for first adverse level
   basketDirection = direction;
   basketId++;
   basketStartTime = TimeCurrent();
   basketAnchorPrice = marketPrice;
   basketEntryEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   basketEntryBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   basketWorstPrice = marketPrice;
   basketMaxAdverseDistanceUSD = 0.0;
   basketPendingFirstLevel = true;
   ApplyInitialBasketStop();

   basketFromSignal = true;
   basketSignalChannel = channelTag;
   basketSignalOpenMessageId = messageId;
   basketSignalPrice = signalPrice;
   basketSignalEntryDrift = drift;
   basketEntryLevelType = "pending_level";
   basketEntryMarketPrice = marketPrice;
   if(tgSignalCursor >= 0 && tgSignalCursor < tgSignalCount)
   {
      TelegramSignalRow snap = tgSignals[tgSignalCursor];
      CaptureSignalFeatureSnapshot(snap, marketPrice, 0.0, "pending_level");
   }
   WriteTradeEventRow("entry_signal_pending");
   TraceEvent("SIGNAL_ENTRY_PENDING",
              "channel=" + channelTag +
              ",message_id=" + IntegerToString((int)messageId) +
              ",direction=" + string(direction == 1 ? "BUY" : "SELL") +
              ",time_basis=" + GetSignalTimeBasisLabel() +
              ",clock_now=" + TimeToString(GetSignalClockTime(), TIME_DATE|TIME_SECONDS) +
              ",signal_price=" + DoubleToString(signalPrice, 2) +
              ",market_price=" + DoubleToString(marketPrice, 2) +
              ",drift=" + DoubleToString(drift, 2));
   return true;
}

int ClosePositionsAtEntryPrice(double targetPrice)
{
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      double posOpen = PositionGetDouble(POSITION_PRICE_OPEN);
      if(MathAbs(posOpen - targetPrice) >= (_Point * 0.5)) continue;

      // A recovered block must never be closed while still negative.
      double posProfit = PositionGetDouble(POSITION_PROFIT);
      if(posProfit < 0.0) continue;

      if(trade.PositionClose(ticket))
         closed++;
   }
   if(closed > 0)
   {
      basketRecoveredCount += closed;
      WriteTradeEventRow("recovered_close");
   }
   return closed;
}

bool ManageUnitNetCompensation()
{
   int total = CountBasketPositions();
   if(total <= 1) return false;

   double globalProfit = ComputeCurrentBasketGlobalProfit();
   if(globalProfit <= 0.0) return false;

   ulong positiveTickets[];
   double positiveProfits[];
   ulong negativeTickets[];
   double negativeProfits[];
   int positiveCount = 0;
   int negativeCount = 0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      double profit = PositionGetDouble(POSITION_PROFIT);
      if(profit > 0.0)
      {
         ArrayResize(positiveTickets, positiveCount + 1);
         ArrayResize(positiveProfits, positiveCount + 1);
         positiveTickets[positiveCount] = ticket;
         positiveProfits[positiveCount] = profit;
         positiveCount++;
      }
      else if(profit < 0.0)
      {
         ArrayResize(negativeTickets, negativeCount + 1);
         ArrayResize(negativeProfits, negativeCount + 1);
         negativeTickets[negativeCount] = ticket;
         negativeProfits[negativeCount] = profit;
         negativeCount++;
      }
   }

   int netPositiveUnits = positiveCount - negativeCount;
   if(negativeCount <= 0 || netPositiveUnits < 1 || positiveCount <= 1)
      return false;

   for(int a = 0; a < positiveCount - 1; ++a)
   {
      for(int b = a + 1; b < positiveCount; ++b)
      {
         if(positiveProfits[b] > positiveProfits[a])
         {
            double pTmp = positiveProfits[a];
            positiveProfits[a] = positiveProfits[b];
            positiveProfits[b] = pTmp;

            ulong tTmp = positiveTickets[a];
            positiveTickets[a] = positiveTickets[b];
            positiveTickets[b] = tTmp;
         }
      }
   }

   int closedNeg = 0;
   int closedPos = 0;

   for(int i = 0; i < negativeCount; ++i)
   {
      if(trade.PositionClose(negativeTickets[i]))
         closedNeg++;
   }

   for(int i = 1; i < positiveCount; ++i)
   {
      if(trade.PositionClose(positiveTickets[i]))
         closedPos++;
   }

   profitActionArmed = false;
   profitStage = 0;
   nextProfitTriggerPrice = 0.0;

   int remaining = CountBasketPositions();
   if(remaining == 1)
      ArmSecureProfitAction((basketDirection == 1) ? BidPrice() : AskPrice());

   TraceEvent("UNIT_NET_COMPENSATION",
              "global_profit=" + DoubleToString(globalProfit, 2) +
              ",positive_count=" + IntegerToString(positiveCount) +
              ",negative_count=" + IntegerToString(negativeCount) +
              ",net_positive_units=" + IntegerToString(netPositiveUnits) +
              ",closed_positive=" + IntegerToString(closedPos) +
              ",closed_negative=" + IntegerToString(closedNeg) +
              ",remaining=" + IntegerToString(remaining) +
              ",next_target=" + DoubleToString(nextProfitTriggerPrice, _Digits));
   if(closedNeg > 0 || closedPos > 0)
      WriteTradeEventRow("unit_net_comp");
   return (closedNeg > 0 || closedPos > 0);
}

void EvaluateRemainingBasketAfterRecovery(double currentPrice, double recoveredLevel)
{
   int remaining = CountBasketPositions();
   if(remaining <= 0) return;

   // After closing a recovered level, any previously armed target may belong
   // to the old basket structure, so we always rebuild profit management.
   profitActionArmed = false;
   profitStage = 0;
   nextProfitTriggerPrice = 0.0;

   double globalProfit = ComputeCurrentBasketGlobalProfit();
   int positiveCount = 0;
   int negativeCount = 0;
   double leastFavorablePositiveEntry = 0.0;
   GetBasketUnitState(positiveCount, negativeCount, leastFavorablePositiveEntry);

   if(remaining == 1)
   {
      ArmSecureProfitAction(currentPrice);
      TraceEvent("RECOVERY_SINGLE_REARM",
                 "recovered_level=" + DoubleToString(recoveredLevel, _Digits) +
                 ",price=" + DoubleToString(currentPrice, _Digits) +
                 ",global_profit=" + DoubleToString(globalProfit, 2) +
                 ",next_target=" + DoubleToString(nextProfitTriggerPrice, _Digits));
      return;
   }

   if(positiveCount > 0 && negativeCount == 0 && leastFavorablePositiveEntry > 0.0)
   {
      profitStage = 0;
      nextProfitTriggerPrice = GetNextFavorableLevelPrice(leastFavorablePositiveEntry);
      profitActionArmed = true;
      TraceEvent("RECOVERY_POSITIVE_ONLY_REARM",
                 "recovered_level=" + DoubleToString(recoveredLevel, _Digits) +
                 ",price=" + DoubleToString(currentPrice, _Digits) +
                 ",global_profit=" + DoubleToString(globalProfit, 2) +
                 ",positive_count=" + IntegerToString(positiveCount) +
                 ",next_target=" + DoubleToString(nextProfitTriggerPrice, _Digits));
      return;
   }

   TraceEvent("RECOVERY_MULTI_CONTINUE",
              "recovered_level=" + DoubleToString(recoveredLevel, _Digits) +
              ",price=" + DoubleToString(currentPrice, _Digits) +
              ",global_profit=" + DoubleToString(globalProfit, 2) +
              ",remaining=" + IntegerToString(remaining));
}

void ManageRecoveredLevels()
{
   recoveredManagedFlag = false;
   if(basketDirection == 0 || CountBasketPositions() <= 1) return;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   double openPrices[];
   int priceCount = 0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      if(openPrice <= 0.0) continue;

      bool exists = false;
      for(int j = 0; j < priceCount; ++j)
      {
         if(MathAbs(openPrices[j] - openPrice) < (_Point * 0.5))
         {
            exists = true;
            break;
         }
      }
      if(!exists && priceCount < 64)
      {
         ArrayResize(openPrices, priceCount + 1);
         openPrices[priceCount] = openPrice;
         priceCount++;
      }
   }

   if(priceCount < 2) return;

   ArraySort(openPrices);

   for(int idx = 0; idx < priceCount; ++idx)
   {
      double entryPrice = openPrices[idx];
      bool hasMoreAdverse = false;

      for(int j = 0; j < priceCount; ++j)
      {
         if(basketDirection == -1 && openPrices[j] > entryPrice + (_Point * 0.5))
         {
            hasMoreAdverse = true;
            break;
         }
         if(basketDirection == 1 && openPrices[j] < entryPrice - (_Point * 0.5))
         {
            hasMoreAdverse = true;
            break;
         }
      }

      if(!hasMoreAdverse) continue;

      bool recovered = IsRecoveredLevelReachedExact(basketDirection, currentPrice, entryPrice);
      if(!recovered) continue;

      int closed = ClosePositionsAtEntryPrice(entryPrice);

      if(!UseCompactBacktestLogging())
         Print("RECOVERED ENTRY CLOSE entry=", DoubleToString(entryPrice, _Digits),
               " current=", DoubleToString(currentPrice, _Digits));
      TraceEvent("RECOVERED_ENTRY_CLOSE",
                 "entry=" + DoubleToString(entryPrice, _Digits) +
                 ",current=" + DoubleToString(currentPrice, _Digits) +
                 ",closed_positions=" + IntegerToString(closed));
      lastClosedRecoveredLevel = entryPrice;
      EvaluateRemainingBasketAfterRecovery(currentPrice, entryPrice);
      recoveredManagedFlag = true;
      return;
   }
}

void CloseAllBasketPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurBasketPosition()) continue;
      trade.PositionClose(ticket);
   }
}

void ManageHardDrawdown()
{
   if(!EffectiveEnableHardCloseDD()) return;
   if(basketDirection == 0 || hardDDTriggered || basketEntryEquity <= 0.0) return;

   double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(currentEquity <= 0.0) return;

   double ddAmount = basketEntryEquity - currentEquity;
   double ddPct = (ddAmount / basketEntryEquity) * 100.0;
   if(ddPct > basketMaxDDPct)
   {
      basketMaxDDPct = ddPct;
      basketMaxDDAmount = ddAmount;
      TraceEvent("DD_UPDATE",
                 "current_dd_pct=" + DoubleToString(ddPct, 3) +
                 ",current_dd_amount=" + DoubleToString(ddAmount, 2) +
                 ",max_dd_pct=" + DoubleToString(basketMaxDDPct, 3) +
                 ",max_dd_amount=" + DoubleToString(basketMaxDDAmount, 2) +
                 ",entry_equity=" + DoubleToString(basketEntryEquity, 2) +
                 ",current_equity=" + DoubleToString(currentEquity, 2));
   }
   if(ddPct < HardCloseDDPct) return;

   hardDDTriggered = true;
   basketCloseReason = "hard_dd";
   if(!UseCompactBacktestLogging())
      Print("HARD DD CLOSE dd=", DoubleToString(ddPct, 2), "% entryEq=", DoubleToString(basketEntryEquity, 2),
            " currentEq=", DoubleToString(currentEquity, 2));
   TraceEvent("HARD_DD_CLOSE",
              "dd_pct=" + DoubleToString(ddPct, 2) +
              ",entry_equity=" + DoubleToString(basketEntryEquity, 2) +
              ",current_equity=" + DoubleToString(currentEquity, 2));
   CloseAllBasketPositions();
}

bool ManagePriceBasketStop()
{
   if(!EffectiveEnablePriceBasketStop()) return false;
   if(basketDirection == 0 || basketStopPrice <= 0.0) return false;
   if(CountBasketPositions() <= 0) return false;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   double currentAdverseDist = (basketDirection == 1)
      ? (basketAnchorPrice - currentPrice)
      : (currentPrice - basketAnchorPrice);

   if(basketLateSessionEntry)
   {
      bool lateStopHit = (basketDirection == 1)
         ? (currentPrice <= basketStopPrice)
         : (currentPrice >= basketStopPrice);

      if(!lateStopHit)
         return false;

      basketCloseReason = "price_basket_stop";
      TraceEvent("PRICE_BASKET_STOP",
                 "price=" + DoubleToString(currentPrice, _Digits) +
                 ",stop=" + DoubleToString(basketStopPrice, _Digits) +
                 ",mode=late_100_fixed_from_level" +
                 ",anchor=" + DoubleToString(basketAnchorPrice, _Digits) +
                 ",entry_level=" + DoubleToString(basketEntryLevelPrice, _Digits) +
                 ",current_adverse_usd=" + DoubleToString(currentAdverseDist, _Digits) +
                 ",direction=" + string(basketDirection == 1 ? "BUY" : "SELL"));
      if(!UseCompactBacktestLogging())
         Print("PRICE BASKET STOP late100 hit=", DoubleToString(currentPrice, _Digits),
               " stop=", DoubleToString(basketStopPrice, _Digits),
               " level=", DoubleToString(basketEntryLevelPrice, _Digits),
               " anchor=", DoubleToString(basketAnchorPrice, _Digits),
               " adverseUSD=", DoubleToString(currentAdverseDist, _Digits),
               " direction=", (basketDirection == 1 ? "BUY" : "SELL"));

      CloseAllBasketPositions();
      activeTelegramSignalIndex = -1;
      activeTelegramSignalCloseTime = 0;
      activeTelegramSignalHasClose = false;
      activeTelegramSignalCloseMessageId = 0;
      return true;
   }

   double referenceLevel = GetBasketStructuralReferenceLevel();
   double strongLevel = GetNextAdverseStrongLevelFrom(referenceLevel, basketDirection);
   if(strongLevel <= 0.0)
      return false;

   double breakConfirmDistance = EffectiveStructuralBreakConfirmUSD();
   double breakConfirmPrice = (basketDirection == 1)
      ? NormalizePrice(strongLevel - breakConfirmDistance)
      : NormalizePrice(strongLevel + breakConfirmDistance);

   bool structuralBreak = false;
   if(basketDirection == 1)
      structuralBreak = (currentPrice <= breakConfirmPrice);
   else
      structuralBreak = (currentPrice >= breakConfirmPrice);

   if(!structuralBreak)
      return false;

   basketCloseReason = "price_basket_stop";
   WriteTradeEventRow("pre_close_price_basket_stop");
   TraceEvent("PRICE_BASKET_STOP",
              "price=" + DoubleToString(currentPrice, _Digits) +
              ",stop=" + DoubleToString(basketStopPrice, _Digits) +
              ",mode=strong_plus_" + DoubleToString(breakConfirmDistance, 0) + "usd" +
              ",anchor=" + DoubleToString(basketAnchorPrice, _Digits) +
              ",reference_level=" + DoubleToString(referenceLevel, _Digits) +
              ",broken_strong=" + DoubleToString(strongLevel, _Digits) +
              ",break_confirm=" + DoubleToString(breakConfirmPrice, _Digits) +
              ",current_adverse_usd=" + DoubleToString(currentAdverseDist, _Digits) +
              ",direction=" + string(basketDirection == 1 ? "BUY" : "SELL"));
   if(!UseCompactBacktestLogging())
      Print("PRICE BASKET STOP hit=", DoubleToString(currentPrice, _Digits),
            " stop=", DoubleToString(basketStopPrice, _Digits),
            " anchor=", DoubleToString(basketAnchorPrice, _Digits),
            " referenceLevel=", DoubleToString(referenceLevel, _Digits),
            " brokenStrong=", DoubleToString(strongLevel, _Digits),
            " breakConfirm=", DoubleToString(breakConfirmPrice, _Digits),
            " adverseUSD=", DoubleToString(currentAdverseDist, _Digits),
            " direction=", (basketDirection == 1 ? "BUY" : "SELL"));

   CloseAllBasketPositions();
   activeTelegramSignalIndex = -1;
   activeTelegramSignalCloseTime = 0;
   activeTelegramSignalHasClose = false;
   activeTelegramSignalCloseMessageId = 0;
   return true;
}

void UpdateRunDrawdown()
{
   double currentEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(currentEquity <= 0.0)
      return;

   if(runPeakEquity <= 0.0 || currentEquity > runPeakEquity)
      runPeakEquity = currentEquity;

   double ddAmount = runPeakEquity - currentEquity;
   double ddPct = (runPeakEquity > 0.0) ? (ddAmount / runPeakEquity * 100.0) : 0.0;
   if(ddPct <= runMaxDDPct)
      return;

   runMaxDDPct = ddPct;
   runMaxDDAmount = ddAmount;
   TraceEvent("ACCOUNT_DD_UPDATE",
              "peak_equity=" + DoubleToString(runPeakEquity, 2) +
              ",current_equity=" + DoubleToString(currentEquity, 2) +
              ",dd_pct=" + DoubleToString(runMaxDDPct, 3) +
              ",dd_amount=" + DoubleToString(runMaxDDAmount, 2));
}

void AdvanceTelegramSignalCursor(datetime nowTime)
{
   while(tgSignalCursor < tgSignalCount)
   {
      TelegramSignalRow row = tgSignals[tgSignalCursor];
      if(row.hasClose && row.closeTime < nowTime)
      {
         TraceEvent("SIGNAL_SKIPPED_EXPIRED",
                    "channel=" + row.channelTag +
                    ",message_id=" + IntegerToString((int)row.openMessageId) +
                    ",open_time=" + TimeToString(row.openTime, TIME_DATE|TIME_SECONDS) +
                    ",close_time=" + TimeToString(row.closeTime, TIME_DATE|TIME_SECONDS));
         tgSignalCursor++;
         continue;
      }
      break;
   }
}

bool ManageTelegramSignalClose()
{
   if(!UseTelegramSignalCsvEntries || !CloseOnTelegramSignalClose)
      return false;

   if(activeTelegramSignalIndex < 0 || activeTelegramSignalIndex >= tgSignalCount)
      return false;

   if(!activeTelegramSignalHasClose || activeTelegramSignalCloseTime <= 0)
      return false;

   datetime nowTime = GetSignalClockTime();
   if(nowTime < activeTelegramSignalCloseTime)
      return false;

   if(CountBasketPositions() <= 0)
   {
      // Cancel pending basket if signal closes before first level reached
      if(basketPendingFirstLevel)
      {
         basketPendingFirstLevel = false;
         basketCloseReason = "signal_close_pending";
         FinalizeBasket();
         ResetBasketState();
      }
      activeTelegramSignalIndex = -1;
      activeTelegramSignalCloseTime = 0;
      activeTelegramSignalHasClose = false;
      activeTelegramSignalCloseMessageId = 0;
      return false;
   }

   TelegramSignalRow row = tgSignals[activeTelegramSignalIndex];
   double marketPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   basketCloseReason = "signal_close";
   basketSignalCloseMessageId = row.closeMessageId;
   WriteTradeEventRow("pre_close_signal");
   TraceEvent("SIGNAL_FORCE_CLOSE",
              "channel=" + row.channelTag +
              ",open_message_id=" + IntegerToString((int)row.openMessageId) +
              ",close_message_id=" + IntegerToString((int)row.closeMessageId) +
              ",time_basis=" + GetSignalTimeBasisLabel() +
              ",clock_now=" + TimeToString(nowTime, TIME_DATE|TIME_SECONDS) +
              ",close_time=" + TimeToString(activeTelegramSignalCloseTime, TIME_DATE|TIME_SECONDS) +
              ",reason=" + row.closeReason +
              ",market_price=" + DoubleToString(marketPrice, _Digits));
   TraceEvent("SIGNAL_CLOSE_DIAG",
              BuildSignalTimingDiag(row, nowTime, marketPrice) +
              ",signal_close_time=" + TimeToString(activeTelegramSignalCloseTime, TIME_DATE|TIME_SECONDS) +
              ",signal_close_message_id=" + IntegerToString((int)row.closeMessageId));
   if(!UseCompactBacktestLogging())
      Print("SIGNAL CLOSE ", row.channelTag, " open_msg=", row.openMessageId,
            " close_msg=", row.closeMessageId,
            " time=", TimeToString(activeTelegramSignalCloseTime, TIME_DATE|TIME_SECONDS));
   CloseAllBasketPositions();
   activeTelegramSignalIndex = -1;
   activeTelegramSignalCloseTime = 0;
   activeTelegramSignalHasClose = false;
   activeTelegramSignalCloseMessageId = 0;
   return true;
}

void TryOpenTelegramSignalEntry()
{
   if(!UseTelegramSignalCsvEntries)
      return;
   if(CountBasketPositions() > 0 || basketPendingFirstLevel)
      return;
   if(tgSignalCount <= 0)
      return;

   datetime nowTime = GetSignalClockTime();
   AdvanceTelegramSignalCursor(nowTime);
   if(tgSignalCursor >= tgSignalCount)
      return;

   TelegramSignalRow row = tgSignals[tgSignalCursor];
   if(nowTime < row.openTime)
      return;

   double signalPrice = row.openPrice;
   if(signalPrice <= 0.0)
      signalPrice = (row.direction == 1) ? AskPrice() : BidPrice();
   double marketPrice = (row.direction == 1) ? AskPrice() : BidPrice();

   bool inRegularSession = IsEntrySessionAllowed(row.openTime);
   bool isNyOpenBlocked = IsNyOpenBlockedWindow(row.openTime);
   bool isLateSignal = (!inRegularSession && IsLateEntryWindow(row.openTime));

   if(isNyOpenBlocked)
   {
      TraceEvent("SIGNAL_ENTRY_SKIPPED_NY_OPEN",
                 "channel=" + row.channelTag +
                 ",message_id=" + IntegerToString((int)row.openMessageId) +
                 ",signal_open_time=" + TimeToString(row.openTime, TIME_DATE|TIME_SECONDS) +
                 ",blocked_window=14-16");
      tgSignalCursor++;
      return;
   }

   if(!inRegularSession && !isLateSignal)
   {
      TraceEvent("SIGNAL_ENTRY_SKIPPED_SESSION",
                 "channel=" + row.channelTag +
                 ",message_id=" + IntegerToString((int)row.openMessageId) +
                 ",signal_open_time=" + TimeToString(row.openTime, TIME_DATE|TIME_SECONDS) +
                 ",session=" + IntegerToString(EntrySessionStartHour) + "-" + IntegerToString(EntrySessionEndHour));
      tgSignalCursor++;
      return;
   }

   if(isLateSignal)
   {
      double lateLevel = ResolveLateSignalEntryLevel(row.direction, marketPrice);
      if(lateLevel <= 0.0)
         return;

      if(OpenPendingTelegramSignalEntry(row, lateLevel, marketPrice))
      {
         activeTelegramSignalIndex = tgSignalCursor;
         activeTelegramSignalCloseTime = row.closeTime;
         activeTelegramSignalHasClose = row.hasClose;
         activeTelegramSignalCloseMessageId = row.closeMessageId;
         tgSignalCursor++;
      }
      return;
   }

   TraceEvent("SIGNAL_ENTRY_DIAG",
              BuildSignalTimingDiag(row, nowTime, marketPrice));

   if(OpenInitialSignalBasketEntry(row.direction, signalPrice, row.channelTag, row.openMessageId))
   {
      activeTelegramSignalIndex = tgSignalCursor;
      activeTelegramSignalCloseTime = row.closeTime;
      activeTelegramSignalHasClose = row.hasClose;
      activeTelegramSignalCloseMessageId = row.closeMessageId;
   }
   else
   {
      TraceEvent("SIGNAL_ENTRY_FAILED",
                 "channel=" + row.channelTag +
                 ",message_id=" + IntegerToString((int)row.openMessageId) +
                 ",direction=" + string(row.direction == 1 ? "BUY" : "SELL") +
                 ",time_basis=" + GetSignalTimeBasisLabel() +
                 ",clock_now=" + TimeToString(nowTime, TIME_DATE|TIME_SECONDS) +
                 ",signal_open_time=" + TimeToString(row.openTime, TIME_DATE|TIME_SECONDS));
   }

   tgSignalCursor++;
}

void TryOpenInitialEntry()
{
   if(CountBasketPositions() > 0) return;
   if(TimeCurrent() - lastEntryTime < MinSecondsBetweenEntries) return;
   if(!IsEntrySessionAllowed(TimeCurrent())) return;

   bool isOB, isOS;
   if(!ReadDFMOState(isOB, isOS)) return;

   int signal = 0;
   if(isOS && AllowBuyFromOversoldBand) signal = 1;
   if(isOB && AllowSellFromOverboughtBand) signal = -1;
   if(signal == 0) return;

   double tradePrice = (signal == 1) ? AskPrice() : BidPrice();
   long roundId = GetNearestRoundId(tradePrice, EntryRoundStepUSD);
   double roundPrice = GetRoundPriceFromId(roundId, EntryRoundStepUSD);
   double entryTol = MathMin(EntryWindowUSD, GetLevelTouchWindow(roundPrice));
   if(MathAbs(tradePrice - roundPrice) > entryTol) return;

   datetime barTime = iTime(_Symbol, SignalTimeframe, 0);
   if(roundId == lastEntryRoundId && signal == lastEntrySignal && barTime == lastManagedBarTime)
      return;

   if(OpenInitialBasketEntry(signal, roundPrice))
   {
      lastEntryTime = TimeCurrent();
      lastEntryRoundId = roundId;
      lastEntrySignal = signal;
      lastManagedBarTime = barTime;
      if(!UseCompactBacktestLogging())
         Print("ENTRY ", (signal == 1 ? "BUY" : "SELL"), " round=", roundPrice, " price=", tradePrice);
      TraceEvent("ENTRY",
                 string(signal == 1 ? "BUY" : "SELL") +
                 ",round=" + DoubleToString(roundPrice, 0) +
                 ",price=" + DoubleToString(tradePrice, _Digits));
   }
}

void TryOpenAveraging()
{
   if(basketDirection == 0) return;
   if(CountBasketPositions() <= 0 && !basketPendingFirstLevel) { ResetBasketState(); return; }
   if(basketLateSessionEntry) return;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   double adverseDist = (basketDirection == 1)
      ? (basketAnchorPrice - currentPrice)
      : (currentPrice - basketAnchorPrice);
   if(adverseDist <= 0.0) return;

   bool isOB = false, isOS = false;
   if(RequireDFMOForAveraging && !ReadDFMOState(isOB, isOS)) return;
   if(RequireDFMOForAveraging)
   {
      if(basketDirection == 1 && !isOS) return;
      if(basketDirection == -1 && !isOB) return;
   }

   double bestOpenLevel = 0.0, worstOpenLevel = 0.0;
   double referencePrice = basketAnchorPrice;
   bool hasAvgLevels = GetOpenLevelBounds(bestOpenLevel, worstOpenLevel);

   if(hasAvgLevels)
   {
      referencePrice = (basketDirection == 1) ? bestOpenLevel : worstOpenLevel;

      if(referencePrice > 0.0)
      {
         double gap = MathAbs(currentPrice - referencePrice);
         if(gap < MinGapFromLastAverageUSD) return;
      }
   }
   // When no AVG levels (only ENTRY positions), use basketAnchorPrice — no MinGap check

   double nextLevel = GetNextAdverseLevelPrice(referencePrice, basketDirection);

   if(hasAvgLevels)
   {
      bool reopeningRecoveredLevel = (MathAbs(nextLevel - lastClosedRecoveredLevel) < 0.10);
      bool alreadyOpenAtLevel = (nextLevel >= bestOpenLevel - 0.10 && nextLevel <= worstOpenLevel + 0.10);
      if(reopeningRecoveredLevel || alreadyOpenAtLevel)
      {
         TraceEvent("AVG_SKIPPED_LEVEL_BLOCKED",
                    "next_level=" + DoubleToString(nextLevel, 0) +
                    ",best_open=" + DoubleToString(bestOpenLevel, 0) +
                    ",worst_open=" + DoubleToString(worstOpenLevel, 0) +
                    ",last_closed_recovered=" + DoubleToString(lastClosedRecoveredLevel, 0));
         return;
      }
   }

   double lot = 0.0;
   string levelType = "";
   DetermineAverageRoundLevel(nextLevel, lot, levelType);
   double avgTol = EffectiveAverageLevelTouchWindow(nextLevel);
   bool hit = false;
   if(basketDirection == 1)
      hit = (currentPrice <= (nextLevel + avgTol));
   else if(basketDirection == -1)
      hit = (currentPrice >= (nextLevel - avgTol));

   bool requireReject = IsHundredLevel(nextLevel);
   if(requireReject)
   {
      double overshootLimit = StrongAverageRejectOvershootUSD();
      double confirmDistance = StrongAverageRejectConfirmUSD();

      if(!pendingStrongAvgReject || MathAbs(nextLevel - pendingStrongAvgLevel) > 0.10)
      {
         if(!hit)
            return;

         pendingStrongAvgReject = true;
         pendingStrongAvgLevel = nextLevel;
         TraceEvent("AVG_REJECT_ARMED",
                    "level=" + DoubleToString(nextLevel, 0) +
                    ",price=" + DoubleToString(currentPrice, _Digits) +
                    ",tol=" + DoubleToString(avgTol, 2));
         return;
      }

      double adverseOvershoot = 0.0;
      bool confirmed = false;
      if(basketDirection == 1)
      {
         adverseOvershoot = MathMax(0.0, nextLevel - currentPrice);
         confirmed = (currentPrice >= (nextLevel + confirmDistance));
      }
      else if(basketDirection == -1)
      {
         adverseOvershoot = MathMax(0.0, currentPrice - nextLevel);
         confirmed = (currentPrice <= (nextLevel - confirmDistance));
      }

      if(adverseOvershoot > overshootLimit)
      {
         TraceEvent("AVG_REJECT_FAILED",
                    "level=" + DoubleToString(nextLevel, 0) +
                    ",price=" + DoubleToString(currentPrice, _Digits) +
                    ",overshoot=" + DoubleToString(adverseOvershoot, 2));
         pendingStrongAvgReject = false;
         pendingStrongAvgLevel = 0.0;
         return;
      }

      if(!confirmed)
         return;

      TraceEvent("AVG_REJECT_CONFIRMED",
                 "level=" + DoubleToString(nextLevel, 0) +
                 ",price=" + DoubleToString(currentPrice, _Digits) +
                 ",confirm_distance=" + DoubleToString(confirmDistance, 2));
   }
   else
   {
      pendingStrongAvgReject = false;
      pendingStrongAvgLevel = 0.0;
      if(!hit) return;
   }

   int opened = OpenBasketOrderUnits(basketDirection, lot, "AVG_" + DoubleToString(nextLevel, 0));
   if(opened > 0)
   {
      pendingStrongAvgReject = false;
      pendingStrongAvgLevel = 0.0;
      if(basketPendingFirstLevel)
      {
         basketPendingFirstLevel = false;
         basketEntryLevelPrice = nextLevel;
         basketEntryLevelType = levelType;
      }
      if(!basketFirstAvgCaptured)
      {
         basketFirstAvgCaptured = true;
         basketFirstAvgTimeMin = (int)((TimeCurrent() - basketStartTime) / 60);
         basketFirstAvgLevelType = levelType;
         basketFirstAvgLevelPrice = nextLevel;
         CountAgainstM1ClosesFromEntry(10, basketFirstAvgAgainstCount10m, basketFirstAvgAgainstStreak10m);
         CountAgainstM1ClosesFromEntry(20, basketFirstAvgAgainstCount20m, basketFirstAvgAgainstStreak20m);
      }
      basketAverageCount++;
      if(levelType == "weak")
         basketAvgWeakCount++;
      else if(levelType == "mid")
         basketAvgMidCount++;
      else if(levelType == "strong")
         basketAvgStrongCount++;
      WriteTradeEventRow("avg_" + levelType);
      if(!UseCompactBacktestLogging())
         Print("AVG ", levelType, " level=", nextLevel, " total_lot=", DoubleToString(lot, 2),
               " orders=", IntegerToString(opened), " unit=", DoubleToString(cachedUnitLot, 2));
      TraceEvent("AVG",
                 "type=" + levelType +
                 ",level=" + DoubleToString(nextLevel, 0) +
                 ",total_lot=" + DoubleToString(lot, 2) +
                 ",orders=" + IntegerToString(opened) +
                 ",unit=" + DoubleToString(cachedUnitLot, 2));
   }
}

void ManageProfitStep()
{
   if(!profitActionArmed || basketDirection == 0) return;

   double currentPrice = (basketDirection == 1) ? BidPrice() : AskPrice();
   int openPositions = CountBasketPositions();
   int positiveCount = 0;
   int negativeCount = 0;
   double leastFavorablePositiveEntry = 0.0;
   GetBasketUnitState(positiveCount, negativeCount, leastFavorablePositiveEntry);

   double weighted = 0.0;
   double totalLots = 0.0;
   double openProfit = 0.0;
   if(!GetBasketStats(weighted, totalLots, openProfit)) return;
   double globalProfit = ComputeCurrentBasketGlobalProfit();

   if(openPositions == 1)
   {
      bool hit = IsFavorableLevelTouched(basketDirection, currentPrice, nextProfitTriggerPrice);
      if(!hit) return;

      if(profitStage == 0)
      {
         double be = ComputeBreakEvenPrice();
         if(be > 0.0)
            UpdateBasketStops(be);

         profitStage = 1;
         nextProfitTriggerPrice = 0.0;
         profitActionArmed = false;
         TraceEvent("SINGLE_STEP1_BE",
                    "price=" + DoubleToString(currentPrice, _Digits) +
                    ",basket_profit=" + DoubleToString(openProfit, 2) +
                    ",global_profit=" + DoubleToString(globalProfit, 2) +
                    ",secure_trigger=" + DoubleToString(GetProfitSecureTriggerPrice(weighted), _Digits) +
                    ",be=" + DoubleToString(be, _Digits));
         return;
      }
      return;
   }

   if(negativeCount == 0 && positiveCount > 1 && leastFavorablePositiveEntry > 0.0)
   {
      if(nextProfitTriggerPrice <= 0.0)
         nextProfitTriggerPrice = GetProfitSecureTriggerPrice(leastFavorablePositiveEntry);

      bool hit = IsFavorableLevelTouched(basketDirection, currentPrice, nextProfitTriggerPrice);
      if(!hit) return;
      if(globalProfit <= 0.0) return;

      if(profitStage == 0)
      {
         double be = ComputeBreakEvenPrice();
         if(be > 0.0)
            UpdateBasketStops(be);

         profitStage = 1;
         nextProfitTriggerPrice = 0.0;
         profitActionArmed = false;
         TraceEvent("POSITIVE_MULTI_STEP1_BE",
                    "price=" + DoubleToString(currentPrice, _Digits) +
                    ",basket_profit=" + DoubleToString(openProfit, 2) +
                    ",global_profit=" + DoubleToString(globalProfit, 2) +
                    ",secure_trigger=" + DoubleToString(GetProfitSecureTriggerPrice(leastFavorablePositiveEntry), _Digits) +
                    ",positive_count=" + IntegerToString(positiveCount) +
                    ",be=" + DoubleToString(be, _Digits));
         return;
      }
   }
}

void ReconcileBasket()
{
   if(CountBasketPositions() > 0) return;
   // Don't reset a pending basket waiting for first level
   if(basketPendingFirstLevel && basketDirection != 0) return;
   if(basketDirection != 0)
      FinalizeBasket();
   ResetBasketState();
}

int OnInit()
{
   string signalCsvFile = EffectiveTelegramSignalCsvFile();

   trade.SetExpertMagicNumber((int)MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   if(UseCompactBacktestLogging())
      trade.LogLevel(LOG_LEVEL_ERRORS);

   bool needDFMOHandle = (!UseTelegramSignalCsvEntries || RequireDFMOForAveraging);
   if(needDFMOHandle)
   {
      hDFMO = iCustom(_Symbol, SignalTimeframe, "DFMO_DualFrameMomentumOscillator",
                      "", StochKPeriod, StochSmoothing, StochDPeriod,
                      "", RSIPeriod,
                      "", OverboughtLvl, OversoldLvl,
                      "", true, clrTomato, clrLimeGreen,
                      "", true, clrRed, clrDodgerBlue,
                      "", false, false);
      if(hDFMO == INVALID_HANDLE)
      {
         Print("Cannot create DFMO handle. Error=", GetLastError());
         return INIT_FAILED;
      }
   }

   TraceRunId = TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES|TIME_SECONDS);
   StringReplace(TraceRunId, ".", "");
   StringReplace(TraceRunId, ":", "");
   StringReplace(TraceRunId, " ", "_");
   TraceFileName = "dfmo_backtest_trace_" + TraceRunId + ".csv";
   BasketSummaryFileName = "dfmo_basket_summary_" + TraceRunId + ".csv";
   RunSummaryFileName = "dfmo_run_summary_" + TraceRunId + ".csv";
   SignalFeatureFileName = "dfmo_signal_features_" + TraceRunId + ".csv";
   TradeEventFileName = "dfmo_trade_events_" + TraceRunId + ".csv";

   int resetHandle = FileOpen(TraceFileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(resetHandle != INVALID_HANDLE && !UseCompactBacktestLogging())
   {
      FileWriteString(resetHandle, "time,run_id,event,details\r\n");
      FileClose(resetHandle);
   }

   resetHandle = FileOpen(BasketSummaryFileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(resetHandle != INVALID_HANDLE && !UseCompactBacktestLogging())
   {
      FileWriteString(resetHandle, "run_id,basket_id,direction,start,end,duration_min,from_signal,signal_channel,signal_open_msg,signal_close_msg,signal_price,signal_entry_drift,entry_level_type,entry_level,anchor_price,unit_lot,avg_count,recovered_count,partial_count,close_reason,pnl,pct_balance,pct_equity,max_adverse_usd,max_adverse_price,max_dd_pct,max_dd_amount,entry_balance,end_balance,entry_equity,end_equity\r\n");
      FileClose(resetHandle);
   }
   else if(resetHandle != INVALID_HANDLE)
      FileClose(resetHandle);

   resetHandle = FileOpen(RunSummaryFileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(resetHandle != INVALID_HANDLE)
   {
      FileWriteString(resetHandle, "run_id,status,start,end,start_balance,start_equity,peak_equity,end_balance,end_equity,max_dd_pct,max_dd_amount\r\n");
      FileClose(resetHandle);
   }

   resetHandle = FileOpen(SignalFeatureFileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(resetHandle != INVALID_HANDLE)
   {
      FileWriteString(resetHandle, "run_id,basket_id,direction,start,end,duration_min,close_reason,signal_channel,signal_open_msg,signal_close_msg,signal_open_time,signal_declared_close_time,signal_declared_duration_min,entry_hour,entry_minute,signal_price,entry_market_price,signal_entry_drift,entry_level_type,entry_level,entry_distance_to_level,signal_distance_to_level,signal_distance_to_strong,spread_usd,dfmo_stoch,dfmo_rsi,dfmo_ob,dfmo_os,atr_m1,atr_m5,tick_vol_m1,tick_vol_ratio20,move_3m,move_5m,move_15m,move_against_3m,move_against_5m,move_against_15m,adverse10_captured,adverse10_time_min,max_favorable_before_adverse10_usd,first_avg_captured,first_avg_time_min,first_avg_level_type,first_avg_level,first_avg_max_favorable_usd,first_avg_against_count_10m,first_avg_against_count_20m,first_avg_against_streak_10m,first_avg_against_streak_20m,avg_count,avg_weak_count,avg_mid_count,avg_strong_count,recovered_count,partial_count,pnl,pct_balance,pct_equity,max_adverse_usd,max_adverse_price,max_dd_pct,max_dd_amount,loss_flag,severe_loss_flag\r\n");
      FileClose(resetHandle);
   }

   resetHandle = FileOpen(TradeEventFileName, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(resetHandle != INVALID_HANDLE)
   {
      FileWriteString(resetHandle, "run_id,basket_id,event,time,duration_min,direction,entry_level_type,entry_level,current_price,weighted_price,total_lots,open_profit,global_profit,max_adverse_usd,worst_price,avg_count,avg_weak_count,avg_mid_count,avg_strong_count,recovered_count,partial_count,dfmo_stoch,dfmo_rsi,dfmo_ob,dfmo_os,atr_m1,atr_m5,tick_vol_m1,tick_vol_ratio20,move_3m,move_5m,move_15m,move_against_3m,move_against_5m,move_against_15m\r\n");
      FileClose(resetHandle);
   }

   RefreshRiskModel();
   runStartTime = TimeCurrent();
   runStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   runStartEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   runPeakEquity = runStartEquity;
   runMaxDDPct = 0.0;
   runMaxDDAmount = 0.0;
   double contractSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double pointValuePerLot = 0.0;
   if(tickSize > 0.0)
      pointValuePerLot = tickValue * (_Point / tickSize);

   Print("INIT risk model: channel=", GetChannelTag(),
         " lot_base=", DoubleToString(cachedLotBase, 2),
         " entry_lots=", DoubleToString(cachedEntryLotWeak, 2), "/",
         DoubleToString(cachedEntryLotMid, 2), "/",
         DoubleToString(cachedEntryLotStrong, 2),
         " avg_lots=", DoubleToString(cachedAvgLotWeak, 2), "/",
         DoubleToString(cachedAvgLotMid, 2), "/",
         DoubleToString(cachedAvgLotStrong, 2),
         " entry_session=", (EnableEntrySessionFilter ? "on" : "off"),
         " ", IntegerToString(EntrySessionStartHour), "-", IntegerToString(EntrySessionEndHour),
         " range=", DoubleToString(cachedRange, 1),
         " hard_dd_enabled=", (EffectiveEnableHardCloseDD() ? "true" : "false"),
         " hard_dd=", DoubleToString(HardCloseDDPct, 1), "%",
         " base_unit=", DoubleToString(EffectiveBaseUnitLot(), 2),
         " unit_multiplier=", DoubleToString(EffectiveUnitLotMultiplier(), 1),
         " effective_unit=", DoubleToString(cachedUnitLot, 2),
         " signal_time_basis=", GetSignalTimeBasisLabel(),
         " signal_offset_min=", IntegerToString(TelegramSignalTimeOffsetMinutes));
   Print("INIT symbol model: symbol=", _Symbol,
         " contract_size=", DoubleToString(contractSize, 2),
         " tick_value=", DoubleToString(tickValue, 5),
         " tick_size=", DoubleToString(tickSize, _Digits),
         " point_value_per_lot=", DoubleToString(pointValuePerLot, 5),
         " move1usd@0.01=", DoubleToString(contractSize * 0.01, 2),
         " move1usd@0.03=", DoubleToString(contractSize * 0.03, 2),
         " move1usd@0.12=", DoubleToString(contractSize * 0.12, 2));
   TraceEvent("RUN_START", "file=" + TraceFileName);
   TraceEvent("INIT",
              "channel=" + GetChannelTag() +
              ",use_signal_csv=" + string(UseTelegramSignalCsvEntries ? "1" : "0") +
              ",signal_file=" + signalCsvFile +
              ",lot_base=" + DoubleToString(cachedLotBase, 2) +
              ",entry_weak=" + DoubleToString(cachedEntryLotWeak, 2) +
              ",entry_mid=" + DoubleToString(cachedEntryLotMid, 2) +
              ",entry_strong=" + DoubleToString(cachedEntryLotStrong, 2) +
              ",avg_weak=" + DoubleToString(cachedAvgLotWeak, 2) +
              ",avg_mid=" + DoubleToString(cachedAvgLotMid, 2) +
              ",avg_strong=" + DoubleToString(cachedAvgLotStrong, 2) +
              ",entry_session_filter=" + string(EnableEntrySessionFilter ? "true" : "false") +
              ",entry_session_start=" + IntegerToString(EntrySessionStartHour) +
              ",entry_session_end=" + IntegerToString(EntrySessionEndHour) +
              ",range=" + DoubleToString(cachedRange, 1) +
              ",hard_dd_enabled=" + string(EffectiveEnableHardCloseDD() ? "true" : "false") +
              ",hard_dd=" + DoubleToString(HardCloseDDPct, 1) +
              ",base_unit=" + DoubleToString(EffectiveBaseUnitLot(), 2) +
              ",unit_multiplier=" + DoubleToString(EffectiveUnitLotMultiplier(), 1) +
              ",effective_unit=" + DoubleToString(cachedUnitLot, 2) +
              ",signal_time_basis=" + GetSignalTimeBasisLabel() +
              ",signal_offset_min=" + IntegerToString(TelegramSignalTimeOffsetMinutes) +
              ",contract_size=" + DoubleToString(contractSize, 2) +
              ",tick_value=" + DoubleToString(tickValue, 5) +
              ",tick_size=" + DoubleToString(tickSize, _Digits) +
              ",move1usd_001=" + DoubleToString(contractSize * 0.01, 2) +
              ",move1usd_003=" + DoubleToString(contractSize * 0.03, 2) +
              ",move1usd_012=" + DoubleToString(contractSize * 0.12, 2));

   if(UseTelegramSignalCsvEntries && !LoadTelegramSignalCsv())
      return INIT_FAILED;

   ResetBasketState();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   WriteRunSummary("deinit_" + IntegerToString(reason));
   if(hDFMO != INVALID_HANDLE)
   {
      IndicatorRelease(hDFMO);
      hDFMO = INVALID_HANDLE;
   }
}

void OnTick()
{
   UpdateRunDrawdown();
   ReconcileBasket();
   UpdateBasketAdverseDistance();
   TrackBasketDeteriorationMilestones();
   TrackBasketFavorableMilestones();
   ManageHardDrawdown();
   if(hardDDTriggered)
   {
      ReconcileBasket();
      return;
   }

   if(ManagePriceBasketStop())
   {
      ReconcileBasket();
      return;
   }

   if(ManageTelegramSignalClose())
   {
      ReconcileBasket();
      return;
   }

   if(UseTelegramSignalCsvEntries)
      TryOpenTelegramSignalEntry();
   else
      TryOpenInitialEntry();

   TryOpenAveraging();
   ManageRecoveredLevels();
   ManageUnitNetCompensation();
   ManageProfitStep();
   ReconcileBasket();
}
