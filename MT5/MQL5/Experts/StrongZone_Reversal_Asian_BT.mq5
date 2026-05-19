//+------------------------------------------------------------------+
//|                              StrongZone_Reversal_Asian_BT.mq5     |
//| Simple Asian-session reversal test on strong 50/100 levels       |
//+------------------------------------------------------------------+
#property strict
#property version   "1.00"
#property description "Backtest EA: fade strong 50/100 levels in Asian session with simple partial + BE."

#include <Trade\Trade.mqh>

input ulong MagicNumber            = 260405;
input int   SlippagePoints         = 30;
input double BaseLot               = 0.10;

input double StrongStepUSD         = 50.0;
input double EntryTouchWindowUSD   = 5.0;
input double LevelStopOffsetUSD    = 10.0;
input double PartialTriggerUSD     = 10.0;
input double FinalTargetUSD        = 20.0;
input double PartialClosePercent   = 50.0;

input bool EnableSessionFilter     = true;
input int  SessionStartHour        = 0;
input int  SessionEndHour          = 8;

input int  MinSecondsBetweenTrades = 60;

CTrade trade;

double lastMidPrice = 0.0;
datetime lastTradeTime = 0;
double lastTriggeredLevel = 0.0;
int lastTriggeredDirection = 0;

bool partialDone = false;
double activeLevelPrice = 0.0;
int activeDirection = 0;
bool setupActive = false;
double setupLevelPrice = 0.0;
int setupDirection = 0;
bool setupFilled1 = false;
bool setupFilled2 = false;
bool setupFilled3 = false;

double BidPrice() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double AskPrice() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

double NormalizePrice(double price)
{
   return NormalizeDouble(price, _Digits);
}

double NormalizeVolume(double volume)
{
   double volMin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double volMax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double volStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(volStep <= 0.0) volStep = 0.01;

   volume = MathMax(volMin, MathMin(volMax, volume));
   volume = MathFloor(volume / volStep) * volStep;
   return NormalizeDouble(volume, 2);
}

bool IsSessionAllowed(datetime whenTime)
{
   if(!EnableSessionFilter)
      return true;

   MqlDateTime tm;
   TimeToStruct(whenTime, tm);
   int hour = tm.hour;

   if(SessionStartHour == SessionEndHour)
      return true;

   if(SessionStartHour < SessionEndHour)
      return (hour >= SessionStartHour && hour < SessionEndHour);

   return (hour >= SessionStartHour || hour < SessionEndHour);
}

bool IsOurPosition()
{
   if(PositionGetString(POSITION_SYMBOL) != _Symbol) return false;
   if((ulong)PositionGetInteger(POSITION_MAGIC) != MagicNumber) return false;
   return true;
}

int CountOurPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurPosition()) continue;
      count++;
   }
   return count;
}

bool GetSinglePosition(ulong &ticket,
                       ENUM_POSITION_TYPE &type,
                       double &volume,
                       double &openPrice)
{
   ticket = 0;
   volume = 0.0;
   openPrice = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong posTicket = PositionGetTicket(i);
      if(posTicket == 0) continue;
      if(!IsOurPosition()) continue;

      ticket = posTicket;
      type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      volume = PositionGetDouble(POSITION_VOLUME);
      openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      return true;
   }

   return false;
}

void ResetTradeState()
{
   partialDone = false;
   activeLevelPrice = 0.0;
   activeDirection = 0;
   setupActive = false;
   setupLevelPrice = 0.0;
   setupDirection = 0;
   setupFilled1 = false;
   setupFilled2 = false;
   setupFilled3 = false;
}

bool IsStrongLevel(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   return (mod100 == 0 || mod100 == 50);
}

double GetNearestStrongLevel(double price)
{
   double nearest = NormalizePrice(MathRound(price / StrongStepUSD) * StrongStepUSD);
   if(IsStrongLevel(nearest))
      return nearest;

   double lower = MathFloor(price / StrongStepUSD) * StrongStepUSD;
   double upper = MathCeil(price / StrongStepUSD) * StrongStepUSD;

   if(MathAbs(price - lower) <= MathAbs(price - upper))
      return NormalizePrice(lower);
   return NormalizePrice(upper);
}

bool UpdateMidPrice(double &currentMid)
{
   double bid = BidPrice();
   double ask = AskPrice();
   if(bid <= 0.0 || ask <= 0.0)
      return false;

   currentMid = NormalizePrice((bid + ask) * 0.5);
   return true;
}

bool OpenReversalAtLevel(int direction, double levelPrice)
{
   double volume = NormalizeVolume(BaseLot);
   if(volume <= 0.0)
      return false;

   bool ok = (direction == 1)
      ? trade.Buy(volume, _Symbol, 0.0, 0.0, 0.0, "REV_" + DoubleToString(levelPrice, 0))
      : trade.Sell(volume, _Symbol, 0.0, 0.0, 0.0, "REV_" + DoubleToString(levelPrice, 0));

   if(!ok)
      return false;

   uint rc = trade.ResultRetcode();
   if(rc != 10008 && rc != 10009)
      return false;

   activeLevelPrice = levelPrice;
   activeDirection = direction;
   lastTradeTime = TimeCurrent();
   lastTriggeredLevel = levelPrice;
   lastTriggeredDirection = direction;
   return true;
}

void CloseAllOurPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurPosition()) continue;
      trade.PositionClose(ticket);
   }
}

void CloseBasketVolumePercent(double percent)
{
   if(percent <= 0.0)
      return;

   double remainingToClose = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurPosition()) continue;
      remainingToClose += PositionGetDouble(POSITION_VOLUME);
   }

   if(remainingToClose <= 0.0)
      return;

   remainingToClose = NormalizeVolume(remainingToClose * (percent / 100.0));
   double minVol = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if(remainingToClose < minVol)
      return;

   for(int i = PositionsTotal() - 1; i >= 0 && remainingToClose >= minVol; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurPosition()) continue;

      double posVolume = PositionGetDouble(POSITION_VOLUME);
      double closeVol = MathMin(posVolume, remainingToClose);
      closeVol = NormalizeVolume(closeVol);
      if(closeVol < minVol)
         continue;

      if(closeVol >= posVolume - 1e-9)
      {
         if(trade.PositionClose(ticket))
            remainingToClose = NormalizeVolume(remainingToClose - posVolume);
      }
      else
      {
         if(trade.PositionClosePartial(ticket, closeVol))
            remainingToClose = NormalizeVolume(remainingToClose - closeVol);
      }
   }
}

bool GetBasketStats(double &weightedPrice, double &totalVolume)
{
   weightedPrice = 0.0;
   totalVolume = 0.0;

   for(int i = PositionsTotal() - 1; i >= 0; --i)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!IsOurPosition()) continue;

      double volume = PositionGetDouble(POSITION_VOLUME);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      weightedPrice += openPrice * volume;
      totalVolume += volume;
   }

   if(totalVolume <= 0.0)
      return false;

   weightedPrice /= totalVolume;
   return true;
}

void ManageOpenTrade()
{
   ulong ticket = 0;
   ENUM_POSITION_TYPE type;
   double volume = 0.0;
   double openPrice = 0.0;
   if(!GetSinglePosition(ticket, type, volume, openPrice))
   {
      if(!setupActive)
         ResetTradeState();
      return;
   }

   int direction = (type == POSITION_TYPE_BUY) ? 1 : -1;
   double currentPrice = (direction == 1) ? BidPrice() : AskPrice();
   double weightedPrice = 0.0;
   double totalVolume = 0.0;
   if(!GetBasketStats(weightedPrice, totalVolume))
      return;

   // Fixed stop based on level, not on fill.
   double stopPrice = (direction == 1)
      ? NormalizePrice(activeLevelPrice - LevelStopOffsetUSD)
      : NormalizePrice(activeLevelPrice + LevelStopOffsetUSD);

   bool stopHit = (direction == 1)
      ? (currentPrice <= stopPrice)
      : (currentPrice >= stopPrice);
   if(stopHit)
   {
      CloseAllOurPositions();
      ResetTradeState();
      return;
   }

   double favorableMove = (direction == 1)
      ? (currentPrice - weightedPrice)
      : (weightedPrice - currentPrice);

   if(!partialDone && favorableMove >= PartialTriggerUSD)
   {
      CloseBasketVolumePercent(PartialClosePercent);

      for(int i = PositionsTotal() - 1; i >= 0; --i)
      {
         ulong posTicket = PositionGetTicket(i);
         if(posTicket == 0) continue;
         if(!IsOurPosition()) continue;
         trade.PositionModify(posTicket, NormalizePrice(weightedPrice), 0.0);
      }
      partialDone = true;
      return;
   }

   if(favorableMove >= FinalTargetUSD)
   {
      CloseAllOurPositions();
      ResetTradeState();
   }
}

double GetLadderEntryPrice(int direction, double levelPrice, int index)
{
   double offset = 0.0;
   if(index == 0) offset = -2.0;
   if(index == 1) offset = 0.0;
   if(index == 2) offset = 2.0;

   // Same offsets around the level regardless of side.
   return NormalizePrice(levelPrice + offset);
}

bool IsEntryTriggered(int direction, double targetPrice)
{
   double bid = BidPrice();
   double ask = AskPrice();
   if(direction == 1)
      return ask <= (targetPrice + 0.10);
   return bid >= (targetPrice - 0.10);
}

void ManageSetupEntries()
{
   if(!setupActive)
      return;

   if(activeDirection == 0)
   {
      activeDirection = setupDirection;
      activeLevelPrice = setupLevelPrice;
   }

   double p1 = GetLadderEntryPrice(setupDirection, setupLevelPrice, 0);
   double p2 = GetLadderEntryPrice(setupDirection, setupLevelPrice, 1);
   double p3 = GetLadderEntryPrice(setupDirection, setupLevelPrice, 2);

   if(!setupFilled1 && IsEntryTriggered(setupDirection, p1))
   {
      if(OpenReversalAtLevel(setupDirection, setupLevelPrice))
         setupFilled1 = true;
   }

   if(!setupFilled2 && IsEntryTriggered(setupDirection, p2))
   {
      if(OpenReversalAtLevel(setupDirection, setupLevelPrice))
         setupFilled2 = true;
   }

   if(!setupFilled3 && IsEntryTriggered(setupDirection, p3))
   {
      if(OpenReversalAtLevel(setupDirection, setupLevelPrice))
         setupFilled3 = true;
   }

   // If the market leaves the area without any fill, cancel the setup.
   if(!setupFilled1 && !setupFilled2 && !setupFilled3)
   {
      double currentMid = 0.0;
      if(UpdateMidPrice(currentMid) && MathAbs(currentMid - setupLevelPrice) > (EntryTouchWindowUSD + 5.0))
         ResetTradeState();
   }
}

void TryArmNewSetup()
{
   if(CountOurPositions() > 0) return;
   if(setupActive) return;
   if(TimeCurrent() - lastTradeTime < MinSecondsBetweenTrades) return;
   if(!IsSessionAllowed(TimeCurrent())) return;

   double currentMid = 0.0;
   if(!UpdateMidPrice(currentMid))
      return;

   if(lastMidPrice <= 0.0)
   {
      lastMidPrice = currentMid;
      return;
   }

   double level = GetNearestStrongLevel(currentMid);
   if(level <= 0.0) return;
   if(MathAbs(currentMid - level) > EntryTouchWindowUSD) return;

   int direction = 0;
   if(lastMidPrice < (level - EntryTouchWindowUSD))
      direction = -1; // approached from below -> fade with SELL
   else if(lastMidPrice > (level + EntryTouchWindowUSD))
      direction = 1;  // approached from above -> fade with BUY

   if(direction == 0)
      return;

   if(MathAbs(lastTriggeredLevel - level) < 0.10 && lastTriggeredDirection == direction &&
      (TimeCurrent() - lastTradeTime) < (MinSecondsBetweenTrades * 5))
      return;

   setupActive = true;
   setupLevelPrice = level;
   setupDirection = direction;
   setupFilled1 = false;
   setupFilled2 = false;
   setupFilled3 = false;
   activeLevelPrice = level;
   activeDirection = direction;
}

int OnInit()
{
   trade.SetExpertMagicNumber((int)MagicNumber);
   trade.SetDeviationInPoints(SlippagePoints);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   ResetTradeState();
   return INIT_SUCCEEDED;
}

void OnTick()
{
   ManageOpenTrade();
   ManageSetupEntries();
   TryArmNewSetup();

   double currentMid = 0.0;
   if(UpdateMidPrice(currentMid))
      lastMidPrice = currentMid;
}
