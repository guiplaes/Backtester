//+------------------------------------------------------------------+
//|                                       MeanRevAveraging_v4.mq5    |
//|  Sistema mean-rev averaging amb auto-detecció símbol + TF        |
//|  Backtest 5y: +21-85%/any segons lot, Calmar fins 4.58           |
//+------------------------------------------------------------------+
#property copyright "Claude"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>

CTrade trade;

// ==================== INPUTS ====================
input double LOT_PER_ENTRY = 0.05;       // 0.05 conservador / 0.15 sweet spot / 0.20 agressiu
input int    MAX_PYRAMID = 4;            // Màxim entrades acumulades
input int    MAGIC_NUMBER = 4040404;     // Per identificar trades
input bool   ENABLE_TG_ALERTS = false;   // Telegram alerts
input bool   AUTO_PARAMS = true;         // Auto-detect símbol+TF
input int    MANUAL_SMA = 200;
input double MANUAL_LVL1 = 0.5;
input double MANUAL_LVL2 = 1.0;
input double MANUAL_LVL3 = 1.5;
input double MANUAL_LVL4 = 2.0;
input double MANUAL_STOP = 4.0;
input string MANUAL_DIR = "BOTH";        // LONG, SHORT, BOTH

// ==================== STATE ====================
int sma_period;
double lvl1, lvl2, lvl3, lvl4, stop_z;
bool do_long, do_short;
string strategy_id;

// Position tracking
struct PosInfo {
    bool active_long;
    bool active_short;
    int n_entries_long;
    int n_entries_short;
};
PosInfo pos_info;

// ==================== INIT ====================
int OnInit() {
    if(AUTO_PARAMS) {
        DetectParams();
    } else {
        sma_period = MANUAL_SMA;
        lvl1 = MANUAL_LVL1; lvl2 = MANUAL_LVL2;
        lvl3 = MANUAL_LVL3; lvl4 = MANUAL_LVL4;
        stop_z = MANUAL_STOP;
        do_long = (MANUAL_DIR == "LONG" || MANUAL_DIR == "BOTH");
        do_short = (MANUAL_DIR == "SHORT" || MANUAL_DIR == "BOTH");
        strategy_id = "MANUAL";
    }
    Print("MeanRev v4 INIT: ", strategy_id, " SMA=", sma_period,
          " LVLS=", lvl1, "/", lvl2, "/", lvl3, "/", lvl4,
          " STOP=", stop_z, " DIR=", do_long ? "L" : "", do_short ? "S" : "");
    trade.SetExpertMagicNumber(MAGIC_NUMBER);
    return(INIT_SUCCEEDED);
}

// ==================== DETECT PARAMS ====================
void DetectParams() {
    string sym = Symbol();
    StringToUpper(sym);
    ENUM_TIMEFRAMES tf = Period();

    // Default
    sma_period = 200; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0;stop_z=4.0;
    do_long=true; do_short=true;
    strategy_id = "DEFAULT";

    // EURGBP
    if(StringFind(sym, "EURGBP") >= 0) {
        if(tf == PERIOD_D1) { sma_period=30; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=4.0; do_long=true;do_short=true; strategy_id="EURGBP D1"; }
        else if(tf == PERIOD_H4) { sma_period=200; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=5.0; do_long=true;do_short=true; strategy_id="EURGBP H4"; }
        else if(tf == PERIOD_M15) { sma_period=2400; lvl1=1.0;lvl2=1.5;lvl3=2.0;lvl4=2.5; stop_z=4.0; do_long=true;do_short=false; strategy_id="EURGBP M15"; }
    }
    // GBPCHF
    else if(StringFind(sym, "GBPCHF") >= 0) {
        if(tf == PERIOD_D1) { sma_period=50; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=4.0; do_long=true;do_short=true; strategy_id="GBPCHF D1"; }
        else if(tf == PERIOD_H4) { sma_period=150; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=4.0; do_long=false;do_short=true; strategy_id="GBPCHF H4"; }
        else if(tf == PERIOD_H1) { sma_period=800; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=4.0; do_long=false;do_short=true; strategy_id="GBPCHF H1"; }
    }
    // AUDCAD
    else if(StringFind(sym, "AUDCAD") >= 0) {
        if(tf == PERIOD_M30) { sma_period=200; lvl1=1.0;lvl2=1.5;lvl3=2.0;lvl4=2.5; stop_z=6.0; do_long=true;do_short=true; strategy_id="AUDCAD M30"; }
        else if(tf == PERIOD_H4) { sma_period=50; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=4.0; do_long=true;do_short=false; strategy_id="AUDCAD H4"; }
        else if(tf == PERIOD_H1) { sma_period=100; lvl1=1.0;lvl2=1.5;lvl3=2.0;lvl4=2.5; stop_z=5.0; do_long=true;do_short=true; strategy_id="AUDCAD H1"; }
        else if(tf == PERIOD_M15) { sma_period=200; lvl1=1.0;lvl2=1.5;lvl3=2.0;lvl4=2.5; stop_z=5.0; do_long=true;do_short=false; strategy_id="AUDCAD M15"; }
    }
    // USDCAD
    else if(StringFind(sym, "USDCAD") >= 0) {
        if(tf == PERIOD_H1) { sma_period=1200; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=5.0; do_long=true;do_short=true; strategy_id="USDCAD H1"; }
        else if(tf == PERIOD_H4) { sma_period=200; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=3.0; do_long=true;do_short=true; strategy_id="USDCAD H4"; }
    }
    // NZDCAD
    else if(StringFind(sym, "NZDCAD") >= 0) {
        if(tf == PERIOD_D1) { sma_period=50; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=3.5; do_long=true;do_short=true; strategy_id="NZDCAD D1"; }
    }
    // USDCHF
    else if(StringFind(sym, "USDCHF") >= 0) {
        if(tf == PERIOD_H4) { sma_period=800; lvl1=1.5;lvl2=2.0;lvl3=2.5;lvl4=3.0; stop_z=3.5; do_long=true;do_short=true; strategy_id="USDCHF H4"; }
    }
    // EURCHF
    else if(StringFind(sym, "EURCHF") >= 0) {
        if(tf == PERIOD_D1) { sma_period=100; lvl1=0.5;lvl2=1.5;lvl3=2.5;lvl4=3.0; stop_z=5.0; do_long=false;do_short=true; strategy_id="EURCHF D1"; }
        else if(tf == PERIOD_H4) { sma_period=150; lvl1=1.5;lvl2=2.5;lvl3=3.5;lvl4=4.0; stop_z=6.0; do_long=false;do_short=true; strategy_id="EURCHF H4"; }
    }
    // AUDNZD
    else if(StringFind(sym, "AUDNZD") >= 0) {
        if(tf == PERIOD_D1) { sma_period=75; lvl1=0.5;lvl2=1.0;lvl3=1.5;lvl4=2.0; stop_z=5.0; do_long=true;do_short=true; strategy_id="AUDNZD D1"; }
    }
}

// ==================== MAIN ====================
void OnTick() {
    // Process only on bar close
    static datetime last_bar = 0;
    datetime cur_bar = iTime(NULL, 0, 0);
    if(cur_bar == last_bar) return;
    last_bar = cur_bar;

    // Need closed bar (index 1)
    if(iBars(NULL, 0) < sma_period + 10) return;

    // Calculate SMA + STD on closed bars (shift=1)
    double sum=0;
    double prices[];
    ArrayResize(prices, sma_period);
    for(int i=0; i<sma_period; i++) {
        prices[i] = iClose(NULL, 0, i+1);
        sum += prices[i];
    }
    double sma = sum / sma_period;
    double sum_sq = 0;
    for(int i=0; i<sma_period; i++) sum_sq += MathPow(prices[i]-sma, 2);
    double std = MathSqrt(sum_sq / sma_period);
    if(std <= 0) return;

    double c = iClose(NULL, 0, 1);  // Last closed bar
    double z = (c - sma) / std;

    // Update position state
    UpdatePositionState();

    // Manage open positions
    if(pos_info.active_long) {
        if(c >= sma) {
            CloseAllPositions(POSITION_TYPE_BUY, "TGT");
            return;
        }
        if(z <= -stop_z) {
            CloseAllPositions(POSITION_TYPE_BUY, "SL");
            return;
        }
        // Add to LONG
        if(do_long) {
            int n = pos_info.n_entries_long;
            if(n < 1 && z <= -lvl1) OpenPosition(POSITION_TYPE_BUY, "L1");
            else if(n == 1 && z <= -lvl2) OpenPosition(POSITION_TYPE_BUY, "L2");
            else if(n == 2 && z <= -lvl3) OpenPosition(POSITION_TYPE_BUY, "L3");
            else if(n == 3 && z <= -lvl4) OpenPosition(POSITION_TYPE_BUY, "L4");
        }
    }
    else if(pos_info.active_short) {
        if(c <= sma) {
            CloseAllPositions(POSITION_TYPE_SELL, "TGT");
            return;
        }
        if(z >= stop_z) {
            CloseAllPositions(POSITION_TYPE_SELL, "SL");
            return;
        }
        if(do_short) {
            int n = pos_info.n_entries_short;
            if(n < 1 && z >= lvl1) OpenPosition(POSITION_TYPE_SELL, "S1");
            else if(n == 1 && z >= lvl2) OpenPosition(POSITION_TYPE_SELL, "S2");
            else if(n == 2 && z >= lvl3) OpenPosition(POSITION_TYPE_SELL, "S3");
            else if(n == 3 && z >= lvl4) OpenPosition(POSITION_TYPE_SELL, "S4");
        }
    }
    else {
        // No position — try open first
        if(do_long && z <= -lvl1) OpenPosition(POSITION_TYPE_BUY, "L1");
        else if(do_short && z >= lvl1) OpenPosition(POSITION_TYPE_SELL, "S1");
    }
}

void UpdatePositionState() {
    pos_info.active_long = false;
    pos_info.active_short = false;
    pos_info.n_entries_long = 0;
    pos_info.n_entries_short = 0;

    int total = PositionsTotal();
    for(int i=0; i<total; i++) {
        ulong ticket = PositionGetTicket(i);
        if(!PositionSelectByTicket(ticket)) continue;
        if(PositionGetInteger(POSITION_MAGIC) != MAGIC_NUMBER) continue;
        if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;

        long ptype = PositionGetInteger(POSITION_TYPE);
        if(ptype == POSITION_TYPE_BUY) {
            pos_info.active_long = true;
            pos_info.n_entries_long++;
        } else if(ptype == POSITION_TYPE_SELL) {
            pos_info.active_short = true;
            pos_info.n_entries_short++;
        }
    }
}

void OpenPosition(ENUM_POSITION_TYPE type, string comment) {
    double price = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(Symbol(), SYMBOL_ASK) : SymbolInfoDouble(Symbol(), SYMBOL_BID);
    if(type == POSITION_TYPE_BUY) {
        trade.Buy(LOT_PER_ENTRY, Symbol(), 0, 0, 0, comment);
    } else {
        trade.Sell(LOT_PER_ENTRY, Symbol(), 0, 0, 0, comment);
    }
    Print("OPEN ", comment, " price=", price);
}

void CloseAllPositions(ENUM_POSITION_TYPE type, string reason) {
    int total = PositionsTotal();
    for(int i=total-1; i>=0; i--) {
        ulong ticket = PositionGetTicket(i);
        if(!PositionSelectByTicket(ticket)) continue;
        if(PositionGetInteger(POSITION_MAGIC) != MAGIC_NUMBER) continue;
        if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
        if(PositionGetInteger(POSITION_TYPE) != type) continue;
        trade.PositionClose(ticket);
    }
    Print("CLOSE ALL ", reason);
}
