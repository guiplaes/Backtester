//+------------------------------------------------------------------+
//|           DFMO_DualFrameMomentumOscillator.mq5                   |
//|           Dual-Frame Momentum Oscillator for MT5                 |
//|           Based on concept by rick84etter (TradingView)          |
//|           v3.00 — Clean: StochK + RSI + Zone END arrows          |
//|           Ported from MQL4 to MQL5                               |
//+------------------------------------------------------------------+
#property copyright "Adapted for MT5"
#property link      ""
#property version   "3.00"

#property indicator_separate_window
#property indicator_minimum  0
#property indicator_maximum  100
#property indicator_buffers  5
#property indicator_plots    3

//--- Plot 0: Slow Stoch %K
#property indicator_label1   "Slow Stoch %K"
#property indicator_type1    DRAW_LINE
#property indicator_color1   clrDodgerBlue
#property indicator_style1   STYLE_SOLID
#property indicator_width1   2

//--- Plot 1: Slow Stoch %D
#property indicator_label2   "Slow Stoch %D"
#property indicator_type2    DRAW_LINE
#property indicator_color2   clrOrange
#property indicator_style2   STYLE_DASH
#property indicator_width2   1

//--- Plot 2: Fast RSI
#property indicator_label3   "Fast RSI"
#property indicator_type3    DRAW_LINE
#property indicator_color3   clrMagenta
#property indicator_style3   STYLE_SOLID
#property indicator_width3   2

//--- Levels
#property indicator_level1      80
#property indicator_level2      50
#property indicator_level3      20
#property indicator_levelcolor  clrGray
#property indicator_levelstyle  STYLE_DOT

//+------------------------------------------------------------------+
//| Input Parameters                                                  |
//+------------------------------------------------------------------+
input string   sep1           = "=== Context: Slow Stochastic ==="; // ----
input int      StochKPeriod   = 25;    // Stochastic %K Period
input int      StochSmoothing = 4;     // Stochastic %K Smoothing
input int      StochDPeriod   = 4;     // Stochastic %D Period
input string   sep2           = "=== Trigger: Fast RSI ==="; // ----
input int      RSIPeriod      = 3;     // RSI Period
input string   sep3           = "=== Levels ==="; // ----
input int      OverboughtLvl  = 80;    // Overbought Level
input int      OversoldLvl    = 20;    // Oversold Level
input string   sep4           = "=== Confluence Engine ==="; // ----
input bool     ShowConfluence = true;  // Show Confluence Background
input color    OBConfColor    = clrTomato;     // Overbought Confluence Color
input color    OSConfColor    = clrLimeGreen;  // Oversold Confluence Color

input string   sep8           = "=== Zone END Arrows ==="; // ----
input bool     SIG_Show       = true;   // Show Zone END arrows on chart
input color    SIG_SellColor  = clrRed;        // Sell (OB zone end) arrow color
input color    SIG_BuyColor   = clrDodgerBlue; // Buy (OS zone end) arrow color

input string   sep5           = "=== Alerts ==="; // ----
input bool     AlertOnConfluence = false;  // Alert on Confluence
input bool     AlertOnCross     = false;  // Alert on Stoch K/D Cross

//+------------------------------------------------------------------+
//| Indicator Buffers                                                  |
//+------------------------------------------------------------------+
double SlowStochK[];    // Buffer 0: Slow Stochastic %K (smoothed)
double SlowStochD[];    // Buffer 1: Slow Stochastic %D
double FastRSI[];       // Buffer 2: Fast RSI
double ConfluenceOB[];  // Buffer 3: Overbought confluence (internal)
double ConfluenceOS[];  // Buffer 4: Oversold confluence (internal)

//--- Alert tracking
datetime lastAlertTime = 0;
int      lastConfState = 0;
int      lastCrossState = 0;

//--- Window index
string dfmoShortName = "";
int    dfmoWindowIdx = -1;

//--- Indicator handles
int    hRSI = INVALID_HANDLE;
int    hATR = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Custom indicator initialization function                          |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StochKPeriod < 1 || StochSmoothing < 1 || StochDPeriod < 1 || RSIPeriod < 1)
   { Print("DFMO Error: All periods must be >= 1"); return(INIT_PARAMETERS_INCORRECT); }
   if(OverboughtLvl <= OversoldLvl)
   { Print("DFMO Error: OB must be > OS"); return(INIT_PARAMETERS_INCORRECT); }

   //--- Map buffers: 3 plots + 2 internal calculation buffers
   SetIndexBuffer(0, SlowStochK, INDICATOR_DATA);
   SetIndexBuffer(1, SlowStochD, INDICATOR_DATA);
   SetIndexBuffer(2, FastRSI,    INDICATOR_DATA);
   SetIndexBuffer(3, ConfluenceOB, INDICATOR_CALCULATIONS);
   SetIndexBuffer(4, ConfluenceOS, INDICATOR_CALCULATIONS);

   //--- Set labels
   PlotIndexSetString(0, PLOT_LABEL, "Slow Stoch %K (" + IntegerToString(StochKPeriod) + ")");
   PlotIndexSetString(1, PLOT_LABEL, "Slow Stoch %D (" + IntegerToString(StochDPeriod) + ")");
   PlotIndexSetString(2, PLOT_LABEL, "Fast RSI (" + IntegerToString(RSIPeriod) + ")");

   //--- Set empty values
   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   //--- Build short name
   dfmoShortName = "DFMO (" + IntegerToString(StochKPeriod) + ","
                   + IntegerToString(StochSmoothing) + ","
                   + IntegerToString(StochDPeriod) + " | RSI "
                   + IntegerToString(RSIPeriod) + ")";
   IndicatorSetString(INDICATOR_SHORTNAME, dfmoShortName);

   //--- Create indicator handles
   hRSI = iRSI(NULL, 0, RSIPeriod, PRICE_CLOSE);
   if(hRSI == INVALID_HANDLE)
   { Print("DFMO Error: Failed to create RSI handle"); return(INIT_FAILED); }

   hATR = iATR(NULL, 0, 14);
   if(hATR == INVALID_HANDLE)
   { Print("DFMO Error: Failed to create ATR handle"); return(INIT_FAILED); }

   //--- Get window index
   dfmoWindowIdx = ChartWindowFind(0, dfmoShortName);

   Print("DFMO v3.00 MQL5 Init: ShortName=", dfmoShortName, " WindowIdx=", dfmoWindowIdx,
         " ShowConf=", ShowConfluence, " SIG=", SIG_Show,
         " Stoch(", StochKPeriod, ",", StochSmoothing, ",", StochDPeriod, ")",
         " RSI(", RSIPeriod, ") OB=", OverboughtLvl, " OS=", OversoldLvl);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, "DFMO_BG_");
   ObjectsDeleteAll(0, "DFMO_SIG_");
   Comment("");
   if(hRSI != INVALID_HANDLE) { IndicatorRelease(hRSI); hRSI = INVALID_HANDLE; }
   if(hATR != INVALID_HANDLE) { IndicatorRelease(hATR); hATR = INVALID_HANDLE; }
}

//+------------------------------------------------------------------+
double CalcRawStochK(int shift, const double &high[], const double &low[],
                     const double &close[], int rates_total)
{
   double highestHigh = -DBL_MAX;
   double lowestLow   = DBL_MAX;

   for(int j = 0; j < StochKPeriod; j++)
   {
      int idx = shift + j;
      if(idx >= rates_total) break;
      if(high[idx] > highestHigh) highestHigh = high[idx];
      if(low[idx]  < lowestLow)   lowestLow   = low[idx];
   }

   double range = highestHigh - lowestLow;
   if(range < _Point * 0.5) return(50.0);
   return(((close[shift] - lowestLow) / range) * 100.0);
}

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   ArraySetAsSeries(time, true);
   ArraySetAsSeries(open, true);
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);
   ArraySetAsSeries(SlowStochK, true);
   ArraySetAsSeries(SlowStochD, true);
   ArraySetAsSeries(FastRSI, true);
   ArraySetAsSeries(ConfluenceOB, true);
   ArraySetAsSeries(ConfluenceOS, true);

   int minBars = MathMax(StochKPeriod + StochSmoothing + StochDPeriod, RSIPeriod) + 10;
   if(rates_total < minBars) return(0);

   int limit;
   if(prev_calculated <= 0)
      limit = rates_total - minBars;
   else
      limit = rates_total - prev_calculated + 1;

   // === STOCHASTIC ===
   double rawK[];
   ArrayResize(rawK, rates_total);
   ArraySetAsSeries(rawK, true);
   ArrayInitialize(rawK, EMPTY_VALUE);

   for(int i = limit + StochSmoothing + StochDPeriod; i >= 0; i--)
   {
      int shift = i;
      if(shift + StochKPeriod > rates_total - 1) continue;
      rawK[shift] = CalcRawStochK(shift, high, low, close, rates_total);
   }

   for(int i = limit + StochDPeriod; i >= 0; i--)
   {
      if(i + StochSmoothing - 1 >= rates_total) continue;
      double sum = 0;
      bool valid = true;
      for(int j = 0; j < StochSmoothing; j++)
      {
         if(rawK[i + j] == EMPTY_VALUE) { valid = false; break; }
         sum += rawK[i + j];
      }
      if(valid) SlowStochK[i] = sum / StochSmoothing;
      else      SlowStochK[i] = EMPTY_VALUE;
   }

   for(int i = limit; i >= 0; i--)
   {
      if(i + StochDPeriod - 1 >= rates_total) continue;
      double sum = 0;
      bool valid = true;
      for(int j = 0; j < StochDPeriod; j++)
      {
         if(SlowStochK[i + j] == EMPTY_VALUE) { valid = false; break; }
         sum += SlowStochK[i + j];
      }
      if(valid) SlowStochD[i] = sum / StochDPeriod;
      else      SlowStochD[i] = EMPTY_VALUE;
   }

   // === RSI via CopyBuffer ===
   {
      int copyCount = limit + 1;
      if(copyCount < 1) copyCount = 1;
      double rsiTemp[];
      ArraySetAsSeries(rsiTemp, true);
      int copied = CopyBuffer(hRSI, 0, 0, copyCount, rsiTemp);
      if(copied > 0)
      {
         for(int i = 0; i < copied && i < rates_total; i++)
            FastRSI[i] = rsiTemp[i];
      }
   }

   // === ZONE END ARROWS ===
   if(SIG_Show)
   {
      if(prev_calculated <= 0)
         ObjectsDeleteAll(0, "DFMO_SIG_");

      int sigStart = (prev_calculated <= 0)
                     ? rates_total - minBars
                     : MathMax(1, rates_total - prev_calculated);

      for(int i = sigStart; i >= 1; i--)
      {
         if(SlowStochK[i] == EMPTY_VALUE || FastRSI[i] == EMPTY_VALUE ||
            SlowStochK[i+1] == EMPTY_VALUE || FastRSI[i+1] == EMPTY_VALUE)
            continue;

         bool prevBarOB = (SlowStochK[i+1] > OverboughtLvl) && (FastRSI[i+1] > OverboughtLvl);
         bool prevBarOS = (SlowStochK[i+1] < OversoldLvl)   && (FastRSI[i+1] < OversoldLvl);
         bool currBarOB = (SlowStochK[i] > OverboughtLvl) && (FastRSI[i] > OverboughtLvl);
         bool currBarOS = (SlowStochK[i] < OversoldLvl)   && (FastRSI[i] < OversoldLvl);

         bool zoneEndOB = prevBarOB && !currBarOB;
         bool zoneEndOS = prevBarOS && !currBarOS;

         double atrVal = 0;
         double atrTemp[];
         ArraySetAsSeries(atrTemp, true);
         if(CopyBuffer(hATR, 0, i, 1, atrTemp) > 0)
            atrVal = atrTemp[0];

         if(zoneEndOB)
         {
            string arrowName = "DFMO_SIG_S_" + IntegerToString((int)time[i]);
            if(ObjectFind(0, arrowName) < 0)
            {
               ObjectCreate(0, arrowName, OBJ_ARROW, 0, time[i], high[i] + atrVal * 0.5);
               ObjectSetInteger(0, arrowName, OBJPROP_ARROWCODE, 234);
               ObjectSetInteger(0, arrowName, OBJPROP_COLOR, SIG_SellColor);
               ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 2);
               ObjectSetInteger(0, arrowName, OBJPROP_SELECTABLE, false);
               ObjectSetInteger(0, arrowName, OBJPROP_HIDDEN, true);
            }
         }

         if(zoneEndOS)
         {
            string arrowName = "DFMO_SIG_B_" + IntegerToString((int)time[i]);
            if(ObjectFind(0, arrowName) < 0)
            {
               ObjectCreate(0, arrowName, OBJ_ARROW, 0, time[i], low[i] - atrVal * 0.5);
               ObjectSetInteger(0, arrowName, OBJPROP_ARROWCODE, 233);
               ObjectSetInteger(0, arrowName, OBJPROP_COLOR, SIG_BuyColor);
               ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 2);
               ObjectSetInteger(0, arrowName, OBJPROP_SELECTABLE, false);
               ObjectSetInteger(0, arrowName, OBJPROP_HIDDEN, true);
            }
         }
      }
   }

   // === CONFLUENCE BACKGROUND ===
   int obZoneCount = 0, osZoneCount = 0, objCreated = 0;
   bool needRedraw = false;

   for(int i = limit; i >= 0; i--)
   {
      if(SlowStochK[i] == EMPTY_VALUE || FastRSI[i] == EMPTY_VALUE)
      {
         ConfluenceOB[i] = 0;
         ConfluenceOS[i] = 0;
         continue;
      }

      bool isOB = (SlowStochK[i] > OverboughtLvl) && (FastRSI[i] > OverboughtLvl);
      bool isOS = (SlowStochK[i] < OversoldLvl) && (FastRSI[i] < OversoldLvl);

      ConfluenceOB[i] = isOB ? 1.0 : 0.0;
      ConfluenceOS[i] = isOS ? 1.0 : 0.0;

      if(isOB) obZoneCount++;
      if(isOS) osZoneCount++;

      if(ShowConfluence && i > 0 && (isOB || isOS))
      {
         string objName = "DFMO_BG_" + IntegerToString((int)time[i]);
         if(ObjectFind(0, objName) >= 0) continue;

         datetime t1 = time[i];
         datetime t2 = (i > 0) ? time[i - 1] : time[i] + PeriodSeconds();
         color bgColor = isOB ? OBConfColor : OSConfColor;

         if(dfmoWindowIdx < 0)
            dfmoWindowIdx = ChartWindowFind(0, dfmoShortName);

         if(dfmoWindowIdx >= 0)
         {
            if(ObjectCreate(0, objName, OBJ_RECTANGLE, dfmoWindowIdx, t1, 0, t2, 100))
            {
               ObjectSetInteger(0, objName, OBJPROP_COLOR, bgColor);
               ObjectSetInteger(0, objName, OBJPROP_STYLE, STYLE_SOLID);
               ObjectSetInteger(0, objName, OBJPROP_WIDTH, 1);
               ObjectSetInteger(0, objName, OBJPROP_BACK, true);
               ObjectSetInteger(0, objName, OBJPROP_FILL, true);
               ObjectSetInteger(0, objName, OBJPROP_SELECTABLE, false);
               ObjectSetInteger(0, objName, OBJPROP_HIDDEN, true);
               objCreated++;
               needRedraw = true;
            }
         }
      }
   }

   static bool diagDone = false;
   if(!diagDone && prev_calculated <= 0)
   {
      Print("DFMO DIAG: limit=", limit, " OB_zones=", obZoneCount, " OS_zones=", osZoneCount,
            " obj_created=", objCreated, " winIdx=", dfmoWindowIdx,
            " ShowConf=", ShowConfluence, " StochK[1]=", DoubleToString(SlowStochK[1], 1),
            " RSI[1]=", DoubleToString(FastRSI[1], 1));
      diagDone = true;
   }

   if(needRedraw)
      ChartRedraw(0);

   // === ALERTS ===
   if(rates_total > 0)
   {
      datetime currentBarTime = time[0];

      if(AlertOnConfluence && currentBarTime != lastAlertTime)
      {
         int currentConf = 0;
         if(ConfluenceOB[0] > 0.5) currentConf = 1;
         else if(ConfluenceOS[0] > 0.5) currentConf = -1;

         if(currentConf != lastConfState && currentConf != 0)
         {
            if(currentConf == 1)
               Alert("DFMO [" + Symbol() + " " + GetPeriodStr() + "] OVERBOUGHT! Stoch="
                     + DoubleToString(SlowStochK[0], 1) + " RSI="
                     + DoubleToString(FastRSI[0], 1));
            else
               Alert("DFMO [" + Symbol() + " " + GetPeriodStr() + "] OVERSOLD! Stoch="
                     + DoubleToString(SlowStochK[0], 1) + " RSI="
                     + DoubleToString(FastRSI[0], 1));
            lastConfState = currentConf;
            lastAlertTime = currentBarTime;
         }
      }

      if(AlertOnCross && SlowStochK[1] != EMPTY_VALUE && SlowStochD[1] != EMPTY_VALUE
         && SlowStochK[0] != EMPTY_VALUE && SlowStochD[0] != EMPTY_VALUE)
      {
         int currentCross = 0;
         if(SlowStochK[1] <= SlowStochD[1] && SlowStochK[0] > SlowStochD[0])
            currentCross = 1;
         else if(SlowStochK[1] >= SlowStochD[1] && SlowStochK[0] < SlowStochD[0])
            currentCross = -1;

         if(currentCross != 0 && currentCross != lastCrossState)
         {
            if(currentCross == 1)
               Alert("DFMO [" + Symbol() + " " + GetPeriodStr()
                     + "] Bullish Cross: %K above %D");
            else
               Alert("DFMO [" + Symbol() + " " + GetPeriodStr()
                     + "] Bearish Cross: %K below %D");
            lastCrossState = currentCross;
         }
      }
   }

   return(rates_total);
}

//+------------------------------------------------------------------+
string GetPeriodStr()
{
   switch((int)Period())
   {
      case PERIOD_M1:  return("M1");
      case PERIOD_M5:  return("M5");
      case PERIOD_M15: return("M15");
      case PERIOD_M30: return("M30");
      case PERIOD_H1:  return("H1");
      case PERIOD_H4:  return("H4");
      case PERIOD_D1:  return("D1");
      case PERIOD_W1:  return("W1");
      case PERIOD_MN1: return("MN1");
      default:         return("M" + IntegerToString((int)Period()));
   }
}
//+------------------------------------------------------------------+
