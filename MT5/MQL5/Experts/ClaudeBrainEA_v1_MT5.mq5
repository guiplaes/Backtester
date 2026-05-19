//+------------------------------------------------------------------+
//| ClaudeBrainEA_v1_MT5.mq5 — Brain-dedicated EA for Brain v3        |
//| Communicates with trader_brain.py via JSON files in Common folder |
//|                                                                   |
//| Files (Common\Files\):                                            |
//|   READ:  brain_orders.json       (from brain, orders to execute) |
//|   WRITE: brain_positions.json    (to brain, account+positions)   |
//|   WRITE: brain_market_tick.json  (to brain, lightweight live px) |
//|   WRITE: brain_broker_deals.json (to brain, broker-truth closes) |
//|   WRITE: brain_ea_heartbeat.json (to brain, EA alive+status)     |
//|                                                                   |
//| Magic: 99999 only labels brain-opened trades.                     |
//| But EA MANAGES ALL positions on the symbol (manual opens, legacy, |
//| brain) — master mode. Filter can be toggled via ManageAllPositions|
//| DD protection: auto-close ALL managed positions if DD >= 3.5%     |
//|                                                                   |
//| Actions supported:                                                |
//|   MARKET            — open market order (BUY/SELL)               |
//|   CLOSE_TICKET      — close specific ticket                      |
//|   CLOSE_ALL        — close all managed positions on symbol       |
//|   MODIFY_SL         — set SL on ticket                           |
//|   MODIFY_TP         — set TP on ticket                           |
//|   MODIFY_ALL_SL     — set SL on all brain tickets                |
//|   MODIFY_ALL_TP     — set TP on all brain tickets                |
//|   MOVE_SL_ENTRY     — move SL to weighted entry (breakeven)      |
//|   PARTIAL_CLOSE_PCT — close X% of a ticket's volume              |
//|   TRAIL_SL          — set trailing SL (distance in USD)          |
//|   NO_ACTION         — no-op                                       |
//+------------------------------------------------------------------+
#property copyright "Claude Brain v3"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

// ─── INPUTS ──────────────────────────────────────────────────────
input int      UpdateInterval        = 3;       // sec between JSON writes
input int      LiveTickIntervalMs    = 200;     // lightweight live price snapshot
input int      BrokerDealsLookbackDays = 7;     // how far back to export MT5 deals
input int      BrokerDealsMax        = 200;     // max broker close-deals to keep
input int      DefaultSlippage       = 30;
input long     BrainMagic            = 99999;   // magic for brain-opened trades
input double   DDAutoCloseAtPct      = 3.5;     // auto-close ALL at this DD %
input bool     EnableDDAutoClose     = true;
input int      OrdersFilePollMs      = 500;     // poll brain_orders.json every X ms
input string   CommentTag            = "BRAIN"; // prefix for brain-opened trades
input bool     ManageAllPositions    = true;    // true = master mode (see+act on ALL positions on symbol)
                                                // false = only brain-opened (magic=99999)

// ─── GLOBAL ──────────────────────────────────────────────────────
CTrade trade;
datetime last_heartbeat_write = 0;
datetime last_positions_write = 0;
ulong last_live_tick_write_ms = 0;
string ORDERS_FILE    = "brain_orders.json";
string POSITIONS_FILE = "brain_positions.json";
string LIVE_TICK_FILE = "brain_market_tick.json";
string DEALS_FILE     = "brain_broker_deals.json";
string HB_FILE        = "brain_ea_heartbeat.json";
string LAST_ORDER_HASH = "";  // dedup: don't process same order twice

// Trailing state per ticket (map-like arrays)
ulong  trail_tickets[];
double trail_distances[];
double trail_anchors[];

// ─── HELPERS ─────────────────────────────────────────────────────

string ExtractJSONString(const string src, const string key)
{
   string pat1 = "\"" + key + "\":\"";
   int p = StringFind(src, pat1);
   if(p < 0) return "";
   p += StringLen(pat1);
   int e = StringFind(src, "\"", p);
   if(e < 0) return "";
   return StringSubstr(src, p, e - p);
}

double ExtractJSONNumber(const string src, const string key)
{
   string pat = "\"" + key + "\":";
   int p = StringFind(src, pat);
   if(p < 0) return 0;
   p += StringLen(pat);
   // Skip whitespace
   while(p < StringLen(src) && (StringGetCharacter(src,p)==' ')) p++;
   // Read number
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

// ─── ACCOUNT + POSITIONS ────────────────────────────────────────

double GetAccountDDPct()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   if(balance <= 0) return 0;
   double dd = MathMax(0, balance - equity);
   return (dd / balance) * 100.0;
}

// Returns true if this position is managed by Brain (either brain-opened or master mode)
bool IsManagedPosition()
{
   // Must be on our chart's symbol
   if(PositionGetString(POSITION_SYMBOL) != _Symbol) return false;
   // Master mode: manage everything on the symbol
   if(ManageAllPositions) return true;
   // Strict mode: only brain magic
   return (long)PositionGetInteger(POSITION_MAGIC) == BrainMagic;
}

int CountManagedPositions()
{
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionSelectByTicket(tk) && IsManagedPosition()) cnt++;
   }
   return cnt;
}

bool SelectRecentHistory()
{
   datetime now = TimeCurrent();
   int days = MathMax(1, BrokerDealsLookbackDays);
   datetime from = now - (datetime)(days * 86400);
   if(from < 0) from = 0;
   return HistorySelect(from, now);
}

bool IsManagedDeal(const ulong deal_ticket)
{
   if(deal_ticket == 0) return false;
   string deal_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
   if(deal_symbol != _Symbol) return false;
   if(ManageAllPositions) return true;
   return (long)HistoryDealGetInteger(deal_ticket, DEAL_MAGIC) == BrainMagic;
}

double DealNetProfit(const ulong deal_ticket)
{
   return HistoryDealGetDouble(deal_ticket, DEAL_PROFIT)
        + HistoryDealGetDouble(deal_ticket, DEAL_SWAP)
        + HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION)
        + HistoryDealGetDouble(deal_ticket, DEAL_FEE);
}

string DealEntryToString(const long entry)
{
   if(entry == DEAL_ENTRY_IN) return "IN";
   if(entry == DEAL_ENTRY_OUT) return "OUT";
   if(entry == DEAL_ENTRY_INOUT) return "INOUT";
   if(entry == DEAL_ENTRY_OUT_BY) return "OUT_BY";
   return "UNKNOWN";
}

void PositionAccruedCosts(const long position_id, double &commission, double &fee)
{
   commission = 0.0;
   fee = 0.0;
   if(position_id <= 0) return;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if(!IsManagedDeal(deal)) continue;
      if((long)HistoryDealGetInteger(deal, DEAL_POSITION_ID) != position_id) continue;
      commission += HistoryDealGetDouble(deal, DEAL_COMMISSION);
      fee += HistoryDealGetDouble(deal, DEAL_FEE);
   }
}

void WritePositionsFile()
{
   bool history_ready = SelectRecentHistory();
   string json = "{\n";
   json += "  \"ts\":" + IntegerToString(TimeCurrent()) + ",\n";
   json += "  \"account\":{\n";
   json += "    \"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2) + ",\n";
   json += "    \"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2) + ",\n";
   json += "    \"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN),2) + ",\n";
   json += "    \"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE),2) + ",\n";
   json += "    \"dd_pct\":" + DoubleToString(GetAccountDDPct(),2) + "\n";
   json += "  },\n";
   json += "  \"positions\":[\n";

   bool first = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(!IsManagedPosition()) continue;

      if(!first) json += ",\n";
      first = false;

      string type = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL";
      long position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
      double gross_profit = PositionGetDouble(POSITION_PROFIT);
      double swap = PositionGetDouble(POSITION_SWAP);
      double commission = 0.0;
      double fee = 0.0;
      if(history_ready)
         PositionAccruedCosts(position_id, commission, fee);
      double net_profit = gross_profit + swap + commission + fee;
      json += "    {";
      json += "\"ticket\":" + IntegerToString((long)tk) + ",";
      json += "\"position_id\":" + IntegerToString(position_id) + ",";
      json += "\"type\":\"" + type + "\",";
      json += "\"volume\":" + DoubleToString(PositionGetDouble(POSITION_VOLUME),2) + ",";
      json += "\"price_open\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN),2) + ",";
      json += "\"price_current\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT),2) + ",";
      json += "\"sl\":" + DoubleToString(PositionGetDouble(POSITION_SL),2) + ",";
      json += "\"tp\":" + DoubleToString(PositionGetDouble(POSITION_TP),2) + ",";
      json += "\"profit\":" + DoubleToString(gross_profit,2) + ",";
      json += "\"profit_gross\":" + DoubleToString(gross_profit,2) + ",";
      json += "\"swap\":" + DoubleToString(swap,2) + ",";
      json += "\"commission\":" + DoubleToString(commission,2) + ",";
      json += "\"fee\":" + DoubleToString(fee,2) + ",";
      json += "\"profit_net\":" + DoubleToString(net_profit,2) + ",";
      json += "\"magic\":" + IntegerToString((long)PositionGetInteger(POSITION_MAGIC)) + ",";
      json += "\"time\":" + IntegerToString((long)PositionGetInteger(POSITION_TIME)) + ",";
      json += "\"comment\":\"" + PositionGetString(POSITION_COMMENT) + "\"";
      json += "}";
   }

   json += "\n  ]\n}";

   int h = FileOpen(POSITIONS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

void WriteLiveTickFile()
{
   bool history_ready = SelectRecentHistory();
   MqlTick tick;
   bool has_tick = SymbolInfoTick(_Symbol, tick);
   double bid = has_tick ? tick.bid : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = has_tick ? tick.ask : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double last = has_tick ? tick.last : 0.0;
   double spread = (bid > 0 && ask > 0) ? (ask - bid) : 0.0;

   string json = "{";
   json += "\"ts\":" + IntegerToString(TimeCurrent()) + ",";
   json += "\"ts_ms\":" + IntegerToString((long)GetTickCount()) + ",";
   json += "\"symbol\":\"" + _Symbol + "\",";
   json += "\"bid\":" + DoubleToString(bid, _Digits) + ",";
   json += "\"ask\":" + DoubleToString(ask, _Digits) + ",";
   json += "\"last\":" + DoubleToString(last, _Digits) + ",";
   json += "\"spread\":" + DoubleToString(spread, _Digits) + ",";
   json += "\"positions\":[";

   bool first = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(!IsManagedPosition()) continue;

      if(!first) json += ",";
      first = false;

      long position_id = (long)PositionGetInteger(POSITION_IDENTIFIER);
      double gross_profit = PositionGetDouble(POSITION_PROFIT);
      double swap = PositionGetDouble(POSITION_SWAP);
      double commission = 0.0;
      double fee = 0.0;
      if(history_ready)
         PositionAccruedCosts(position_id, commission, fee);
      double net_profit = gross_profit + swap + commission + fee;

      json += "{";
      json += "\"ticket\":" + IntegerToString((long)tk) + ",";
      json += "\"position_id\":" + IntegerToString(position_id) + ",";
      json += "\"type\":\"" + ((PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL") + "\",";
      json += "\"price_current\":" + DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), _Digits) + ",";
      json += "\"profit\":" + DoubleToString(gross_profit, 2) + ",";
      json += "\"profit_gross\":" + DoubleToString(gross_profit, 2) + ",";
      json += "\"swap\":" + DoubleToString(swap, 2) + ",";
      json += "\"commission\":" + DoubleToString(commission, 2) + ",";
      json += "\"fee\":" + DoubleToString(fee, 2) + ",";
      json += "\"profit_net\":" + DoubleToString(net_profit, 2);
      json += "}";
   }

   json += "]";
   json += "}";

   int h = FileOpen(LIVE_TICK_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

void WriteBrokerDealsFile()
{
   if(!SelectRecentHistory()) return;

   ulong matching[];
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0) continue;
      if(!IsManagedDeal(deal)) continue;

      long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT)
         continue;

      int n = ArraySize(matching);
      ArrayResize(matching, n + 1);
      matching[n] = deal;
   }

   string json = "{\n";
   json += "  \"ts\":" + IntegerToString(TimeCurrent()) + ",\n";
   json += "  \"symbol\":\"" + _Symbol + "\",\n";
   json += "  \"deals\":[\n";

   int start = MathMax(0, ArraySize(matching) - BrokerDealsMax);
   bool first = true;
   for(int i = start; i < ArraySize(matching); i++)
   {
      ulong deal = matching[i];
      long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
      double profit = HistoryDealGetDouble(deal, DEAL_PROFIT);
      double swap = HistoryDealGetDouble(deal, DEAL_SWAP);
      double commission = HistoryDealGetDouble(deal, DEAL_COMMISSION);
      double fee = HistoryDealGetDouble(deal, DEAL_FEE);

      if(!first) json += ",\n";
      first = false;

      json += "    {";
      json += "\"deal\":" + IntegerToString((long)deal) + ",";
      json += "\"order\":" + IntegerToString((long)HistoryDealGetInteger(deal, DEAL_ORDER)) + ",";
      json += "\"position_id\":" + IntegerToString((long)HistoryDealGetInteger(deal, DEAL_POSITION_ID)) + ",";
      json += "\"entry\":\"" + DealEntryToString(entry) + "\",";
      json += "\"type\":\"" + ((HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_BUY) ? "BUY" : "SELL") + "\",";
      json += "\"volume\":" + DoubleToString(HistoryDealGetDouble(deal, DEAL_VOLUME), 2) + ",";
      json += "\"price\":" + DoubleToString(HistoryDealGetDouble(deal, DEAL_PRICE), _Digits) + ",";
      json += "\"profit\":" + DoubleToString(profit, 2) + ",";
      json += "\"swap\":" + DoubleToString(swap, 2) + ",";
      json += "\"commission\":" + DoubleToString(commission, 2) + ",";
      json += "\"fee\":" + DoubleToString(fee, 2) + ",";
      json += "\"net\":" + DoubleToString(DealNetProfit(deal), 2) + ",";
      json += "\"magic\":" + IntegerToString((long)HistoryDealGetInteger(deal, DEAL_MAGIC)) + ",";
      json += "\"time\":" + IntegerToString((long)HistoryDealGetInteger(deal, DEAL_TIME)) + ",";
      json += "\"comment\":\"" + HistoryDealGetString(deal, DEAL_COMMENT) + "\"";
      json += "}";
   }

   json += "\n  ]\n}";

   int h = FileOpen(DEALS_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

void WriteHeartbeat(const string status)
{
   string json = "{";
   json += "\"ts\":" + IntegerToString(TimeCurrent()) + ",";
   json += "\"status\":\"" + status + "\",";
   json += "\"brain_magic\":" + IntegerToString(BrainMagic) + ",";
   json += "\"dd_limit_pct\":" + DoubleToString(DDAutoCloseAtPct,1) + ",";
   json += "\"dd_current_pct\":" + DoubleToString(GetAccountDDPct(),2) + ",";
   json += "\"positions_count\":" + IntegerToString(CountManagedPositions()) + ",";
   json += "\"manage_all\":" + (ManageAllPositions ? "true" : "false") + ",";
   json += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE),2) + ",";
   json += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2);
   json += "}";

   int h = FileOpen(HB_FILE, FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}

// ─── ORDER ACTIONS ──────────────────────────────────────────────

bool ActionMarket(const string type, const double lot, const double sl, const double tp, const string comment)
{
   string sym = _Symbol;
   trade.SetExpertMagicNumber(BrainMagic);
   trade.SetDeviationInPoints(DefaultSlippage);

   bool ok = false;
   if(type == "BUY")
      ok = trade.Buy(lot, sym, 0, sl, tp, CommentTag + "_" + comment);
   else if(type == "SELL")
      ok = trade.Sell(lot, sym, 0, sl, tp, CommentTag + "_" + comment);

   if(ok)
      Print("BRAIN MARKET ", type, " ", DoubleToString(lot,2), " OK ticket=", trade.ResultOrder());
   else
      Print("BRAIN MARKET ", type, " FAILED: ", trade.ResultRetcode(), " ", trade.ResultComment());
   return ok;
}

bool ActionCloseTicket(const long ticket)
{
   trade.SetExpertMagicNumber(BrainMagic);
   if(!PositionSelectByTicket((ulong)ticket))
   {
      Print("CLOSE_TICKET: ticket ", ticket, " not found");
      return false;
   }
   if(!IsManagedPosition())
   {
      Print("CLOSE_TICKET: ticket ", ticket, " not managed by this EA (wrong symbol or magic)");
      return false;
   }
   bool ok = trade.PositionClose((ulong)ticket);
   Print("CLOSE_TICKET ", ticket, " ", ok ? "OK" : "FAIL");
   return ok;
}

int ActionCloseAll()
{
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(!IsManagedPosition()) continue;
      if(trade.PositionClose(tk)) closed++;
   }
   Print("CLOSE_ALL closed ", closed, " managed positions");
   return closed;
}

bool ActionModifySL(const long ticket, const double sl)
{
   if(!PositionSelectByTicket((ulong)ticket)) return false;
   if(!IsManagedPosition()) return false;
   double tp = PositionGetDouble(POSITION_TP);
   return trade.PositionModify((ulong)ticket, sl, tp);
}

bool ActionModifyTP(const long ticket, const double tp)
{
   if(!PositionSelectByTicket((ulong)ticket)) return false;
   if(!IsManagedPosition()) return false;
   double sl = PositionGetDouble(POSITION_SL);
   return trade.PositionModify((ulong)ticket, sl, tp);
}

int ActionModifyAllSL(const double sl)
{
   int mod = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(!IsManagedPosition()) continue;
      double tp = PositionGetDouble(POSITION_TP);
      if(trade.PositionModify(tk, sl, tp)) mod++;
   }
   return mod;
}

int ActionModifyAllTP(const double tp)
{
   int mod = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(!PositionSelectByTicket(tk)) continue;
      if(!IsManagedPosition()) continue;
      double sl = PositionGetDouble(POSITION_SL);
      if(trade.PositionModify(tk, sl, tp)) mod++;
   }
   return mod;
}

int ActionMoveSLEntry()
{
   // Move SL of ALL brain positions to their weighted entry
   double totalVol = 0, weighted = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || !PositionSelectByTicket(tk)) continue;
      if(!IsManagedPosition()) continue;
      double v = PositionGetDouble(POSITION_VOLUME);
      double p = PositionGetDouble(POSITION_PRICE_OPEN);
      totalVol += v;
      weighted += p * v;
   }
   if(totalVol <= 0) return 0;
   double entry = weighted / totalVol;
   return ActionModifyAllSL(entry);
}

bool ActionPartialClosePct(const long ticket, const double pct)
{
   if(!PositionSelectByTicket((ulong)ticket)) return false;
   if(!IsManagedPosition()) return false;
   double vol = PositionGetDouble(POSITION_VOLUME);
   double close_vol = MathMax(0.01, NormalizeDouble(vol * pct / 100.0, 2));
   if(close_vol >= vol) close_vol = vol;  // full close if pct >= 100
   return trade.PositionClosePartial((ulong)ticket, close_vol);
}

void ActionTrailSL(const long ticket, const double distance_usd)
{
   // Register trailing — actual trailing done in OnTick
   for(int i = 0; i < ArraySize(trail_tickets); i++)
   {
      if(trail_tickets[i] == (ulong)ticket)
      {
         trail_distances[i] = distance_usd;
         trail_anchors[i]   = 0;  // reset anchor
         return;
      }
   }
   int n = ArraySize(trail_tickets);
   ArrayResize(trail_tickets, n+1);
   ArrayResize(trail_distances, n+1);
   ArrayResize(trail_anchors, n+1);
   trail_tickets[n]   = (ulong)ticket;
   trail_distances[n] = distance_usd;
   trail_anchors[n]   = 0;
}

void UpdateTrailingSL()
{
   for(int i = ArraySize(trail_tickets) - 1; i >= 0; i--)
   {
      ulong tk = trail_tickets[i];
      if(!PositionSelectByTicket(tk))
      {
         // Ticket gone — remove from trailing list
         for(int j = i; j < ArraySize(trail_tickets)-1; j++)
         {
            trail_tickets[j]   = trail_tickets[j+1];
            trail_distances[j] = trail_distances[j+1];
            trail_anchors[j]   = trail_anchors[j+1];
         }
         ArrayResize(trail_tickets, ArraySize(trail_tickets)-1);
         ArrayResize(trail_distances, ArraySize(trail_distances)-1);
         ArrayResize(trail_anchors, ArraySize(trail_anchors)-1);
         continue;
      }

      double dist = trail_distances[i];
      double type = (double)PositionGetInteger(POSITION_TYPE);
      double priceNow = PositionGetDouble(POSITION_PRICE_CURRENT);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);

      if(type == POSITION_TYPE_BUY)
      {
         if(priceNow > trail_anchors[i]) trail_anchors[i] = priceNow;
         double newSL = trail_anchors[i] - dist;
         if(newSL > sl) trade.PositionModify(tk, newSL, tp);
      }
      else // SELL
      {
         if(trail_anchors[i] == 0 || priceNow < trail_anchors[i]) trail_anchors[i] = priceNow;
         double newSL = trail_anchors[i] + dist;
         if(sl == 0 || newSL < sl) trade.PositionModify(tk, newSL, tp);
      }
   }
}

// ─── DD AUTO-CLOSE ──────────────────────────────────────────────

void CheckDDAutoClose()
{
   if(!EnableDDAutoClose) return;
   double dd = GetAccountDDPct();
   if(dd >= DDAutoCloseAtPct)
   {
      Print("🚨 DD AUTO-CLOSE TRIGGERED: DD=", DoubleToString(dd,2), "% >= ", DoubleToString(DDAutoCloseAtPct,1), "%");
      ActionCloseAll();
   }
}

// ─── ORDER PROCESSOR ────────────────────────────────────────────

void ProcessOrders()
{
   string content = ReadOrdersFile();
   if(content == "") return;
   if(StringFind(content, "PROCESSED") >= 0) return;  // already done
   if(StringFind(content, "\"action\"") < 0 && StringFind(content, "\"orders\"") < 0) return;

   // Simple hash to dedup: just length + first 20 chars
   string hash = IntegerToString(StringLen(content)) + "_" + StringSubstr(content, 0, 20);
   if(hash == LAST_ORDER_HASH) return;
   LAST_ORDER_HASH = hash;

   // Check if it's the multi-order format: {"orders":[{...},{...}]}
   int ordersPos = StringFind(content, "\"orders\"");
   if(ordersPos >= 0)
   {
      // Find each order block {...}
      int p = ordersPos;
      while(true)
      {
         int start = StringFind(content, "{", p+1);
         if(start < 0) break;
         // Find matching closing brace
         int depth = 1, end = start + 1;
         while(end < StringLen(content) && depth > 0)
         {
            ushort c = StringGetCharacter(content, end);
            if(c == '{') depth++;
            else if(c == '}') depth--;
            end++;
         }
         if(depth != 0) break;
         string orderStr = StringSubstr(content, start, end - start);
         ProcessSingleOrder(orderStr);
         p = end;
      }
   }
   else
   {
      // Single order format
      ProcessSingleOrder(content);
   }

   MarkOrdersProcessed();
}

void ProcessSingleOrder(const string orderStr)
{
   string action = ExtractJSONString(orderStr, "action");
   string type   = ExtractJSONString(orderStr, "type");
   string comment= ExtractJSONString(orderStr, "comment");
   double lot    = ExtractJSONNumber(orderStr, "lot");
   double sl     = ExtractJSONNumber(orderStr, "sl");
   double tp     = ExtractJSONNumber(orderStr, "tp");
   double pct    = ExtractJSONNumber(orderStr, "pct");
   double dist   = ExtractJSONNumber(orderStr, "distance");
   long   ticket = (long)ExtractJSONNumber(orderStr, "ticket");

   if(action == "") return;

   Print("BRAIN ORDER: action=", action, " type=", type, " lot=", lot, " ticket=", ticket);

   if(action == "MARKET" && (type == "BUY" || type == "SELL") && lot > 0)
      ActionMarket(type, lot, sl, tp, comment);

   else if(action == "CLOSE_TICKET" && ticket > 0)
      ActionCloseTicket(ticket);

   else if(action == "CLOSE_ALL_BRAIN" || action == "CLOSE_ALL")
      ActionCloseAll();

   else if(action == "MODIFY_SL" && ticket > 0)
      ActionModifySL(ticket, sl);

   else if(action == "MODIFY_TP" && ticket > 0)
      ActionModifyTP(ticket, tp);

   else if(action == "MODIFY_ALL_SL")
      ActionModifyAllSL(sl);

   else if(action == "MODIFY_ALL_TP")
      ActionModifyAllTP(tp);

   else if(action == "MOVE_SL_ENTRY")
      ActionMoveSLEntry();

   else if(action == "PARTIAL_CLOSE_PCT" && ticket > 0 && pct > 0)
      ActionPartialClosePct(ticket, pct);

   else if(action == "TRAIL_SL" && ticket > 0 && dist > 0)
      ActionTrailSL(ticket, dist);

   else if(action == "NO_ACTION")
      ; // no-op

   else
      Print("UNKNOWN action: ", action);
}

// ─── EVENT HANDLERS ─────────────────────────────────────────────

int OnInit()
{
   Print("═════════════════════════════════════════════");
   Print("Claude Brain EA v1 — starting");
   Print("  Symbol: ", _Symbol);
   Print("  Brain Magic (label): ", BrainMagic);
   Print("  Mode: ", ManageAllPositions ? "MASTER (manage ALL positions on symbol)" : "ISOLATED (only magic=" + IntegerToString(BrainMagic) + ")");
   Print("  DD auto-close at: ", DDAutoCloseAtPct, "%");
   Print("  Positions currently managed: ", CountManagedPositions());
   Print("═════════════════════════════════════════════");

   ArrayResize(trail_tickets, 0);
   ArrayResize(trail_distances, 0);
   ArrayResize(trail_anchors, 0);

   WriteHeartbeat("STARTED");
   WritePositionsFile();
   WriteBrokerDealsFile();
   WriteLiveTickFile();

   int timer_ms = OrdersFilePollMs;
   if(LiveTickIntervalMs > 0 && (timer_ms <= 0 || LiveTickIntervalMs < timer_ms))
      timer_ms = LiveTickIntervalMs;
   if(timer_ms < 50)
      timer_ms = 50;
   EventSetMillisecondTimer(timer_ms);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   WriteHeartbeat("STOPPED");
   Print("Claude Brain EA stopped. Reason: ", reason);
}

void OnTimer()
{
   ulong now_ms = GetTickCount();

   // Poll orders file every OrdersFilePollMs (default 500ms)
   static ulong last_orders_poll_ms = 0;
   if(last_orders_poll_ms == 0 || (now_ms - last_orders_poll_ms) >= (ulong)MathMax(50, OrdersFilePollMs))
   {
      ProcessOrders();
      last_orders_poll_ms = now_ms;
   }

   // Lightweight live price snapshot for the console/watch mode.
   if(LiveTickIntervalMs > 0 && (last_live_tick_write_ms == 0 || (now_ms - last_live_tick_write_ms) >= (ulong)MathMax(50, LiveTickIntervalMs)))
   {
      WriteLiveTickFile();
      last_live_tick_write_ms = now_ms;
   }
}

void OnTick()
{
   datetime now = TimeCurrent();

   // DD auto-close check (every tick)
   CheckDDAutoClose();

   // Trailing SL update
   UpdateTrailingSL();

   if(LiveTickIntervalMs > 0)
   {
      WriteLiveTickFile();
      last_live_tick_write_ms = GetTickCount();
   }

   // Write positions every UpdateInterval seconds
   if(now - last_positions_write >= UpdateInterval)
   {
      WritePositionsFile();
      WriteBrokerDealsFile();
      last_positions_write = now;
   }

   // Write heartbeat every UpdateInterval seconds
   if(now - last_heartbeat_write >= UpdateInterval)
   {
      WriteHeartbeat("RUNNING");
      last_heartbeat_write = now;
   }
}
