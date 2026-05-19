//+------------------------------------------------------------------+
//|                                            CleanV2Pendings.mq5  |
//|       Script one-shot: cancel.la TOTS els pendents del magic    |
//|       indicat al simbol actiu. NO toca posicions obertes.       |
//|                                                                  |
//|       Us: arrossega'l al chart on tens el DualGridEA_v2_Reset   |
//|       i confirma. Fa la feina i s'autoelimina.                  |
//+------------------------------------------------------------------+
#property copyright "Claude + User"
#property version   "1.00"
#property strict
#property script_show_inputs

#include <Trade\Trade.mqh>
#include <Trade\OrderInfo.mqh>

input long InpMagicNumber = 88888;   // Magic dels pendents a esborrar (per defecte v2)
input bool InpAlsoOtherSymbols = false; // true = esborra tambe pendents en altres simbols

void OnStart()
{
   CTrade     trade;
   COrderInfo ord;

   int total_found = 0, total_deleted = 0, total_failed = 0;
   int sym_other = 0;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!ord.SelectByIndex(i)) continue;
      if(ord.Magic() != InpMagicNumber) continue;

      bool same_symbol = (ord.Symbol() == _Symbol);
      if(!same_symbol)
      {
         sym_other++;
         if(!InpAlsoOtherSymbols) continue;
      }

      total_found++;
      ulong tk = ord.Ticket();
      string sym = ord.Symbol();
      double pr  = ord.PriceOpen();
      ENUM_ORDER_TYPE ot = ord.OrderType();
      string type_str = EnumToString(ot);

      if(trade.OrderDelete(tk))
      {
         total_deleted++;
         PrintFormat("[CleanV2] OK delete tk=%I64u %s %s @ %.2f", tk, sym, type_str, pr);
      }
      else
      {
         total_failed++;
         PrintFormat("[CleanV2] FAIL delete tk=%I64u %s %s @ %.2f err=%d",
                     tk, sym, type_str, pr, trade.ResultRetcode());
      }
   }

   PrintFormat("[CleanV2] === FINAL ===");
   PrintFormat("[CleanV2] Magic=%d  Symbol=%s  AlsoOtherSymbols=%s",
               (int)InpMagicNumber, _Symbol, InpAlsoOtherSymbols ? "true" : "false");
   PrintFormat("[CleanV2] Pendents trobats: %d", total_found);
   PrintFormat("[CleanV2] Esborrats OK: %d", total_deleted);
   PrintFormat("[CleanV2] Fallits: %d", total_failed);
   if(sym_other > 0 && !InpAlsoOtherSymbols)
      PrintFormat("[CleanV2] (a més hi havia %d pendents amb el mateix magic en altres símbols, NO esborrats)", sym_other);

   Alert(StringFormat("CleanV2 acabat: %d pendents esborrats (%d fallits) al simbol %s",
                      total_deleted, total_failed, _Symbol));
}
//+------------------------------------------------------------------+
