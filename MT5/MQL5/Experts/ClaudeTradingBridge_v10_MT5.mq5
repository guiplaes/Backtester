//+------------------------------------------------------------------+
#property copyright "Claude Trading System"
#property version   "10.00"

#include <Trade\Trade.mqh>

// === CONFIG ===
input int      UpdateInterval   = 3;
input double   DefaultLotSize   = 0.01;
input int      DefaultSlippage  = 30;
input int      HeartbeatTimeout = 15;
input bool     EnablePush       = true;

input int      ExtremeATRPeriod = 14;
input double   ExtremeDistATRWarn = 1.00;
input double   ExtremeDistATRExtreme = 1.50;
input double   ExtremeSpeedATRWarn = 0.60;
input double   ExtremeSpeedATRExtreme = 1.00;
input int      ExtremeConfirmSec = 8;
input int      ExtremeHoldSec = 15;

// === CENTENAS CONFIG (v11: backtest-validated rules) ===
input double   UNIT_LOT       = 0.03;   // lot unitari (0.03 x posicio)
input double   ENTRY_WEIGHT_WEAK   = 4.0; // pes entrada febles ($x25, $x75)
input double   ENTRY_WEIGHT_MID    = 2.0; // pes entrada mitjos ($x50)
input double   ENTRY_WEIGHT_STRONG = 6.0; // pes entrada forts ($x00)
input double   AVG_WEIGHT_WEAK   = 2.0; // pes avg febles ($x25, $x75) = 2x0.03=0.06
input double   AVG_WEIGHT_MID    = 4.0; // pes avg mitjos ($x50) = 4x0.03=0.12
input double   AVG_WEIGHT_STRONG = 4.0; // pes avg forts ($x00) = 4x0.03=0.12
input double   AVG_TOLERANCE  = 0.5;    // USD tolerancia per tocar nivell
input double   REJECT_OVERSHOOT = 3.0;  // USD max overshoot 100-levels
input double   REJECT_CONFIRM = 2.0;    // USD confirmacio rebot 100-levels
input double   STRUCT_STOP_CONFIRM = 5.0; // USD confirm trencament nivell fort
input double   BE_TRIGGER_USD = 10.0;   // USD profit per activar BE
input double   MIN_GAP_AVG    = 20.0;   // USD gap minim entre averagings
input double   VEL_MAX        = 0.10;   // $/s maxim per averaging (disabled)

// === ENTRY PROFIT LOCKS (SL escalonat per assegurar ENTRY) ===
input double   ENTRY_LOCK_1   = 10.0;   // USD advance per lock pos 1
input double   ENTRY_LOCK_2   = 14.0;   // USD advance per lock pos 2
input double   ENTRY_LOCK_3   = 18.0;   // USD advance per lock pos 3

// === FILE PATHS ===
string COMMON_PATH, MARKET_FILE, ORDERS_FILE, EVENTS_FILE, STATUS_FILE, HEARTBEAT_FILE;

// === CTRADE ===
CTrade trade;

// === PYTHON SYNC ===
double pythonEntryPrice = 0;
int    pythonDirection  = 0;   // 1=BUY, -1=SELL
bool   pythonClosing    = false;
string hbChannelTag     = "T";

// === HEARTBEAT ===
datetime lastHeartbeatCheck = 0;
int    missedHeartbeats = 0;
bool   heartbeatAlertSent = false;

// === POSITION TRACKING ===
int    previousPositions = 0;
double previousEquity = 0;
datetime lastUpdate = 0;
datetime lastOrdersModified = 0;

// === AVERAGING STATE ===
#define AVG_MAX 60
int    avgPosCount = 0;
long   avgPosTickets[AVG_MAX];
double avgPosPrices[AVG_MAX];
double avgPosLots[AVG_MAX];
double avgLastOpenDist = 0;

// === CLAIMED LEVELS (prevent duplicate averaging at same centena) ===
#define CLAIMED_MAX 40
double claimedLevels[CLAIMED_MAX];
int    claimedCount = 0;

// === CENTENAS STATE ===
bool   pendingReject = false;
double pendingRejectLevel = 0.0;
bool   beTriggered = false;
double basketStructRefLevel = 0.0;
bool   autoTrailActive = false;        // flag: trailing SL activat (no es re-activa)
double autoTrailPeak = 0.0;            // màxim avanç registrat
double autoTrailLastSL = 0.0;          // últim SL enviat
ulong  autoTrailTickets[20];           // tickets de la MEITAT seleccionada
int    autoTrailTicketCount = 0;       // quants tickets en trailing
input double   AUTO_TRAIL_TRIGGER = 10.0;  // USD favorable des de BE per iniciar trailing

// === VELOCITY FILTER ===
double _vel_prices[20];
uint   _vel_times[20];
int    _vel_idx = 0;
int    _vel_count = 0;
uint   _vel_lastStore = 0;
bool   _vel_blocked = false;
double _vel_speed = 0;

// === EXTREME MOVE STATE ===
#define EXTREME_BUF_MAX 40
double   _adv_dist_hist[EXTREME_BUF_MAX];
datetime _adv_time_hist[EXTREME_BUF_MAX];
int      _adv_hist_count = 0;
int      _adv_hist_idx = 0;
datetime _adv_last_store = 0;
double   _adverse_speed = 0.0;
double   _atr_m1 = 0.0;
int      _extreme_raw_state = 0;
int      _extreme_state = 0;
datetime _extreme_since = 0;
datetime _extreme_hold_until = 0;
int      _market_pace_state = 0;

// === RESET_SL TRAILING ===
ulong  resetTrailTickets[30];
double resetTrailLastSL[30];
int    resetTrailCount = 0;
double CLOSE_SL_MIN_MOVE = 0.50;

// === CLOSE FLAGS ===
bool   closeViaSL = false;

//+------------------------------------------------------------------+
// HELPERS
//+------------------------------------------------------------------+
double Bid() { return SymbolInfoDouble(_Symbol, SYMBOL_BID); }
double Ask() { return SymbolInfoDouble(_Symbol, SYMBOL_ASK); }

double GetAtrM1(int period)
{
   if(period < 2) period = 2;
   MqlRates rates[];
   int needBars = period + 2;
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(_Symbol, PERIOD_M1, 0, needBars, rates);
   if(copied < period + 1) return 0.0;

   double trSum = 0.0;
   for(int i = period; i >= 1; i--)
   {
      double high = rates[i - 1].high;
      double low = rates[i - 1].low;
      double prevClose = rates[i].close;
      double tr1 = high - low;
      double tr2 = MathAbs(high - prevClose);
      double tr3 = MathAbs(low - prevClose);
      trSum += MathMax(tr1, MathMax(tr2, tr3));
   }
   return trSum / period;
}

long GetJsonInt(string &json, string key)
{
   string search = "\"" + key + "\":";
   int idx = StringFind(json, search);
   if(idx < 0) return 0;
   int start = idx + StringLen(search);
   int end = start;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == ' ' || ch == '\n') break;
      end++;
   }
   return StringToInteger(StringSubstr(json, start, end - start));
}

string ExtractJSONString(string json, string key)
{
   string search = "\"" + key + "\":";
   int idx = StringFind(json, search);
   if(idx < 0) return "";
   int qStart = StringFind(json, "\"", idx + StringLen(search));
   if(qStart < 0) return "";
   int qEnd = StringFind(json, "\"", qStart + 1);
   if(qEnd < 0) return "";
   return StringSubstr(json, qStart + 1, qEnd - qStart - 1);
}

double ExtractJSONDouble(string json, string key)
{
   string search = "\"" + key + "\":";
   int idx = StringFind(json, search);
   if(idx < 0) return 0;
   int start = idx + StringLen(search);
   while(start < StringLen(json) && StringGetCharacter(json, start) == ' ') start++;
   int end = start;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == ']' || ch == ' ' || ch == '\n') break;
      end++;
   }
   return StringToDouble(StringSubstr(json, start, end - start));
}

string ErrorDescription(int error)
{
   switch(error)
   {
      case 10004: return "Requote";
      case 10006: return "Request rejected";
      case 10007: return "Request canceled";
      case 10009: return "Done";
      case 10010: return "Partially filled";
      case 10013: return "Invalid request";
      case 10014: return "Invalid volume";
      case 10015: return "Invalid price";
      case 10016: return "Invalid stops";
      case 10018: return "Market closed";
      case 10019: return "Not enough money";
      case 10021: return "No prices";
      case 10024: return "Too frequent";
      case 10026: return "Autotrading disabled";
      case 10030: return "Invalid fill type";
      default:    return "Error " + IntegerToString(error);
   }
}

int CountPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetTicket(i) > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol)
         count++;
   }
   return count;
}

void WriteEvent(string eventType, string details)
{
   static int eventSeq = 0;
   eventSeq++;
   string json = "{\"event\": \"" + eventType + "\", \"details\": \"" + details +
                 "\", \"time\": \"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) +
                 "\", \"seq\": " + IntegerToString(eventSeq) + "}";
   int h = FileOpen(EVENTS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, json); FileClose(h); }
}

//+------------------------------------------------------------------+
// ONINIT
//+------------------------------------------------------------------+
int OnInit()
{
   COMMON_PATH = "Files\\";
   MARKET_FILE    = "claude_market_data.json";
   ORDERS_FILE    = "claude_orders.json";
   EVENTS_FILE    = "claude_events.json";
   STATUS_FILE    = "claude_positions.json";
   HEARTBEAT_FILE = "claude_heartbeat.json";

   trade.SetExpertMagicNumber(12345);
   trade.SetDeviationInPoints(DefaultSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   EventSetMillisecondTimer(500);
   previousPositions = CountPositions();

   LoadAvgState();
   Print("=== ClaudeTradingBridge v11 CENTENAS === unit=", UNIT_LOT,
         " avg w/m/s=", DoubleToString(UNIT_LOT*AVG_WEIGHT_WEAK, 2), "/",
         DoubleToString(UNIT_LOT*AVG_WEIGHT_MID, 2), "/",
         DoubleToString(UNIT_LOT*AVG_WEIGHT_STRONG, 2),
         " tol=", AVG_TOLERANCE, " reject=", REJECT_OVERSHOOT, "/", REJECT_CONFIRM,
         " struct=", STRUCT_STOP_CONFIRM, " BE=", BE_TRIGGER_USD);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   SaveAvgState();
   EventKillTimer();
   Print("=== v10 DEINIT === reason=", reason);
}

//+------------------------------------------------------------------+
// ONTIMER (500ms) — guaranteed order processing
//+------------------------------------------------------------------+
void OnTimer()
{
   // Write market data
   double bid = Bid(), ask = Ask();
   if(bid > 0)
   {
      string json = "{\"bid\": " + DoubleToString(bid, _Digits) +
                    ", \"ask\": " + DoubleToString(ask, _Digits) +
                    ", \"spread_points\": " + DoubleToString((ask - bid) / _Point, 1) +
                    ", \"time\": \"" + TimeToString(TimeCurrent()) + "\"}";
      int h = FileOpen(MARKET_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWriteString(h, json); FileClose(h); }
   }

   // Heartbeat every 5s
   if(TimeCurrent() - lastHeartbeatCheck >= 5)
   {
      lastHeartbeatCheck = TimeCurrent();
      CheckHeartbeat();
   }

   // Orders — always check
   CheckForCloseAll();
   CheckForNewOrders();
}

//+------------------------------------------------------------------+
// ONTICK
//+------------------------------------------------------------------+
void OnTick()
{
   // Velocity: update ALWAYS (not just during averaging)
   UpdateVelocity();
   UpdateExtremeMoveState();

   CheckFixedAveraging();
   ManageDDStop();
   ManageBreakEven();
   CheckEntryProfitLocks();
   ManagePartialTP();
   ManageResetTrailing();
   UpdateAvgPositionStatus();

   // Throttled: 1x per second
   if(TimeCurrent() - lastUpdate >= 1)
   {
      lastUpdate = TimeCurrent();
      CheckPositionChanges();
      ExportPositions();
      UpdateChartDisplay();
   }

   // Orders on tick too (fast response)
   CheckForCloseAll();
   CheckForNewOrders();
}

//+------------------------------------------------------------------+
// HEARTBEAT — sync with Python
//+------------------------------------------------------------------+
void CheckHeartbeat()
{
   int h = FileOpen(HEARTBEAT_FILE, FILE_READ|FILE_BIN|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   string content = FileReadString(h, (int)FileSize(h));
   FileClose(h);
   if(StringLen(content) < 5) return;

   long ts = GetJsonInt(content, "timestamp");
   long diff = (long)TimeGMT() - ts;

   if(diff > HeartbeatTimeout)
   {
      missedHeartbeats++;
      if(missedHeartbeats >= 3 && !heartbeatAlertSent)
      {
         Print("!!! Python NO RESPON! Ultim heartbeat fa ", diff, "s");
         if(EnablePush) SendNotification(_Symbol + " | Python NO RESPON (" + IntegerToString((int)diff) + "s)");
         heartbeatAlertSent = true;
      }
   }
   else
   {
      if(heartbeatAlertSent) Print("Python RECUPERAT");
      missedHeartbeats = 0;
      heartbeatAlertSent = false;
   }

   // Parse entry_price
   double oldEntry = pythonEntryPrice;
   double newEntry = ExtractJSONDouble(content, "entry_price");
   if(newEntry > 0 && MathAbs(newEntry - pythonEntryPrice) > 0.01)
   {
      pythonEntryPrice = newEntry;
      Print(">>> HB: Entry = ", DoubleToString(pythonEntryPrice, 2));
   }
   else if(newEntry <= 0)
   {
      pythonEntryPrice = 0;  // Immediately stop new averagings
   }

   // Signal lifecycle — debounced to survive Python restarts
   static bool hadValidEntry = false;
   static int zeroEntryCount = 0;

   if(pythonEntryPrice > 0)
   {
      zeroEntryCount = 0;
      if(!hadValidEntry)
      {
         hadValidEntry = true;
         ReconcileAvgPositions();
      }
   }
   else if(hadValidEntry)
   {
      // entry=0: debounce — require 3 consecutive heartbeats before clearing state
      zeroEntryCount++;
      if(zeroEntryCount >= 3)
      {
         Print(">>> SIGNAL END (confirmed after ", zeroEntryCount, " HBs): clearing state");
         pythonDirection = 0;
         ClearAllAvgState();
         hadValidEntry = false;
         zeroEntryCount = 0;
      }
      else
         Print(">>> SIGNAL END PENDING (", zeroEntryCount, "/3): waiting for confirm");
   }

   // Parse direction
   string dirStr = ExtractJSONString(content, "direction");
   if(dirStr == "BUY") pythonDirection = 1;
   else if(dirStr == "SELL") pythonDirection = -1;
   else if(dirStr == "") pythonDirection = 0;

   // Parse channel_tag
   string ch = ExtractJSONString(content, "channel_tag");
   if(ch != "") hbChannelTag = ch;

   // Parse closing flag
   int clIdx = StringFind(content, "\"closing\":");
   if(clIdx >= 0)
   {
      int trueIdx = StringFind(content, "true", clIdx);
      if(trueIdx >= 0 && trueIdx < clIdx + 20)
      {
         if(!pythonClosing) Print(">>> HB: CLOSING flag ON - averaging BLOCKED");
         pythonClosing = true;
      }
      else
         pythonClosing = false;
   }
   else
   {
      if(pythonClosing) Print(">>> HB: Closing flag OFF - averaging OK");
      pythonClosing = false;
   }
}

//+------------------------------------------------------------------+
// CLOSE ALL
//+------------------------------------------------------------------+
void CheckForCloseAll()
{
   int h = FileOpen(ORDERS_FILE, FILE_READ|FILE_BIN|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   string content = FileReadString(h, (int)FileSize(h));
   FileClose(h);
   if(StringLen(content) < 5) return;
   if(StringFind(content, "\"status\"") >= 0) return;  // Already processed

   if(StringFind(content, "CLOSE_ALL") < 0) return;

   Print(">>> CLOSE_ALL DETECTAT");

   // Cancel pending
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderGetString(ORDER_SYMBOL) == _Symbol)
         trade.OrderDelete(ticket);
   }

   // Close all positions (3 retries ASYNC)
   trade.SetAsyncMode(true);
   for(int retry = 0; retry < 3; retry++)
   {
      int closed = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         trade.PositionClose(ticket);
         closed++;
      }
      if(closed == 0) break;
      Sleep(500);
   }
   trade.SetAsyncMode(false);

   ClearAllAvgState();
   resetTrailCount = 0;

   // Mark processed
   string result = "{\"status\": \"PROCESSED\", \"by\": \"CLOSE_ALL\", \"time\": \"" + TimeToString(TimeCurrent()) + "\"}";
   h = FileOpen(ORDERS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, result); FileClose(h); }
   WriteEvent("CLOSE_ALL", "Totes posicions tancades");
   Print(">>> CLOSE_ALL completat");
}

//+------------------------------------------------------------------+
// NEW ORDERS
//+------------------------------------------------------------------+
void CheckForNewOrders()
{
   int h = FileOpen(ORDERS_FILE, FILE_READ|FILE_BIN|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("!!! ORDERS FILE LOCKED"); return; }
   int fsize = (int)FileSize(h);
   string content = FileReadString(h, fsize);
   FileClose(h);
   int slen = StringLen(content);

   // DEBUG: log every 10s what we read
   static datetime lastDbg = 0;
   if(TimeCurrent() - lastDbg >= 10)
   {
      lastDbg = TimeCurrent();
      Print(">>> ORD DBG: fsize=", fsize, " slen=", slen, " first60=[", StringSubstr(content, 0, 60), "]");
   }

   if(slen < 5) return;
   if(StringFind(content, "\"status\"") >= 0) return;
   if(StringFind(content, "CLOSE_ALL") >= 0) return;  // handled by CheckForCloseAll

   Print(">>> NEW ORDERS: ", StringSubstr(content, 0, 100));

   // RESET_SL special handling
   if(StringFind(content, "RESET_SL") >= 0)
   {
      Print(">>> RESET_SL DETECTAT");
      trade.SetAsyncMode(false);
      int slSet = 0;
      resetTrailCount = 0;

      double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
      double freezeLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * _Point;
      double minDist = MathMax(stopLvl, freezeLvl) + 0.30; // robust safety margin
      if(minDist < 0.50) minDist = 0.50;
      Print(">>> RESET_SL stopLvl=", stopLvl, " freezeLvl=", freezeLvl, " minDist=", minDist);

      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         ENUM_POSITION_TYPE pType = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         // Fresh price for each position to avoid stale data
         double freshBid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double freshAsk = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double newSL;
         if(pType == POSITION_TYPE_BUY)
            newSL = NormalizeDouble(freshBid - minDist, _Digits);
         else
            newSL = NormalizeDouble(freshAsk + minDist, _Digits);
         Print(">>> RESET pos #", ticket, " type=", (pType==POSITION_TYPE_BUY?"BUY":"SELL"),
               " SL=", newSL, " bid=", freshBid, " ask=", freshAsk);
         if(trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP)))
         {
            slSet++;
            // Afegir a trailing per continuar perseguint el preu
            if(resetTrailCount < 30)
            {
               resetTrailTickets[resetTrailCount] = ticket;
               resetTrailLastSL[resetTrailCount] = newSL;
               resetTrailCount++;
            }
         }
      }
      Print(">>> RESET_SL: SL posat a ", slSet, " posicions");
      WriteEvent("RESET_SL", "SL a " + IntegerToString(slSet) + " posicions");

      // Process new_orders if present
      int nIdx = StringFind(content, "new_orders");
      if(nIdx >= 0)
      {
         int aS = StringFind(content, "[", nIdx);
         int aE = StringFind(content, "]", aS);
         if(aS >= 0 && aE >= 0)
         {
            string nc = "{\"orders\": " + StringSubstr(content, aS, aE - aS + 1) + "}";
            ProcessOrders(nc);
         }
      }

      // Mark processed
      string result = "{\"status\": \"PROCESSED\", \"by\": \"RESET_SL\", \"time\": \"" + TimeToString(TimeCurrent()) + "\"}";
      h = FileOpen(ORDERS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
      if(h != INVALID_HANDLE) { FileWriteString(h, result); FileClose(h); }
      return;
   }

   ProcessOrders(content);

   // Mark processed
   string result = "{\"status\": \"PROCESSED\", \"by\": \"EA\", \"time\": \"" + TimeToString(TimeCurrent()) + "\"}";
   h = FileOpen(ORDERS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, result); FileClose(h); }
}

//+------------------------------------------------------------------+
// PROCESS ORDER ARRAY
//+------------------------------------------------------------------+
void ProcessOrders(string content)
{
   int arrayStart = StringFind(content, "[");
   if(arrayStart < 0)
   {
      ProcessSingleOrder(content);
      return;
   }

   int bracketCount = 1;
   int arrayEnd = arrayStart + 1;
   while(arrayEnd < StringLen(content) && bracketCount > 0)
   {
      int ch = StringGetCharacter(content, arrayEnd);
      if(ch == '[') bracketCount++;
      else if(ch == ']') bracketCount--;
      if(bracketCount > 0) arrayEnd++;
   }

   string arr = StringSubstr(content, arrayStart + 1, arrayEnd - arrayStart - 1);
   int pos = 0, count = 0;

   while(pos < StringLen(arr))
   {
      int objStart = StringFind(arr, "{", pos);
      if(objStart < 0) break;
      int braceCount = 1;
      int objEnd = objStart + 1;
      while(objEnd < StringLen(arr) && braceCount > 0)
      {
         int ch = StringGetCharacter(arr, objEnd);
         if(ch == '{') braceCount++;
         else if(ch == '}') braceCount--;
         if(braceCount > 0) objEnd++;
      }
      string orderStr = StringSubstr(arr, objStart, objEnd - objStart + 1);
      if(StringFind(orderStr, "CANCEL_PENDING") >= 0)
      {
         // Cancel all pending
         for(int i = OrdersTotal() - 1; i >= 0; i--)
         {
            ulong ticket = OrderGetTicket(i);
            if(ticket > 0 && OrderGetString(ORDER_SYMBOL) == _Symbol)
               trade.OrderDelete(ticket);
         }
         pos = objEnd + 1;
         continue;
      }
      ProcessSingleOrder(orderStr);
      count++;
      pos = objEnd + 1;
      Sleep(50);
   }
   Print(">>> ", count, " ordres processades");
}

//+------------------------------------------------------------------+
// PROCESS SINGLE ORDER
//+------------------------------------------------------------------+
void ProcessSingleOrder(string orderStr)
{
   string action  = ExtractJSONString(orderStr, "action");
   string type    = ExtractJSONString(orderStr, "type");
   double price   = ExtractJSONDouble(orderStr, "price");
   double sl      = ExtractJSONDouble(orderStr, "sl");
   double tp      = ExtractJSONDouble(orderStr, "tp");
   double lot     = ExtractJSONDouble(orderStr, "lot");
   long   ticket  = (long)ExtractJSONDouble(orderStr, "ticket");
   int    magic   = (int)ExtractJSONDouble(orderStr, "magic");
   string comment = ExtractJSONString(orderStr, "comment");

   if(comment == "") comment = "Claude";
   if(lot <= 0) lot = DefaultLotSize;
   if(magic <= 0) magic = 12345;

   double bid = Bid(), ask = Ask();
   Print(">>> ORDER: ", action, " ", type, " lot=", lot, " magic=", magic);
   trade.SetExpertMagicNumber(magic);

   // Block new positions during CLOSE_ALL
   if(closeViaSL && (action == "MARKET" || action == "LIMIT" || action == "STOP"))
   {
      Print("!!! BLOCKED by closeViaSL");
      return;
   }

   // === MARKET ===
   if(action == "MARKET")
   {
      bool isBuy = (type == "BUY");
      if(!isBuy && type != "SELL") { Print("ERROR: type=", type); return; }

      // v13: lot count based on first adverse averaging level strength
      //  $x00/$x50 = strong → x2 | $x25/$x75 = weak → x1
      double unitLot = UNIT_LOT;
      if(unitLot <= 0) unitLot = 0.01;

      double estPx = isBuy ? Ask() : Bid();
      double firstAdverse;
      if(isBuy)   // BUY: adverse is below
         firstAdverse = MathCeil(estPx / 25.0) * 25.0 - 25.0;
      else         // SELL: adverse is above
         firstAdverse = MathFloor(estPx / 25.0) * 25.0 + 25.0;

      int fMod100 = (int)MathRound(MathMod(firstAdverse, 100.0));
      int fMod50  = (int)MathRound(MathMod(firstAdverse, 50.0));
      bool strongLevel = (fMod100 == 0 || fMod50 == 0);
      int numOrders = strongLevel ? 4 : 2;

      Print(">>> ENTRY CALC: px=$", DoubleToString(estPx, 2),
            " 1st_adverse=$", DoubleToString(firstAdverse, 0),
            " ", (strongLevel ? "STRONG" : "WEAK"),
            " -> x", numOrders);

      int opened = 0;
      double firstPrice = 0;
      for(int ord = 0; ord < numOrders; ord++)
      {
         string cm = "ENTRY_" + IntegerToString(ord + 1);
         bool result = isBuy ? trade.Buy(unitLot, _Symbol, 0, sl, tp, cm)
                             : trade.Sell(unitLot, _Symbol, 0, sl, tp, cm);
         if(result && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
         {
            double fillPrice = trade.ResultPrice();
            if(opened == 0) firstPrice = fillPrice;
            // NO afegim a avgPos — ENTRY no compta per averaging
            opened++;
         }
         else
            Print("!!! ENTRY ERROR #", ord+1, ": rc=", trade.ResultRetcode());
         if(ord < numOrders - 1) Sleep(50);
      }
      if(opened > 0)
      {
         // NO posem basketStructRefLevel aqui — l'entrada no es un nivell real
         // Es posara al primer averaging (que SI es un nivell centena)
         Print(">>> ENTRY: ", type, " ", opened, "x", DoubleToString(unitLot, 2),
               " = ", DoubleToString(unitLot * opened, 2),
               " @ ", DoubleToString(firstPrice, 2));
         WriteEvent("ORDER_OPENED", "ENTRY " + type + " " + IntegerToString(opened) + "x" + DoubleToString(unitLot, 2) + " @ " + DoubleToString(firstPrice, 2));
      }
   }
   // === BUY_LIMIT / SELL_LIMIT ===
   else if(action == "BUY_LIMIT" || action == "SELL_LIMIT")
   {
      bool isBuy = (action == "BUY_LIMIT");
      if(price <= 0) { Print("!!! LIMIT ERROR: price=0"); return; }
      bool result = isBuy ? trade.BuyLimit(lot, price, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment)
                          : trade.SellLimit(lot, price, _Symbol, sl, tp, ORDER_TIME_GTC, 0, comment);
      if(result && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
      {
         Print(">>> ", action, " PLACED: ", DoubleToString(lot, 2), " @ $", DoubleToString(price, 2),
               " SL=", DoubleToString(sl, 2), " TP=", DoubleToString(tp, 2));
         WriteEvent("LIMIT_PLACED", action + " " + DoubleToString(lot, 2) + " @ " + DoubleToString(price, 2));
      }
      else
         Print("!!! LIMIT ERROR: rc=", trade.ResultRetcode(), " retmsg=", trade.ResultRetcodeDescription());
   }
   // === CLOSE_TICKET ===
   else if(action == "CLOSE_TICKET" && ticket > 0)
   {
      if(PositionSelectByTicket((ulong)ticket))
      {
         trade.PositionClose((ulong)ticket);
         // Remove from avgPos
         for(int i = 0; i < avgPosCount; i++)
         {
            if(avgPosTickets[i] == ticket)
            {
               for(int j = i; j < avgPosCount - 1; j++)
               {
                  avgPosTickets[j] = avgPosTickets[j+1];
                  avgPosPrices[j] = avgPosPrices[j+1];
                  avgPosLots[j] = avgPosLots[j+1];
               }
               avgPosCount--;
               break;
            }
         }
         WriteEvent("CLOSE_TICKET", "Ticket " + IntegerToString(ticket));
      }
   }
   // === MODIFY_SL ===
   else if(action == "MODIFY_SL" && ticket > 0)
   {
      if(PositionSelectByTicket((ulong)ticket))
      {
         trade.PositionModify((ulong)ticket, sl, PositionGetDouble(POSITION_TP));
         WriteEvent("SL_MODIFIED", "Ticket " + IntegerToString(ticket) + " SL=" + DoubleToString(sl, 2));
      }
   }
   // === MODIFY_TP ===
   else if(action == "MODIFY_TP" && ticket > 0)
   {
      if(PositionSelectByTicket((ulong)ticket))
      {
         trade.PositionModify((ulong)ticket, PositionGetDouble(POSITION_SL), tp);
         WriteEvent("TP_MODIFIED", "Ticket " + IntegerToString(ticket) + " TP=" + DoubleToString(tp, 2));
      }
   }
   // === MODIFY_ALL_SL ===
   else if(action == "MODIFY_ALL_SL")
   {
      trade.SetAsyncMode(true);
      int mod = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tkt = PositionGetTicket(i);
         if(tkt > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            trade.PositionModify(tkt, sl, PositionGetDouble(POSITION_TP));
            mod++;
         }
      }
      trade.SetAsyncMode(false);
      WriteEvent("ALL_SL_MODIFIED", IntegerToString(mod) + " pos SL=" + DoubleToString(sl, 2));
   }
   // === MODIFY_ALL_TP ===
   else if(action == "MODIFY_ALL_TP")
   {
      trade.SetAsyncMode(true);
      int mod = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tkt = PositionGetTicket(i);
         if(tkt > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol)
         {
            trade.PositionModify(tkt, PositionGetDouble(POSITION_SL), tp);
            mod++;
         }
      }
      trade.SetAsyncMode(false);
      WriteEvent("ALL_TP_MODIFIED", IntegerToString(mod) + " pos TP=" + DoubleToString(tp, 2));
   }
   // === MOVE_SL_ENTRY (breakeven colectivo — SL = media ponderada de todas las posiciones) ===
   else if(action == "MOVE_SL_ENTRY")
   {
      trade.SetAsyncMode(false);
      // Paso 1: calcular media ponderada por lotes
      double totalLots = 0.0;
      double weightedSum = 0.0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tkt = PositionGetTicket(i);
         if(tkt == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         double lots = PositionGetDouble(POSITION_VOLUME);
         double openPx = PositionGetDouble(POSITION_PRICE_OPEN);
         totalLots += lots;
         weightedSum += openPx * lots;
      }
      if(totalLots <= 0.0)
      {
         WriteEvent("MOVE_SL_ENTRY", "0 pos (no positions found)");
         return;
      }
      double bePx = NormalizeDouble(weightedSum / totalLots, _Digits);
      // Paso 2: aplicar bePx como SL a todas las posiciones
      double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
      if(stopLvl < 0.10) stopLvl = 0.10;
      int mod = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tkt = PositionGetTicket(i);
         if(tkt == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         double newSL = bePx;
         // Ajuste por stop level minimo del broker
         if(pt == POSITION_TYPE_BUY && Bid() - newSL < stopLvl)
            newSL = NormalizeDouble(Bid() - stopLvl - 0.10, _Digits);
         else if(pt == POSITION_TYPE_SELL && newSL - Ask() < stopLvl)
            newSL = NormalizeDouble(Ask() + stopLvl + 0.10, _Digits);
         if(trade.PositionModify(tkt, newSL, PositionGetDouble(POSITION_TP)))
            mod++;
      }
      WriteEvent("MOVE_SL_ENTRY", IntegerToString(mod) + " pos SL->BE " + DoubleToString(bePx, 2));
   }
   // === NO_ACTION ===
   else if(action == "NO_ACTION")
   {
      // Do nothing
   }
   else
   {
      Print(">>> Unknown action: ", action);
   }

   // Restore default magic
   trade.SetExpertMagicNumber(12345);
}

//+------------------------------------------------------------------+
// VELOCITY — update every tick (ring buffer 500ms)
//+------------------------------------------------------------------+
void UpdateVelocity()
{
   double px = Bid();
   if(px <= 0) return;
   uint now = GetTickCount();
   if(now - _vel_lastStore >= 500)
   {
      _vel_prices[_vel_idx] = px;
      _vel_times[_vel_idx] = now;
      _vel_idx = (_vel_idx + 1) % 20;
      if(_vel_count < 20) _vel_count++;
      _vel_lastStore = now;
   }
   if(_vel_count >= 4)
   {
      int oldIdx = (_vel_idx - _vel_count + 20) % 20;
      int newIdx = (_vel_idx - 1 + 20) % 20;
      double timeDiff = (double)(_vel_times[newIdx] - _vel_times[oldIdx]) / 1000.0;
      if(timeDiff > 0)
      {
         _vel_speed = MathAbs(_vel_prices[newIdx] - _vel_prices[oldIdx]) / timeDiff;
         _vel_blocked = (_vel_speed > VEL_MAX);
      }
   }
}

void UpdateExtremeMoveState()
{
   double bid = Bid();
   double ask = Ask();
   double midPrice = (bid > 0 && ask > 0) ? ((bid + ask) / 2.0) : bid;
   if(midPrice <= 0) return;

   _atr_m1 = GetAtrM1(ExtremeATRPeriod);

   bool signalActive = (pythonEntryPrice > 0 && pythonDirection != 0 && !pythonClosing);
   double metric = midPrice;
   if(signalActive)
      metric = (pythonDirection == 1) ? (pythonEntryPrice - bid) : (ask - pythonEntryPrice);
   if(metric < 0) metric = 0;

   datetime now = TimeCurrent();
   if(now != _adv_last_store)
   {
      _adv_dist_hist[_adv_hist_idx] = metric;
      _adv_time_hist[_adv_hist_idx] = now;
      _adv_hist_idx = (_adv_hist_idx + 1) % EXTREME_BUF_MAX;
      if(_adv_hist_count < EXTREME_BUF_MAX) _adv_hist_count++;
      _adv_last_store = now;
   }

   _adverse_speed = 0.0;
   if(_adv_hist_count >= 6)
   {
      int newestIdx = (_adv_hist_idx - 1 + EXTREME_BUF_MAX) % EXTREME_BUF_MAX;
      int oldestIdx = (_adv_hist_idx - _adv_hist_count + EXTREME_BUF_MAX) % EXTREME_BUF_MAX;
      double metricDiff = _adv_dist_hist[newestIdx] - _adv_dist_hist[oldestIdx];
      double timeDiff = (double)(_adv_time_hist[newestIdx] - _adv_time_hist[oldestIdx]);
      if(timeDiff > 0)
      {
         if(signalActive)
         {
            if(metricDiff > 0) _adverse_speed = metricDiff / timeDiff;
         }
         else
         {
            _adverse_speed = MathAbs(metricDiff) / timeDiff;
         }
      }
   }

   int newRawState = 0;
   if(_atr_m1 > 0.0)
   {
      double speedPerMinute = _adverse_speed * 60.0;
      double speedRatio = speedPerMinute / _atr_m1;

      if(signalActive)
      {
         double distRatio = metric / _atr_m1;
         if(distRatio >= ExtremeDistATRExtreme && speedRatio >= ExtremeSpeedATRExtreme)
            newRawState = 2;
         else if(distRatio >= ExtremeDistATRWarn && speedRatio >= ExtremeSpeedATRWarn)
            newRawState = 1;
      }
      else
      {
         double moveRatio = 0.0;
         if(_adv_hist_count >= 6)
         {
            int newestIdx2 = (_adv_hist_idx - 1 + EXTREME_BUF_MAX) % EXTREME_BUF_MAX;
            int oldestIdx2 = (_adv_hist_idx - _adv_hist_count + EXTREME_BUF_MAX) % EXTREME_BUF_MAX;
            moveRatio = MathAbs(_adv_dist_hist[newestIdx2] - _adv_dist_hist[oldestIdx2]) / _atr_m1;
         }

         // Idle mode is intentionally stricter to avoid flagging normal market noise as EXTREME.
         if(moveRatio >= (ExtremeDistATRExtreme * 1.8) && speedRatio >= (ExtremeSpeedATRExtreme * 1.8))
            newRawState = 2;
         else if(moveRatio >= (ExtremeDistATRWarn * 1.4) && speedRatio >= (ExtremeSpeedATRWarn * 1.4))
            newRawState = 1;
      }
   }
   else
   {
      newRawState = 0;
   }

   if(newRawState != _extreme_raw_state)
   {
      _extreme_raw_state = newRawState;
      _extreme_since = now;
   }

   if(_extreme_raw_state > _extreme_state)
   {
      if(_extreme_since == 0) _extreme_since = now;
      if((now - _extreme_since) >= ExtremeConfirmSec)
      {
         _extreme_state = _extreme_raw_state;
         _extreme_hold_until = now + ExtremeHoldSec;
      }
   }
   else if(_extreme_raw_state < _extreme_state)
   {
      if(now >= _extreme_hold_until)
         _extreme_state = _extreme_raw_state;
   }
}

//+------------------------------------------------------------------+
// FIXED AVERAGING — every $2 up to $60
//+------------------------------------------------------------------+
// v11: Centenas helpers (ported from backtest)
bool IsHundredLevel(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   return (mod100 == 0);
}

bool IsStrongLevel(double levelPrice)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   return (mod100 == 0 || mod100 == 50);
}

double GetNextAdverseLevelPrice(double referencePrice, int direction)
{
   double step = 25.0;
   if(direction == -1)
   {
      double nextRound = MathCeil(referencePrice / step) * step;
      if(MathAbs(nextRound - referencePrice) < 0.10) nextRound += step;
      return NormalizeDouble(nextRound, _Digits);
   }
   double nextRound = MathFloor(referencePrice / step) * step;
   if(MathAbs(nextRound - referencePrice) < 0.10) nextRound -= step;
   return NormalizeDouble(nextRound, _Digits);
}

double GetNextAdverseStrongLevel(double referenceLevel, int direction)
{
   double level = NormalizeDouble(referenceLevel, _Digits);
   for(int guard = 0; guard < 20; ++guard)
   {
      double step = 25.0;
      double delta = (direction == 1 ? -step : step);
      level = NormalizeDouble(level + delta, _Digits);
      if(IsStrongLevel(level))
         return level;
   }
   return 0.0;
}

void GetLevelLotAndType(double levelPrice, double &lot, string &levelType)
{
   int mod100 = (int)MathRound(MathMod(levelPrice, 100.0));
   int mod50  = (int)MathRound(MathMod(levelPrice, 50.0));
   if(mod100 == 0)
   {
      lot = NormalizeDouble(UNIT_LOT * AVG_WEIGHT_STRONG, 2);
      levelType = "FORT $" + DoubleToString(levelPrice, 0);
   }
   else if(mod50 == 0)
   {
      lot = NormalizeDouble(UNIT_LOT * AVG_WEIGHT_MID, 2);
      levelType = "MIG $" + DoubleToString(levelPrice, 0);
   }
   else
   {
      lot = NormalizeDouble(UNIT_LOT * AVG_WEIGHT_WEAK, 2);
      levelType = "feble $" + DoubleToString(levelPrice, 0);
   }
}

void GetEntryLotAndType(double price, double &lot, string &levelType)
{
   double nearestLevel = MathRound(price / 25.0) * 25.0;
   int mod100 = (int)MathRound(MathMod(nearestLevel, 100.0));
   int mod50  = (int)MathRound(MathMod(nearestLevel, 50.0));
   if(mod100 == 0)
   {
      lot = NormalizeDouble(UNIT_LOT * ENTRY_WEIGHT_STRONG, 2);
      levelType = "FORT $" + DoubleToString(nearestLevel, 0);
   }
   else if(mod50 == 0)
   {
      lot = NormalizeDouble(UNIT_LOT * ENTRY_WEIGHT_MID, 2);
      levelType = "MIG $" + DoubleToString(nearestLevel, 0);
   }
   else
   {
      lot = NormalizeDouble(UNIT_LOT * ENTRY_WEIGHT_WEAK, 2);
      levelType = "feble $" + DoubleToString(nearestLevel, 0);
   }
}

double GetWeightedEntryPrice()
{
   double wSum = 0, tLots = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 12345) continue;
      double lots = PositionGetDouble(POSITION_VOLUME);
      double px = PositionGetDouble(POSITION_PRICE_OPEN);
      wSum += px * lots;
      tLots += lots;
   }
   if(tLots <= 0) return 0;
   return NormalizeDouble(wSum / tLots, _Digits);
}

// Get the best (closest to entry) and worst (furthest) open averaging level
bool GetOpenLevelBounds(double &bestLevel, double &worstLevel)
{
   bestLevel = 0; worstLevel = 0;
   if(avgPosCount <= 0) return false;
   for(int i = 0; i < avgPosCount; i++)
   {
      double px = avgPosPrices[i];
      if(bestLevel == 0 && worstLevel == 0) { bestLevel = px; worstLevel = px; }
      else { if(px < bestLevel) bestLevel = px; if(px > worstLevel) worstLevel = px; }
   }
   return (bestLevel > 0);
}

// --- CLAIMED LEVELS: prevent re-opening same centena ---
bool IsLevelClaimed(double level)
{
   for(int i = 0; i < claimedCount; i++)
      if(MathAbs(claimedLevels[i] - level) < 1.0) return true;
   return false;
}

void ClaimLevel(double level)
{
   if(claimedCount < CLAIMED_MAX)
   {
      claimedLevels[claimedCount] = NormalizeDouble(level, 0);
      claimedCount++;
      Print(">>> LEVEL CLAIMED: $", DoubleToString(level, 0), " (total: ", claimedCount, ")");
   }
}

void CheckFixedAveraging()
{
   if(pythonEntryPrice <= 0 || pythonDirection == 0) return;
   if(pythonClosing) return;

   double bid = Bid(), ask = Ask();
   double currentPrice = (pythonDirection == 1) ? bid : ask;

   // Must be adverse from entry
   if(pythonDirection == 1 && currentPrice >= pythonEntryPrice) return;
   if(pythonDirection == -1 && currentPrice <= pythonEntryPrice) return;

   // Cooldown: 1 averaging per M1 bar
   static datetime lastAvgBar = 0;
   datetime currentBar = iTime(_Symbol, PERIOD_M1, 0);
   if(currentBar == lastAvgBar) return;

   // Find nearest $25 level to current price
   double nearest = MathRound(currentPrice / 25.0) * 25.0;

   // Price must be within ±AVG_TOLERANCE of the level
   if(MathAbs(currentPrice - nearest) > AVG_TOLERANCE) return;

   // Level must be adverse from entry
   if(pythonDirection == 1 && nearest >= pythonEntryPrice) return;
   if(pythonDirection == -1 && nearest <= pythonEntryPrice) return;

   // CLAIMED CHECK: never re-open a level
   if(IsLevelClaimed(nearest)) return;

   // Level lot and type
   double avgLot = 0;
   string levelType = "";
   GetLevelLotAndType(nearest, avgLot, levelType);

   // === OPEN AVERAGING ORDERS ===
   lastAvgBar = currentBar;
   bool isBuy = (pythonDirection == 1);
   double unitLot = UNIT_LOT;
   if(unitLot <= 0) unitLot = 0.01;
   int numOrders = (int)MathRound(avgLot / unitLot);
   if(numOrders < 1) numOrders = 1;
   if(numOrders > 8) numOrders = 8;

   int opened = 0;
   double firstPrice = 0;
   double remaining = avgLot;

   for(int ord = 0; ord < numOrders; ord++)
   {
      double chunk = (ord < numOrders - 1) ? unitLot : NormalizeDouble(remaining, 2);
      if(chunk <= 0) continue;
      string cm = "AVG_" + DoubleToString(nearest, 0) + "_" + IntegerToString(ord + 1);
      bool result = isBuy ? trade.Buy(chunk, _Symbol, 0, 0, 0, cm)
                          : trade.Sell(chunk, _Symbol, 0, 0, 0, cm);

      if(result && (trade.ResultRetcode() == 10009 || trade.ResultRetcode() == 10008))
      {
         double fillPrice = trade.ResultPrice();
         long tkt = (long)trade.ResultOrder();
         if(opened == 0) firstPrice = fillPrice;

         if(avgPosCount < AVG_MAX)
         {
            avgPosTickets[avgPosCount] = tkt;
            avgPosPrices[avgPosCount] = fillPrice;
            avgPosLots[avgPosCount] = chunk;
            avgPosCount++;
         }
         opened++;
         remaining = NormalizeDouble(remaining - chunk, 2);
      }
      else
         Print("!!! AVG ORDER ERROR [", levelType, " #", ord+1, "]: rc=", trade.ResultRetcode());
      if(ord < numOrders - 1) Sleep(50);
   }

   if(opened > 0)
   {
      // CLAIM the level — can NEVER be re-opened
      ClaimLevel(nearest);

      double dist = (pythonDirection == 1)
         ? (pythonEntryPrice - currentPrice)
         : (currentPrice - pythonEntryPrice);
      avgLastOpenDist = dist;

      if(basketStructRefLevel <= 0)
         basketStructRefLevel = NormalizeDouble(nearest, _Digits);

      Print(">>> AVG [", levelType, "]: ",
            (isBuy ? "BUY" : "SELL"), " ", opened, "x", DoubleToString(unitLot, 2),
            " = ", DoubleToString(unitLot * opened, 2),
            " @ ", DoubleToString(firstPrice, 2),
            " level=$", DoubleToString(nearest, 0),
            " dist=$", DoubleToString(dist, 1),
            " claimed=", claimedCount);
      WriteEvent("AVG_OPENED", levelType + " " + IntegerToString(opened) + "x" + DoubleToString(unitLot, 2) + " @ $" + DoubleToString(nearest, 0));
      SaveAvgState();
   }
}

//+------------------------------------------------------------------+
// DD STOP — close all if drawdown >= 4.0% of account balance
//+------------------------------------------------------------------+
void ManageDDStop()
{
   if(pythonEntryPrice <= 0 || pythonDirection == 0) return;
   if(pythonClosing) return;
   if(CountPositions() <= 0) return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance <= 0) return;

   double ddPct = ((balance - equity) / balance) * 100.0;
   if(ddPct < 4.0) return;

   Print("!!! DD STOP: DD=", DoubleToString(ddPct, 2), "% (>=4.0%) bal=",
         DoubleToString(balance, 2), " eq=", DoubleToString(equity, 2));
   if(EnablePush)
      SendNotification(_Symbol + " | DD STOP " + DoubleToString(ddPct, 1) + "% — tancant tot");

   // Close all positions via async
   trade.SetAsyncMode(true);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 12345) continue;
      trade.PositionClose(tkt);
   }
   trade.SetAsyncMode(false);

   ClearAllAvgState();
   WriteEvent("DD_STOP", "DD=" + DoubleToString(ddPct, 2) + "% bal=" + DoubleToString(balance, 2) + " eq=" + DoubleToString(equity, 2));
}

//+------------------------------------------------------------------+
// BREAK-EVEN TRIGGER — +10 USD from weighted entry
//+------------------------------------------------------------------+
void ManageBreakEven()
{
   if(pythonEntryPrice <= 0 || pythonDirection == 0) return;
   if(beTriggered) return;
   if(CountPositions() <= 0) return;

   double weighted = GetWeightedEntryPrice();
   if(weighted <= 0) return;

   double currentPrice = (pythonDirection == 1) ? Bid() : Ask();
   double favorableDist = (pythonDirection == 1)
      ? (currentPrice - weighted)
      : (weighted - currentPrice);

   if(favorableDist < BE_TRIGGER_USD) return;

   // Set SL to weighted entry (break-even)
   beTriggered = true;
   Print(">>> BE TRIGGERED: weighted=", DoubleToString(weighted, 2),
         " profit=$", DoubleToString(favorableDist, 1));

   double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(stopLvl < 0.10) stopLvl = 0.10;

   int mod = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 12345) continue;
      ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double curSL = PositionGetDouble(POSITION_SL);
      double newSL = weighted;
      if(pt == POSITION_TYPE_BUY && Bid() - newSL < stopLvl)
         newSL = NormalizeDouble(Bid() - stopLvl - 0.10, _Digits);
      else if(pt == POSITION_TYPE_SELL && newSL - Ask() < stopLvl)
         newSL = NormalizeDouble(Ask() + stopLvl + 0.10, _Digits);
      // No empitjorar SL ja existent (entry locks, trailing, etc.)
      if(curSL > 0)
      {
         if(pt == POSITION_TYPE_BUY && curSL >= newSL) continue;
         if(pt == POSITION_TYPE_SELL && curSL <= newSL) continue;
      }
      if(trade.PositionModify(tkt, newSL, PositionGetDouble(POSITION_TP)))
         mod++;
   }
   Print(">>> BE SET: SL=", DoubleToString(weighted, 2), " on ", mod, " positions");
   WriteEvent("BE_TRIGGERED", "SL=" + DoubleToString(weighted, 2) + " profit=$" + DoubleToString(favorableDist, 1));
}

//+------------------------------------------------------------------+
// ENTRY PROFIT LOCKS — SL escalonat per assegurar posicions ENTRY
// Nomes ENTRY_, no AVG_. Ultima ENTRY = runner (sense lock).
// 2 entries: pos1 lock@$10, pos2 runner
// 4 entries: pos1 lock@$10, pos2 lock@$14, pos3 lock@$18, pos4 runner
//+------------------------------------------------------------------+
void CheckEntryProfitLocks()
{
   if(pythonDirection == 0 || pythonEntryPrice <= 0) return;
   if(pythonClosing) return;

   // Recollir posicions ENTRY_
   ulong  eTkts[];
   double ePrices[];
   int n = 0;

   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 12345) continue;
      if(StringFind(PositionGetString(POSITION_COMMENT), "ENTRY_") != 0) continue;

      ArrayResize(eTkts, n + 1);
      ArrayResize(ePrices, n + 1);
      eTkts[n]   = tkt;
      ePrices[n] = PositionGetDouble(POSITION_PRICE_OPEN);
      n++;
   }

   if(n < 2) return;  // minim 2 per tenir lock + runner

   // Ordenar per ticket (oldest first) — bubble sort petit
   for(int i = 0; i < n - 1; i++)
      for(int j = i + 1; j < n; j++)
         if(eTkts[j] < eTkts[i])
         {
            ulong tt = eTkts[i]; eTkts[i] = eTkts[j]; eTkts[j] = tt;
            double tp = ePrices[i]; ePrices[i] = ePrices[j]; ePrices[j] = tp;
         }

   bool   isBuy = (pythonDirection == 1);
   double px    = isBuy ? Bid() : Ask();
   double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(stopLvl < 0.10) stopLvl = 0.10;

   double thresh[3];
   thresh[0] = ENTRY_LOCK_1;
   thresh[1] = ENTRY_LOCK_2;
   thresh[2] = ENTRY_LOCK_3;

   // Lock posicions 0..n-2 (ultima = runner sense lock)
   int maxLocks = MathMin(n - 1, 3);
   for(int p = 0; p < maxLocks; p++)
   {
      double ep = ePrices[p];
      double lp = isBuy ? NormalizeDouble(ep + thresh[p], _Digits)
                        : NormalizeDouble(ep - thresh[p], _Digits);

      // Preu ha arribat al nivell de lock?
      bool reached = isBuy ? (px >= lp) : (px <= lp);
      if(!reached) continue;

      // Nomes millorar SL (mai empitjorar)
      if(!PositionSelectByTicket(eTkts[p])) continue;
      double curSL = PositionGetDouble(POSITION_SL);

      bool need = (curSL == 0);
      if(!need)
      {
         if(isBuy)  need = (curSL < lp);
         else       need = (curSL > lp);
      }
      if(!need) continue;

      // Respectar distancia minima stop level del broker
      double adjLP = lp;
      if(isBuy && px - adjLP < stopLvl)
         adjLP = NormalizeDouble(px - stopLvl - 0.10, _Digits);
      else if(!isBuy && adjLP - px < stopLvl)
         adjLP = NormalizeDouble(px + stopLvl + 0.10, _Digits);

      if(trade.PositionModify(eTkts[p], adjLP, PositionGetDouble(POSITION_TP)))
         Print(">>> ENTRY LOCK #", p + 1, ": tkt=", eTkts[p],
               " entry=$", DoubleToString(ep, 2),
               " +$", DoubleToString(thresh[p], 0),
               " SL->", DoubleToString(adjLP, 2));
   }
}

//+------------------------------------------------------------------+
// AUTO TRAILING SL — a la MEITAT de les posicions
// S'activa UNA SOLA VEGADA quan avanç >= AUTO_TRAIL_TRIGGER ($10)
// Selecciona la meitat de posicions (les de menys profit)
// SL = BE + 70% del peak (30% marge). Actualitza cada $0.50.
// L'altra meitat queda intacta amb el seu SL/TP original.
//+------------------------------------------------------------------+
void ManagePartialTP()
{
   if(pythonEntryPrice <= 0 || pythonDirection == 0) return;
   if(pythonClosing) return;

   int totalPos = CountPositions();
   if(totalPos <= 0)
   {
      if(autoTrailActive)
      {
         autoTrailActive = false;
         autoTrailPeak = 0;
         autoTrailLastSL = 0;
         autoTrailTicketCount = 0;
         Print(">>> AUTO TRAIL reset: 0 posicions");
      }
      return;
   }

   double weighted = GetWeightedEntryPrice();
   if(weighted <= 0) return;

   double currentPrice = (pythonDirection == 1) ? Bid() : Ask();
   double favorableDist = (pythonDirection == 1)
      ? (currentPrice - weighted)
      : (weighted - currentPrice);

   if(favorableDist <= 0) return;

   // === ACTIVACIÓ (una sola vegada): seleccionar meitat de posicions ===
   if(!autoTrailActive)
   {
      if(favorableDist < AUTO_TRAIL_TRIGGER) return;
      if(totalPos < 2) return;  // mínim 2 per poder partir

      // Recollir tickets i profits
      ulong  tmpTickets[20];
      double tmpProfits[20];
      int count = 0;
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong tkt = PositionGetTicket(i);
         if(tkt == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
         if(PositionGetInteger(POSITION_MAGIC) != 12345) continue;
         if(count < 20)
         {
            tmpTickets[count] = tkt;
            tmpProfits[count] = PositionGetDouble(POSITION_PROFIT);
            count++;
         }
      }

      if(count < 2) return;

      // Ordenar per profit ascendent (menys profit primer)
      for(int a = 0; a < count - 1; a++)
         for(int b = a + 1; b < count; b++)
            if(tmpProfits[b] < tmpProfits[a])
            {
               ulong  tmpT = tmpTickets[a]; tmpTickets[a] = tmpTickets[b]; tmpTickets[b] = tmpT;
               double tmpP = tmpProfits[a]; tmpProfits[a] = tmpProfits[b]; tmpProfits[b] = tmpP;
            }

      // Seleccionar la MEITAT (les de menys profit → trailing)
      int halfCount = count / 2;
      autoTrailTicketCount = 0;
      for(int h = 0; h < halfCount && h < 20; h++)
      {
         autoTrailTickets[autoTrailTicketCount] = tmpTickets[h];
         autoTrailTicketCount++;
      }

      autoTrailActive = true;
      autoTrailPeak = favorableDist;
      autoTrailLastSL = 0;

      Print(">>> AUTO TRAIL ACTIVAT: ", autoTrailTicketCount, "/", count,
            " posicions, BE=", DoubleToString(weighted, 2),
            " avanç=$", DoubleToString(favorableDist, 1));
      WriteEvent("AUTO_TRAIL_ON", IntegerToString(autoTrailTicketCount) + "/" +
                 IntegerToString(count) + " pos, BE=" + DoubleToString(weighted, 2) +
                 " advance=$" + DoubleToString(favorableDist, 1));
      // Caure directament al trailing per aplicar primer SL
   }

   // === TRAILING: només als tickets seleccionats ===
   if(autoTrailTicketCount <= 0) return;

   if(favorableDist > autoTrailPeak)
      autoTrailPeak = favorableDist;

   // SL ideal: BE + 70% del peak
   double idealSL;
   if(pythonDirection == 1)
      idealSL = weighted + autoTrailPeak * 0.70;
   else
      idealSL = weighted - autoTrailPeak * 0.70;

   idealSL = NormalizeDouble(idealSL, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));

   // SL mai retrocedeix
   if(pythonDirection == 1 && idealSL < autoTrailLastSL && autoTrailLastSL > 0) return;
   if(pythonDirection == -1 && idealSL > autoTrailLastSL && autoTrailLastSL > 0) return;

   // Només enviar si canvi >= $0.50
   if(MathAbs(idealSL - autoTrailLastSL) < 0.50) return;

   double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   if(stopLvl < 0.10) stopLvl = 0.10;

   int modified = 0;
   for(int t = 0; t < autoTrailTicketCount; t++)
   {
      ulong tkt = autoTrailTickets[t];
      if(!PositionSelectByTicket(tkt)) continue;

      double curSL = PositionGetDouble(POSITION_SL);
      double curTP = PositionGetDouble(POSITION_TP);
      long   posType = PositionGetInteger(POSITION_TYPE);

      bool valid = true;
      if(posType == POSITION_TYPE_BUY && idealSL > (Bid() - stopLvl))
         valid = false;
      if(posType == POSITION_TYPE_SELL && idealSL < (Ask() + stopLvl))
         valid = false;
      // No empitjorar SL existent
      if(posType == POSITION_TYPE_BUY && curSL > 0 && idealSL < curSL)
         valid = false;
      if(posType == POSITION_TYPE_SELL && curSL > 0 && idealSL > curSL)
         valid = false;

      if(valid)
      {
         if(trade.PositionModify(tkt, idealSL, curTP))
            modified++;
      }
   }

   autoTrailLastSL = idealSL;
   Print(">>> AUTO TRAIL SL -> ", DoubleToString(idealSL, 2),
         " (peak=$", DoubleToString(autoTrailPeak, 1),
         " 70%=$", DoubleToString(autoTrailPeak * 0.70, 1),
         " mod=", modified, "/", autoTrailTicketCount, ")");
   WriteEvent("AUTO_TRAIL_SL", "SL=" + DoubleToString(idealSL, 2) +
              " peak=$" + DoubleToString(autoTrailPeak, 1) +
              " modified=" + IntegerToString(modified));
}

//+------------------------------------------------------------------+
// RESET_SL TRAILING — trail $1 increments
//+------------------------------------------------------------------+
void ManageResetTrailing()
{
   if(resetTrailCount <= 0) return;
   double bid = Bid(), ask = Ask();

   for(int i = resetTrailCount - 1; i >= 0; i--)
   {
      ulong tkt = resetTrailTickets[i];
      if(!PositionSelectByTicket(tkt))
      {
         // Position closed, remove from tracking
         for(int j = i; j < resetTrailCount - 1; j++)
         {
            resetTrailTickets[j] = resetTrailTickets[j+1];
            resetTrailLastSL[j] = resetTrailLastSL[j+1];
         }
         resetTrailCount--;
         continue;
      }

      ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double currentSL = PositionGetDouble(POSITION_SL);

      if(pt == POSITION_TYPE_BUY)
      {
         double newSL = NormalizeDouble(bid - 0.50, _Digits);
         if(newSL > currentSL + CLOSE_SL_MIN_MOVE)
         {
            double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
            if(bid - newSL >= stopLvl)
            {
               trade.PositionModify(tkt, newSL, PositionGetDouble(POSITION_TP));
               resetTrailLastSL[i] = newSL;
            }
         }
      }
      else
      {
         double newSL = NormalizeDouble(ask + 0.50, _Digits);
         if(currentSL == 0 || newSL < currentSL - CLOSE_SL_MIN_MOVE)
         {
            double stopLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
            if(newSL - ask >= stopLvl)
            {
               trade.PositionModify(tkt, newSL, PositionGetDouble(POSITION_TP));
               resetTrailLastSL[i] = newSL;
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
// AVERAGING STATE PERSISTENCE
//+------------------------------------------------------------------+
void SaveAvgState()
{
   string json = "{\"entry_price\": " + DoubleToString(pythonEntryPrice, 2) +
                 ", \"direction\": " + IntegerToString(pythonDirection) +
                 ", \"avg_count\": " + IntegerToString(avgPosCount) +
                 ", \"last_dist\": " + DoubleToString(avgLastOpenDist, 2) +
                 ", \"tickets\": [";
   for(int i = 0; i < avgPosCount; i++)
   {
      if(i > 0) json += ",";
      json += "{\"t\":" + IntegerToString(avgPosTickets[i]) +
              ",\"p\":" + DoubleToString(avgPosPrices[i], 2) +
              ",\"l\":" + DoubleToString(avgPosLots[i], 2) + "}";
   }
   json += "], \"claimed\": [";
   for(int i = 0; i < claimedCount; i++)
   {
      if(i > 0) json += ",";
      json += DoubleToString(claimedLevels[i], 0);
   }
   json += "]}";

   int h = FileOpen("claude_avg_state.json", FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, json); FileClose(h); }
}

void LoadAvgState()
{
   int h = FileOpen("claude_avg_state.json", FILE_READ|FILE_BIN|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   string content = FileReadString(h, (int)FileSize(h));
   FileClose(h);
   if(StringLen(content) < 5) return;

   pythonEntryPrice = ExtractJSONDouble(content, "entry_price");
   pythonDirection = (int)ExtractJSONDouble(content, "direction");
   avgPosCount = (int)ExtractJSONDouble(content, "avg_count");
   avgLastOpenDist = ExtractJSONDouble(content, "last_dist");

   // Parse tickets array
   int arrStart = StringFind(content, "\"tickets\"");
   if(arrStart >= 0) arrStart = StringFind(content, "[", arrStart);
   if(arrStart < 0) { avgPosCount = 0; return; }

   int loaded = 0;
   int pos = arrStart;
   while(loaded < avgPosCount && loaded < AVG_MAX)
   {
      int objS = StringFind(content, "{", pos);
      if(objS < 0) break;
      int objE = StringFind(content, "}", objS);
      if(objE < 0) break;
      string obj = StringSubstr(content, objS, objE - objS + 1);
      avgPosTickets[loaded] = (long)ExtractJSONDouble(obj, "t");
      avgPosPrices[loaded] = ExtractJSONDouble(obj, "p");
      avgPosLots[loaded] = ExtractJSONDouble(obj, "l");
      loaded++;
      pos = objE + 1;
   }
   avgPosCount = loaded;

   // Parse claimed levels array
   claimedCount = 0;
   int clIdx = StringFind(content, "\"claimed\"");
   if(clIdx >= 0)
   {
      int clArr = StringFind(content, "[", clIdx);
      int clEnd = StringFind(content, "]", clArr);
      if(clArr >= 0 && clEnd > clArr + 1)
      {
         string clStr = StringSubstr(content, clArr + 1, clEnd - clArr - 1);
         int cpos = 0;
         while(cpos < StringLen(clStr) && claimedCount < CLAIMED_MAX)
         {
            // Skip whitespace and commas
            while(cpos < StringLen(clStr))
            {
               ushort ch = StringGetCharacter(clStr, cpos);
               if(ch != ' ' && ch != ',' && ch != '\n') break;
               cpos++;
            }
            if(cpos >= StringLen(clStr)) break;
            // Read number
            int numStart = cpos;
            while(cpos < StringLen(clStr))
            {
               ushort ch = StringGetCharacter(clStr, cpos);
               if(ch == ',' || ch == ' ' || ch == ']') break;
               cpos++;
            }
            if(cpos > numStart)
            {
               double val = StringToDouble(StringSubstr(clStr, numStart, cpos - numStart));
               if(val > 0)
               {
                  claimedLevels[claimedCount] = val;
                  claimedCount++;
               }
            }
         }
      }
   }

   // Verify positions still exist
   ReconcileAvgPositions();

   Print(">>> AVG STATE LOADED: ", avgPosCount, " pos, ", claimedCount, " claimed, entry=", DoubleToString(pythonEntryPrice, 2));
}

void ReconcileAvgPositions()
{
   // Step 1: Remove entries where position no longer exists
   for(int i = avgPosCount - 1; i >= 0; i--)
   {
      if(!PositionSelectByTicket((ulong)avgPosTickets[i]))
      {
         Print(">>> RECONCILE: Removed closed ticket ", avgPosTickets[i]);
         for(int j = i; j < avgPosCount - 1; j++)
         {
            avgPosTickets[j] = avgPosTickets[j+1];
            avgPosPrices[j] = avgPosPrices[j+1];
            avgPosLots[j] = avgPosLots[j+1];
         }
         avgPosCount--;
      }
   }

   // Step 2: Remove ENTRY positions from avgPos (may be loaded from old state file)
   for(int i = avgPosCount - 1; i >= 0; i--)
   {
      if(PositionSelectByTicket((ulong)avgPosTickets[i]))
      {
         string cm = PositionGetString(POSITION_COMMENT);
         if(StringFind(cm, "ENTRY") >= 0)
         {
            Print(">>> RECONCILE: Removed ENTRY ticket ", avgPosTickets[i], " from avgPos");
            for(int j = i; j < avgPosCount - 1; j++)
            {
               avgPosTickets[j] = avgPosTickets[j+1];
               avgPosPrices[j] = avgPosPrices[j+1];
               avgPosLots[j] = avgPosLots[j+1];
            }
            avgPosCount--;
         }
      }
   }

   // Step 3: Add magic=12345 AVG positions not in array (SKIP ENTRY positions)
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 12345) continue;

      string posComment = PositionGetString(POSITION_COMMENT);
      if(StringFind(posComment, "ENTRY") >= 0) continue;

      bool found = false;
      for(int j = 0; j < avgPosCount; j++)
         if(avgPosTickets[j] == (long)tkt) { found = true; break; }

      if(!found && avgPosCount < AVG_MAX)
      {
         avgPosTickets[avgPosCount] = (long)tkt;
         avgPosPrices[avgPosCount] = PositionGetDouble(POSITION_PRICE_OPEN);
         avgPosLots[avgPosCount] = PositionGetDouble(POSITION_VOLUME);
         avgPosCount++;
         Print(">>> RECONCILE: Added AVG ticket ", tkt, " comment=", posComment);
      }
   }

   // Step 4: Claim levels for ALL positions in avgPos (safety net)
   for(int i = 0; i < avgPosCount; i++)
   {
      double nearestLvl = MathRound(avgPosPrices[i] / 25.0) * 25.0;
      if(!IsLevelClaimed(nearestLvl))
         ClaimLevel(nearestLvl);
   }

   // Recalculate avgLastOpenDist from furthest position
   if(avgPosCount > 0 && pythonEntryPrice > 0 && pythonDirection != 0)
   {
      double maxDist = 0;
      for(int i = 0; i < avgPosCount; i++)
      {
         double d = (pythonDirection == 1)
            ? (pythonEntryPrice - avgPosPrices[i])
            : (avgPosPrices[i] - pythonEntryPrice);
         if(d > maxDist) maxDist = d;
      }
      if(maxDist > avgLastOpenDist)
      {
         avgLastOpenDist = maxDist;
         Print(">>> RECONCILE: avgLastOpenDist = $", DoubleToString(avgLastOpenDist, 1));
      }
   }
   Print(">>> RECONCILE DONE: avgPos=", avgPosCount, " claimed=", claimedCount);
}

void ClearAllAvgState()
{
   avgPosCount = 0;
   avgLastOpenDist = 0;
   claimedCount = 0;  // Reset claimed levels
   _vel_count = 0;
   _vel_idx = 0;
   _vel_speed = 0;
   _vel_blocked = false;
   _adv_hist_count = 0;
   _adv_hist_idx = 0;
   _adv_last_store = 0;
   _adverse_speed = 0.0;
   _atr_m1 = 0.0;
   _extreme_raw_state = 0;
   _extreme_state = 0;
   _extreme_since = 0;
   _extreme_hold_until = 0;
   pendingReject = false;
   pendingRejectLevel = 0.0;
   beTriggered = false;
   basketStructRefLevel = 0.0;
   autoTrailActive = false;
   autoTrailPeak = 0;
   autoTrailLastSL = 0;
   autoTrailTicketCount = 0;
   Print(">>> AVG STATE CLEARED (incl claimed levels)");
   SaveAvgState();
}

void UpdateAvgPositionStatus()
{
   for(int i = avgPosCount - 1; i >= 0; i--)
   {
      if(!PositionSelectByTicket((ulong)avgPosTickets[i]))
      {
         for(int j = i; j < avgPosCount - 1; j++)
         {
            avgPosTickets[j] = avgPosTickets[j+1];
            avgPosPrices[j] = avgPosPrices[j+1];
            avgPosLots[j] = avgPosLots[j+1];
         }
         avgPosCount--;
      }
   }
}

//+------------------------------------------------------------------+
// POSITION CHANGES — detect opens/closes for Python
//+------------------------------------------------------------------+
void CheckPositionChanges()
{
   int current = CountPositions();
   if(current != previousPositions)
   {
      if(current > previousPositions)
         WriteEvent("POSITION_OPENED", IntegerToString(current - previousPositions) + " new positions (total " + IntegerToString(current) + ")");
      else
         WriteEvent("POSITION_CLOSED", IntegerToString(previousPositions - current) + " closed (total " + IntegerToString(current) + ")");
      previousPositions = current;
   }
}

//+------------------------------------------------------------------+
// EXPORT POSITIONS — JSON for Python
//+------------------------------------------------------------------+
void ExportPositions()
{
   string json = "{\n";

   // Account
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin  = AccountInfoDouble(ACCOUNT_MARGIN);
   json += "  \"account_balance\": " + DoubleToString(balance, 2) + ",\n";
   json += "  \"account_equity\": " + DoubleToString(equity, 2) + ",\n";
   json += "  \"account\": {\"balance\": " + DoubleToString(balance, 2) +
           ", \"equity\": " + DoubleToString(equity, 2) +
           ", \"margin\": " + DoubleToString(margin, 2) +
           ", \"free_margin\": " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + "},\n";

   // Market
   double bid = Bid(), ask = Ask();
   json += "  \"market\": {\"bid\": " + DoubleToString(bid, _Digits) +
           ", \"ask\": " + DoubleToString(ask, _Digits) +
           ", \"spread_points\": " + DoubleToString((ask - bid) / _Point, 1) + "},\n";

   // Positions
   json += "  \"positions\": [\n";
   bool first = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tkt = PositionGetTicket(i);
      if(tkt == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      if(!first) json += ",\n";
      first = false;

      ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      string typeStr = (pt == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      double openPx = PositionGetDouble(POSITION_PRICE_OPEN);
      double lots = PositionGetDouble(POSITION_VOLUME);
      double profit = PositionGetDouble(POSITION_PROFIT);
      double slVal = PositionGetDouble(POSITION_SL);
      double tpVal = PositionGetDouble(POSITION_TP);
      long mgc = PositionGetInteger(POSITION_MAGIC);
      string cm = PositionGetString(POSITION_COMMENT);

      json += "    {\"ticket\": " + IntegerToString((long)tkt) +
              ", \"type\": \"" + typeStr + "\"" +
              ", \"lots\": " + DoubleToString(lots, 2) +
              ", \"open_price\": " + DoubleToString(openPx, _Digits) +
              ", \"sl\": " + DoubleToString(slVal, _Digits) +
              ", \"tp\": " + DoubleToString(tpVal, _Digits) +
              ", \"profit\": " + DoubleToString(profit, 2) +
              ", \"magic\": " + IntegerToString(mgc) +
              ", \"comment\": \"" + cm + "\"}";
   }
   json += "\n  ],\n";

   // Averaging state (v11 centenas)
   double wEntry = GetWeightedEntryPrice();
   json += "  \"smart_avg\": {\"avg_count\": " + IntegerToString(avgPosCount) +
           ", \"last_dist\": " + DoubleToString(avgLastOpenDist, 1) +
           ", \"step\": 25" +
           ", \"unit_lot\": " + DoubleToString(UNIT_LOT, 2) +
           ", \"lot_weak\": " + DoubleToString(UNIT_LOT * AVG_WEIGHT_WEAK, 2) +
           ", \"lot_mid\": " + DoubleToString(UNIT_LOT * AVG_WEIGHT_MID, 2) +
           ", \"lot_strong\": " + DoubleToString(UNIT_LOT * AVG_WEIGHT_STRONG, 2) +
           ", \"avg_tolerance\": " + DoubleToString(AVG_TOLERANCE, 1) +
           ", \"be_triggered\": " + (beTriggered ? "true" : "false") +
           ", \"pending_reject\": " + (pendingReject ? "true" : "false") +
           ", \"weighted_entry\": " + DoubleToString(wEntry, 2) +
           ", \"vel\": " + DoubleToString(_vel_speed, 2) +
           ", \"vel_blocked\": " + (_vel_blocked ? "true" : "false") +
           ", \"claimed_count\": " + IntegerToString(claimedCount) + "},\n";

   // Today's history
   double todayProfit = 0, todayComm = 0, todaySwap = 0;
   int todayTrades = 0;
   datetime todayStart = StringToTime(TimeToString(TimeCurrent(), TIME_DATE));
   HistorySelect(todayStart, TimeCurrent());
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
   {
      ulong dealTkt = HistoryDealGetTicket(i);
      if(dealTkt == 0) continue;
      if(HistoryDealGetString(dealTkt, DEAL_SYMBOL) != _Symbol) continue;
      if(HistoryDealGetInteger(dealTkt, DEAL_ENTRY) == DEAL_ENTRY_OUT)
      {
         todayProfit += HistoryDealGetDouble(dealTkt, DEAL_PROFIT);
         todayComm += HistoryDealGetDouble(dealTkt, DEAL_COMMISSION);
         todaySwap += HistoryDealGetDouble(dealTkt, DEAL_SWAP);
         todayTrades++;
      }
   }
   json += "  \"history\": {\"today_profit\": " + DoubleToString(todayProfit, 2) +
           ", \"today_commission\": " + DoubleToString(todayComm, 2) +
           ", \"today_swap\": " + DoubleToString(todaySwap, 2) +
           ", \"today_trades\": " + IntegerToString(todayTrades) + "}\n";

   json += "}";

   int h = FileOpen(STATUS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE) { FileWriteString(h, json); FileClose(h); }
}

//+------------------------------------------------------------------+
// CHART DISPLAY — minimal info via OBJ_LABEL
//+------------------------------------------------------------------+
void UpdateChartDisplay()
{
   string prefix = "CB_";
   string fontName = "Consolas";
   int fontSize = 9;
   color clrNormal = clrWhite;
   color lineColors[];

   string lines[];
   int count = 5;
   ArrayResize(lines, count);
   ArrayResize(lineColors, count);
   for(int j = 0; j < count; j++) lineColors[j] = clrNormal;

   string velSt = _vel_blocked ? "BLOCKED" : "OK";
   lines[0] = "Vel: $" + DoubleToString(_vel_speed, 2) + "/s [" + velSt + "]";
   if(_vel_blocked) lineColors[0] = clrOrange;

   string dirStr = (pythonDirection == 1) ? "BUY" : (pythonDirection == -1) ? "SELL" : "--";
   lines[1] = "Entry: $" + DoubleToString(pythonEntryPrice, 2) + " [" + dirStr + "]";

   double dist = 0;
   if(pythonEntryPrice > 0 && pythonDirection != 0)
   {
      double px = (pythonDirection == 1) ? Bid() : Ask();
      dist = (pythonDirection == 1) ? (pythonEntryPrice - px) : (px - pythonEntryPrice);
      if(dist < 0) dist = 0;
   }
   string beStr = beTriggered ? " [BE]" : "";
   string rejStr = pendingReject ? " [REJ:" + DoubleToString(pendingRejectLevel, 0) + "]" : "";
   lines[2] = "Avg: " + IntegerToString(avgPosCount) + " Clm: " + IntegerToString(claimedCount) + " | Dist: $" + DoubleToString(dist, 1) + beStr + rejStr;
   string extremeLabel = "NORMAL";
   if(_extreme_state == 1) extremeLabel = "VIGILANT";
   else if(_extreme_state >= 2) extremeLabel = "EXTREM";
   lines[3] = "Flow: " + extremeLabel + " | ATR: $" + DoubleToString(_atr_m1, 2) +
              " | Speed: $" + DoubleToString(_adverse_speed * 60.0, 2) + "/min";
   if(_extreme_state == 1) lineColors[3] = clrGold;
   else if(_extreme_state >= 2) lineColors[3] = clrTomato;
   else lineColors[3] = clrLimeGreen;
   lines[4] = "Equity: $" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 0);

   // Delete old
   for(int i = 0; i < 10; i++)
      ObjectDelete(0, prefix + IntegerToString(i));

   // Create labels
   int startX = 15, startY = 30, lineH = 18;
   for(int i = 0; i < count; i++)
   {
      string name = prefix + IntegerToString(i);
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, startX);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, startY + i * lineH);
      ObjectSetString(0, name, OBJPROP_TEXT, lines[i]);
      ObjectSetString(0, name, OBJPROP_FONT, fontName);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
      ObjectSetInteger(0, name, OBJPROP_COLOR, lineColors[i]);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   }
   ChartRedraw(0);
}
//+------------------------------------------------------------------+
