//+------------------------------------------------------------------+
//| XiscoMirrorEA_v1_MT5.mq5 — Pure copier slave for Xisco signals    |
//|                                                                   |
//| Reads:  xisco_orders.json  (from xisco_mirror.py)                 |
//| Writes: xisco_positions.json (positions echo, filtered by MAGIC)  |
//|                                                                   |
//| MAGIC = 88888 (Brain=99999, DualGrid=77777, Xisco=88888)          |
//|                                                                   |
//| Actions:                                                           |
//|   MARKET      — open BUY/SELL at market with lot+comment          |
//|   MODIFY_SL   — set SL on ticket                                   |
//|   MODIFY_TP   — set TP on ticket                                   |
//|   CLOSE_TICKET — close specific ticket                             |
//|                                                                   |
//| ALWAYS attaches to a chart of XAUUSD-VIPc (or whatever symbol the |
//| signals are for). Magic-isolates so it never touches Brain/DualGrid|
//| positions.                                                         |
//+------------------------------------------------------------------+
#property copyright "Xisco Mirror v1"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

// ─── INPUTS ──────────────────────────────────────────────────────
input long     XiscoMagic         = 88888;        // magic for mirror trades
input int      OrdersPollMs       = 50;           // poll xisco_orders.json (50ms = 20Hz)
input int      PositionsWriteSec  = 2;            // write echo every N sec
input int      DefaultSlippage    = 30;
input bool     ProcessOnTick      = true;         // also process orders on every market tick
input string   CommentTag         = "MIRROR";     // comment prefix

// ─── GLOBALS ─────────────────────────────────────────────────────
CTrade trade;
string ORDERS_FILE    = "xisco_orders.json";
string POSITIONS_FILE = "xisco_positions.json";
datetime last_positions_write = 0;
string LAST_ORDER_HASH = "";

// ─── JSON HELPERS (light) ────────────────────────────────────────
string ExtractStr(const string src, const string key)
{
   string pat = "\"" + key + "\":\"";
   int p = StringFind(src, pat);
   if(p < 0) return "";
   p += StringLen(pat);
   int e = StringFind(src, "\"", p);
   if(e < 0) return "";
   return StringSubstr(src, p, e - p);
}

double ExtractNum(const string src, const string key)
{
   string pat = "\"" + key + "\":";
   int p = StringFind(src, pat);
   if(p < 0) return 0;
   p += StringLen(pat);
   while(p < StringLen(src) && StringGetCharacter(src,p)==' ') p++;
   int e = p;
   while(e < StringLen(src))
   {
      ushort c = StringGetCharacter(src, e);
      if(c == ',' || c == '}' || c == ' ' || c == '\n' || c == '\r') break;
      e++;
   }
   return StringToDouble(StringSubstr(src, p, e - p));
}

// ─── FILE I/O ────────────────────────────────────────────────────
string ReadOrdersFile()
{
   int h = FileOpen(ORDERS_FILE, FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE) return "";
   string content = "";
   while(!FileIsEnding(h)) content += FileReadString(h);
   FileClose(h);
   return content;
}

void MarkOrdersProcessed()
{
   int h = FileOpen(ORDERS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, "{\"status\":\"PROCESSED\",\"ts\":" + IntegerToString(TimeCurrent()) + "}");
      FileClose(h);
   }
}

// ─── POSITIONS ECHO ──────────────────────────────────────────────
bool IsOurPosition()
{
   // Already selected position before calling
   if(PositionGetInteger(POSITION_MAGIC) != XiscoMagic) return false;
   if(PositionGetString(POSITION_SYMBOL) != _Symbol) return false;
   return true;
}

void WritePositionsEcho()
{
   string json = "{";
   json += "\"ts\":" + IntegerToString(TimeCurrent()) + ",";
   json += "\"magic\":" + IntegerToString(XiscoMagic) + ",";
   json += "\"symbol\":\"" + _Symbol + "\",";
   json += "\"account\":{";
   json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   json += "\"equity\":"  + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2);
   json += "},";
   json += "\"positions\":[";

   bool first = true;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(!IsOurPosition()) continue;
      if(!first) json += ",";
      first = false;
      json += "{";
      json += "\"ticket\":" + IntegerToString((long)tk) + ",";
      json += "\"type\":\"" + (PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?"BUY":"SELL") + "\",";
      json += "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME), 2) + ",";
      json += "\"price_open\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), 5) + ",";
      json += "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL), 5) + ",";
      json += "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP), 5) + ",";
      json += "\"profit\":" + DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + ",";
      json += "\"time\":" + IntegerToString((long)PositionGetInteger(POSITION_TIME)) + ",";
      json += "\"comment\":\"" + PositionGetString(POSITION_COMMENT) + "\"";
      json += "}";
   }
   json += "]}";

   int h = FileOpen(POSITIONS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

// ─── ACTIONS ─────────────────────────────────────────────────────
bool ActMarket(const string type, const double lot, const double sl, const double tp, const string comment)
{
   trade.SetExpertMagicNumber(XiscoMagic);
   trade.SetDeviationInPoints(DefaultSlippage);
   string sym = _Symbol;
   string full_comment = CommentTag + "_" + comment;
   bool ok = false;
   if(type == "BUY")
      ok = trade.Buy(lot, sym, 0, sl, tp, full_comment);
   else if(type == "SELL")
      ok = trade.Sell(lot, sym, 0, sl, tp, full_comment);

   if(ok)
      Print("XISCO MARKET ", type, " ", DoubleToString(lot,2), " OK ticket=", trade.ResultOrder(), " comment=", full_comment);
   else
      Print("XISCO MARKET ", type, " FAILED: ", trade.ResultRetcode(), " ", trade.ResultComment());
   return ok;
}

bool ActCloseTicket(const long ticket)
{
   if(!PositionSelectByTicket((ulong)ticket))
   {
      Print("XISCO CLOSE_TICKET: ticket ", ticket, " not found");
      return false;
   }
   if(!IsOurPosition())
   {
      Print("XISCO CLOSE_TICKET: ticket ", ticket, " not managed by Xisco mirror");
      return false;
   }
   trade.SetExpertMagicNumber(XiscoMagic);
   bool ok = trade.PositionClose((ulong)ticket);
   Print("XISCO CLOSE_TICKET ", ticket, " ", ok ? "OK" : "FAIL");
   return ok;
}

bool ActModifySL(const long ticket, const double sl)
{
   if(!PositionSelectByTicket((ulong)ticket)) return false;
   if(!IsOurPosition()) return false;
   double tp = PositionGetDouble(POSITION_TP);
   bool ok = trade.PositionModify((ulong)ticket, sl, tp);
   Print("XISCO MODIFY_SL ", ticket, " sl=", sl, " ", ok ? "OK" : "FAIL");
   return ok;
}

bool ActModifyTP(const long ticket, const double tp)
{
   if(!PositionSelectByTicket((ulong)ticket)) return false;
   if(!IsOurPosition()) return false;
   double sl = PositionGetDouble(POSITION_SL);
   bool ok = trade.PositionModify((ulong)ticket, sl, tp);
   Print("XISCO MODIFY_TP ", ticket, " tp=", tp, " ", ok ? "OK" : "FAIL");
   return ok;
}

// ─── ORDER PROCESSING ────────────────────────────────────────────
void ProcessOrders()
{
   string content = ReadOrdersFile();
   if(content == "") return;
   // Skip if already processed
   if(StringFind(content, "\"status\":\"PROCESSED\"") >= 0) return;
   // Dedup via hash (simple: full content)
   if(content == LAST_ORDER_HASH) return;
   LAST_ORDER_HASH = content;

   // Cerquem el primer "orders":[ array — processem en ordre
   int p = StringFind(content, "\"orders\"");
   if(p < 0)
   {
      MarkOrdersProcessed();
      return;
   }
   // Cada order està entre { i } dins l'array.
   int start = StringFind(content, "{", p);
   while(start >= 0)
   {
      int end = StringFind(content, "}", start);
      if(end < 0) break;
      string ord = StringSubstr(content, start, end - start + 1);

      string action = ExtractStr(ord, "action");
      if(action == "MARKET")
      {
         string type = ExtractStr(ord, "type");
         double lot = ExtractNum(ord, "lot");
         double sl  = ExtractNum(ord, "sl");
         double tp  = ExtractNum(ord, "tp");
         string comment = ExtractStr(ord, "comment");
         if(lot > 0 && (type == "BUY" || type == "SELL"))
            ActMarket(type, lot, sl, tp, comment);
      }
      else if(action == "CLOSE_TICKET")
      {
         long ticket = (long)ExtractNum(ord, "ticket");
         if(ticket > 0) ActCloseTicket(ticket);
      }
      else if(action == "MODIFY_SL")
      {
         long ticket = (long)ExtractNum(ord, "ticket");
         double sl = ExtractNum(ord, "sl");
         if(ticket > 0) ActModifySL(ticket, sl);
      }
      else if(action == "MODIFY_TP")
      {
         long ticket = (long)ExtractNum(ord, "ticket");
         double tp = ExtractNum(ord, "tp");
         if(ticket > 0) ActModifyTP(ticket, tp);
      }
      // Next order
      start = StringFind(content, "{", end);
      if(start >= StringFind(content, "]", p)) break;
   }
   MarkOrdersProcessed();
}

// ─── LIFECYCLE ───────────────────────────────────────────────────
int OnInit()
{
   trade.SetExpertMagicNumber(XiscoMagic);
   EventSetMillisecondTimer(OrdersPollMs);
   Print("XiscoMirrorEA v1 STARTED — magic=", XiscoMagic, " symbol=", _Symbol);
   WritePositionsEcho();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("XiscoMirrorEA STOPPED (reason=", reason, ")");
}

void OnTimer()
{
   ProcessOrders();
   datetime now = TimeCurrent();
   if(now - last_positions_write >= PositionsWriteSec)
   {
      WritePositionsEcho();
      last_positions_write = now;
   }
}

void OnTick()
{
   // Tick = oportunitat instant a per processar ordres pendents (no esperem timer)
   if(ProcessOnTick) ProcessOrders();
   // Echo posicions periodic
   datetime now = TimeCurrent();
   if(now - last_positions_write >= PositionsWriteSec)
   {
      WritePositionsEcho();
      last_positions_write = now;
   }
}

void OnTradeTransaction(const MqlTradeTransaction& trans, const MqlTradeRequest& req, const MqlTradeResult& res)
{
   // Quan hi ha qualsevol transacció (open/close/modify), refresquem l'echo
   // perquè Python pugui mapejar el ticket nou immediatament.
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      WritePositionsEcho();
      last_positions_write = TimeCurrent();
   }
}
