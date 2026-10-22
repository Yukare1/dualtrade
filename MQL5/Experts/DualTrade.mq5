#property strict
#property version "3.001"
#property description "Demo-only day, technical swing and fundamental investment; shared account risk"
#include <Trade/Trade.mqh>
#include "../Include/DualSignals.mqh"
#include "../Include/DualPolicy.mqh"
#include "../Include/DualRisk.mqh"
#include "../Include/DualSwing.mqh"
#include "../Include/DualAgent.mqh"

#ifdef DUALTRADE_TESTER
input string BridgeFolder="DualTradeTests";
bool tester_writes=false;
#else
input string BridgeFolder="DualTrade";
#endif
input long ExpectedLogin=0;
input string ExpectedServer="ICMarketsSC-Demo";
input ulong DayMagic=26102201;
input ulong SwingMagic=26102202;
input ulong InvestmentMagic=26102203;
input bool AllowDemoOrders=false;
input bool RequireAgentReview=true;
// Default off. Dated user exception, never a successful observation record.
input long DayObservationWaiverUntil=0;
input string DayObservationWaiverContext="";
input string DayObservationWaiverAuthorization="";

struct Contract
{
   string ticker,symbol,risk_group;
   int bridge_mask;
   bool swing_ready,swing_allowed,swing_long_only;
   double commission,slippage,margin_rate,finance_long,finance_short,previous_high,previous_low;
   int server_offset;
   bool verified,pool,entry_allowed,veto_buy,veto_sell,council_ready,council_buy,council_sell;
   datetime open,close,earnings_close,earnings_end,sec_close_until,council_time;
   bool inv_ready,inv_allowed,inv_early,inv_coverage;
   double inv_score,inv_fair,inv_nominal,inv_budget,inv_reference;
   datetime inv_news_time;
   int inv_action,inv_period,inv_rank;
   string inv_event,inv_entry_event,inv_signal;
};
struct Engine
{
   string symbol;
   int style,atr,adx,rsi,ma,bands,trend_atr,trend_adx,fast,slow,exhaust,context_atr;
   ENUM_TIMEFRAMES tf,trend;
};
struct Macro { datetime start,finish,flatten; };
Contract contracts[];
Engine engines[];
Macro events[];
CTrade trade;
string prefix,owner_key,status_text="INITIALIZING",run_mode="stop";
datetime generated=0,last_timer=0;
bool snapshot_ready=false,observation_ready=false,locked=false,foreign_exposure=false;
bool investment_ready=false,investment_observed=false,day_enabled=true,investment_enabled=true;
bool swing_ready=false,swing_observed=false,swing_enabled=true;
double target_risk=40,absolute_risk=100,aggregate_risk=80,loss_limit=200,reserve=.2;
double position_fraction=.04,aggregate_fraction=.08,sector_fraction=.06,daily_fraction=.20;
double lifetime_limit=1000,drawdown_limit=1000,weekly_limit=600;
double gross_multiple=4,sector_multiple=2,stress_fraction=.08,stress_budget_fraction=.20;
int max_entries=5,cooldown=3600;
#include "../Include/DualAgentBridge.mqh"

double Get(string key,double fallback=0) { string k=prefix+key; return GlobalVariableCheck(k) ? GlobalVariableGet(k) : fallback; }
void Set(string key,double value) { GlobalVariableSet(prefix+key,value); }
string PK(ulong id,string name) { return "p."+(string)id+"."+name; }
bool Ours(ulong magic) { return magic==DayMagic || magic==SwingMagic || magic==InvestmentMagic; }
int Style(ulong magic) { return magic==InvestmentMagic ? 2 : magic==SwingMagic ? 1 : 0; }
ulong MagicFor(int style) { return style==2 ? InvestmentMagic : style==1 ? SwingMagic : DayMagic; }
int PositionStyle()
{
   ulong magic=(ulong)PositionGetInteger(POSITION_MAGIC),id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   // Version 0.2 investments used the old swing magic; never turn them into technical swings.
   if(magic==SwingMagic && (Get(PK(id,"policy"))==200 || StringFind(PositionGetString(POSITION_COMMENT),"DT:INV")==0)) return 2;
   return Style(magic);
}
bool PendingIntent()
{
   for(int style=0;style<3;style++) if(Get("intent."+(string)style+".time")>0) return true;
   return Get("reduction_pending")>0 || OrdersTotal()>0;
}
void MigrateInvestmentIntent()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
      if(PositionGetTicket(i)>0 && (ulong)PositionGetInteger(POSITION_MAGIC)==SwingMagic && StringFind(PositionGetString(POSITION_COMMENT),"DT:INV")==0)
         Set(PK((ulong)PositionGetInteger(POSITION_IDENTIFIER),"policy"),200);
   if(Get("intent.1.time")<=0 || Get("intent.1.policy")!=200) return;
   string fields[]={"time","budget","policy","early","period","close_time","entry_bar","strategy"};
   if(Get("intent.2.time")>0)
   {
      bool same=(ulong)Get("intent.2.magic")==SwingMagic;
      for(int i=0;i<ArraySize(fields);i++)if(Get("intent.2."+fields[i])!=Get("intent.1."+fields[i]))same=false;
      if(same){Set("intent.1.time",0);GlobalVariablesFlush();}
      else Set("unreconciled",1);
      return;
   }
   for(int i=0;i<ArraySize(fields);i++) Set("intent.2."+fields[i],Get("intent.1."+fields[i]));
   Set("intent.2.magic",(double)SwingMagic); GlobalVariablesFlush();
   Set("intent.1.time",0); GlobalVariablesFlush();
}
void SaveRiskLimits()
{
   double values[]={target_risk,absolute_risk,aggregate_risk,loss_limit,reserve,
      position_fraction,aggregate_fraction,sector_fraction,daily_fraction,lifetime_limit,
      drawdown_limit,weekly_limit,gross_multiple,sector_multiple,stress_fraction,stress_budget_fraction};
   for(int i=0;i<ArraySize(values);i++)Set("limit."+(string)i,values[i]);
   Set("limit.entries",max_entries);Set("limit.cooldown",cooldown);GlobalVariablesFlush();
}
void RestoreRiskLimits()
{
   // A missing/stale bridge cannot loosen the last accepted protective limits.
   target_risk=MathMin(target_risk,Get("limit.0",target_risk));absolute_risk=MathMin(absolute_risk,Get("limit.1",absolute_risk));
   aggregate_risk=MathMin(aggregate_risk,Get("limit.2",aggregate_risk));loss_limit=MathMin(loss_limit,Get("limit.3",loss_limit));reserve=MathMax(reserve,Get("limit.4",reserve));
   position_fraction=MathMin(position_fraction,Get("limit.5",position_fraction));aggregate_fraction=MathMin(aggregate_fraction,Get("limit.6",aggregate_fraction));
   sector_fraction=MathMin(sector_fraction,Get("limit.7",sector_fraction));daily_fraction=MathMin(daily_fraction,Get("limit.8",daily_fraction));
   lifetime_limit=MathMin(lifetime_limit,Get("limit.9",lifetime_limit));drawdown_limit=MathMin(drawdown_limit,Get("limit.10",drawdown_limit));weekly_limit=MathMin(weekly_limit,Get("limit.11",weekly_limit));
   gross_multiple=MathMin(gross_multiple,Get("limit.12",gross_multiple));sector_multiple=MathMin(sector_multiple,Get("limit.13",sector_multiple));
   stress_fraction=MathMax(stress_fraction,Get("limit.14",stress_fraction));stress_budget_fraction=MathMin(stress_budget_fraction,Get("limit.15",stress_budget_fraction));
   max_entries=(int)MathMin(max_entries,Get("limit.entries",max_entries));cooldown=(int)MathMax(cooldown,Get("limit.cooldown",cooldown));
}
bool Identity()
{
   return ExpectedLogin>0 && AccountInfoInteger(ACCOUNT_LOGIN)==ExpectedLogin &&
      AccountInfoInteger(ACCOUNT_TRADE_MODE)==ACCOUNT_TRADE_MODE_DEMO &&
      AccountInfoInteger(ACCOUNT_MARGIN_MODE)==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING &&
      AccountInfoString(ACCOUNT_SERVER)==ExpectedServer && AccountInfoString(ACCOUNT_CURRENCY)=="USD";
}
bool MayWrite()
{
#ifdef DUALTRADE_TESTER
   // Compiled only into the test/replay experts. Never permit account execution.
   return MQLInfoInteger(MQL_TESTER) && tester_writes;
#else
   return Identity() && locked && GlobalVariableGet(owner_key)==(double)ChartID() && AllowDemoOrders;
#endif
}
void Audit(string type,string symbol,string reason,double a=0,double b=0,double c=0)
{
   int f=FileOpen(BridgeFolder+"\\audit.csv",FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ,'|');
   if(f==INVALID_HANDLE) return;
   FileSeek(f,0,SEEK_END);
   FileWrite(f,(long)TimeGMT(),type,symbol,reason,DoubleToString(a,8),DoubleToString(b,8),DoubleToString(c,8));
   FileFlush(f); FileClose(f);
}
int NthSunday(int year,int month,int nth)
{
   MqlDateTime d={}; d.year=year; d.mon=month; d.day=1;
   datetime t=StructToTime(d); TimeToStruct(t,d);
   return 1+(7-d.day_of_week)%7+7*(nth-1);
}
int NyOffset(datetime utc)
{
   MqlDateTime d; TimeToStruct(utc,d); int y=d.year;
   d.mon=3; d.day=NthSunday(y,3,2); d.hour=7; d.min=0; d.sec=0; datetime start=StructToTime(d);
   d.mon=11; d.day=NthSunday(y,11,1); d.hour=6; datetime finish=StructToTime(d);
   return utc>=start && utc<finish ? -14400 : -18000;
}
int NyDay(datetime utc)
{
   MqlDateTime d; TimeToStruct(utc+NyOffset(utc),d); return d.year*10000+d.mon*100+d.day;
}
bool ReadValue(int h,int buffer,int shift,double &v)
{
   double a[1]; if(h==INVALID_HANDLE || CopyBuffer(h,buffer,shift,1,a)!=1 || !MathIsValidNumber(a[0])) return false;
   v=a[0]; return true;
}
int FindContract(string symbol) { for(int i=0;i<ArraySize(contracts);i++) if(contracts[i].symbol==symbol) return i; return -1; }
int FindEngine(string symbol,int style) { for(int i=0;i<ArraySize(engines);i++) if(engines[i].symbol==symbol && engines[i].style==style) return i; return -1; }
void AddEngine(string symbol,int style)
{
   if(FindEngine(symbol,style)>=0) return;
   int i=ArraySize(engines); ArrayResize(engines,i+1);
   engines[i].symbol=symbol; engines[i].style=style;
   engines[i].tf=style==0 ? PERIOD_M15 : PERIOD_H4;
   engines[i].trend=style==0 ? PERIOD_H1 : PERIOD_D1;
   ENUM_TIMEFRAMES tf=engines[i].tf,tr=engines[i].trend;
   engines[i].atr=iATR(symbol,tf,14); engines[i].adx=iADX(symbol,tf,14);
   engines[i].rsi=iRSI(symbol,tf,14,PRICE_CLOSE); engines[i].ma=iMA(symbol,tf,20,0,MODE_EMA,PRICE_CLOSE);
   engines[i].bands=iBands(symbol,tf,20,0,2,PRICE_CLOSE);
   engines[i].context_atr=iATR(symbol,style==0 ? PERIOD_H4 : PERIOD_D1,14);
   engines[i].trend_atr=iATR(symbol,tr,14); engines[i].trend_adx=iADX(symbol,tr,14);
   engines[i].fast=iMA(symbol,tr,20,0,MODE_EMA,PRICE_CLOSE); engines[i].slow=iMA(symbol,tr,50,0,MODE_EMA,PRICE_CLOSE);
   engines[i].exhaust=iRSI(symbol,style==0 ? PERIOD_H4 : PERIOD_D1,14,PRICE_CLOSE);
   string key="bar."+symbol+"."+(string)style;
   // On attach/restart consume the current entry bar: never replay an old entry.
   Set(key,(double)iTime(symbol,tf,0));
}
bool LoadSnapshot()
{
   int f=FileOpen(BridgeFolder+"\\snapshot.txt",FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(f==INVALID_HANDLE) return false;
   Contract next[]; Macro next_events[];
   bool header=false,end=false,ready=false,observed=false,inv_ready=false,inv_observed=false,next_day=false,next_inv=false,modes=false;
   bool next_sw_ready=false,next_sw_observed=false,next_sw_enabled=false,shared=false; double sr[11]; datetime stamp=0;
   string next_mode="stop",next_context=""; int rows=0,expected=0; double risk[5]; int entries=5,wait=3600;
   while(!FileIsEnding(f))
   {
      string line=FileReadString(f),v[]; int n=StringSplit(line,'|',v); if(n<1 || line=="") continue;
      rows++;
      if(v[0]=="HEADER" && n==16)
      {
         if(v[1]!="4" || (long)StringToInteger(v[5])!=ExpectedLogin || v[6]!=ExpectedServer || v[7]!="USD") break;
         stamp=(datetime)StringToInteger(v[2]); next_mode=v[3]; ready=StringToInteger(v[4])==1;
         observed=StringToInteger(v[8])==1;
         bool valid=true;
         for(int j=0;j<5;j++) { risk[j]=StringToDouble(v[9+j]); if(!MathIsValidNumber(risk[j])) valid=false; }
         if(!valid) break;
         entries=(int)StringToInteger(v[14]); wait=(int)StringToInteger(v[15]);
         if(risk[0]<=0 || risk[0]>40 || risk[1]<risk[0] || risk[1]>100 || risk[2]<=0 || risk[2]>80 || risk[3]<=0 || risk[3]>200 || risk[4]<.2 || risk[4]>=1 || entries<1 || entries>5 || wait<3600) break;
         header=true;
      }
      else if(v[0]=="CONTEXT" && n==2)
      {
         if(StringLen(v[1])!=64 || next_context!="")break;
         next_context=v[1];
      }
      else if(v[0]=="MODES" && n==8)
      {
         inv_ready=StringToInteger(v[1])==1; inv_observed=StringToInteger(v[2])==1;
         next_day=StringToInteger(v[3])==1; next_inv=StringToInteger(v[4])==1;modes=true;
         next_sw_ready=StringToInteger(v[5])==1;next_sw_observed=StringToInteger(v[6])==1;next_sw_enabled=StringToInteger(v[7])==1;
      }
      else if(v[0]=="RISK" && n==12)
      {
         double caps[]={.04,.08,.06,.20,1000,1000,600,4,2,.08,.20};bool valid=true;
         for(int j=0;j<11;j++)
         {
            sr[j]=StringToDouble(v[j+1]);
            if(!MathIsValidNumber(sr[j]) || (j==9 ? sr[j]<.08 || sr[j]>.30 : sr[j]<=0 || sr[j]>caps[j]))valid=false;
         }
         if(!valid || sr[0]>sr[1] || sr[2]>sr[1] || shared)break;
         shared=true;
      }
      else if(v[0]=="CONTRACT" && n==23)
      {
         int k=ArraySize(next); ArrayResize(next,k+1);
         ZeroMemory(next[k]);
         next[k].ticker=v[1]; next[k].symbol=v[2]; next[k].verified=StringToInteger(v[3])==1;
         next[k].commission=StringToDouble(v[4]); next[k].slippage=StringToDouble(v[5]); next[k].margin_rate=StringToDouble(v[6]);
         next[k].finance_long=StringToDouble(v[7]); next[k].finance_short=StringToDouble(v[8]); next[k].server_offset=(int)StringToInteger(v[9]);
         next[k].open=(datetime)StringToInteger(v[10]); next[k].close=(datetime)StringToInteger(v[11]);
         next[k].pool=StringToInteger(v[12])==1; next[k].entry_allowed=StringToInteger(v[13])==1;
         next[k].veto_buy=StringToInteger(v[14])==1; next[k].veto_sell=StringToInteger(v[15])==1;
         next[k].earnings_close=(datetime)StringToInteger(v[16]); next[k].earnings_end=(datetime)StringToInteger(v[17]);
         next[k].sec_close_until=(datetime)StringToInteger(v[18]);
         next[k].previous_high=StringToDouble(v[19]); next[k].previous_low=StringToDouble(v[20]);
         if(v[21]!="USD" || StringToInteger(v[22])!=1 || next[k].commission<0 || next[k].margin_rate<=0 || next[k].margin_rate>1 || next[k].finance_long<0 || next[k].finance_short<0) next[k].verified=false;
      }
      else if(v[0]=="COUNCIL" && n==6)
      {
         int k=-1;for(int j=0;j<ArraySize(next);j++)if(next[j].symbol==v[1]){k=j;break;}
         if(k<0)break;
         next[k].council_ready=StringToInteger(v[2])==1;next[k].council_buy=StringToInteger(v[3])==1;next[k].council_sell=StringToInteger(v[4])==1;
         next[k].council_time=(datetime)StringToInteger(v[5]);
         if((next[k].bridge_mask & 1)!=0)break;next[k].bridge_mask|=1;
         if(next[k].council_time>TimeGMT()+5 || stamp-next[k].council_time>60)next[k].council_ready=false;
      }
      else if(v[0]=="GROUP" && n==3)
      {
         int k=-1;for(int j=0;j<ArraySize(next);j++)if(next[j].symbol==v[1]){k=j;break;}
         if(k<0 || v[2]=="" || (next[k].bridge_mask & 4)!=0)break;
         next[k].risk_group=v[2];next[k].bridge_mask|=4;
      }
      else if(v[0]=="SWING" && n==5)
      {
         int k=-1;for(int j=0;j<ArraySize(next);j++)if(next[j].symbol==v[1]){k=j;break;}
         if(k<0 || v[4]!="1" || (next[k].bridge_mask & 8)!=0)break;
         next[k].swing_ready=v[2]=="1";next[k].swing_allowed=v[3]=="1";next[k].swing_long_only=true;
         next[k].bridge_mask|=8;
      }
      else if(v[0]=="INVESTMENT" && n==18)
      {
         int k=-1;for(int j=0;j<ArraySize(next);j++)if(next[j].symbol==v[1]){k=j;break;}
         if(k<0)break;
         if((next[k].bridge_mask & 2)!=0)break;next[k].bridge_mask|=2;
         next[k].inv_ready=StringToInteger(v[2])==1;next[k].inv_allowed=StringToInteger(v[3])==1;
         next[k].inv_score=StringToDouble(v[4]);next[k].inv_fair=StringToDouble(v[5]);
         next[k].inv_early=StringToInteger(v[6])==1;next[k].inv_nominal=StringToDouble(v[7]);next[k].inv_budget=StringToDouble(v[8]);
         next[k].inv_action=(int)StringToInteger(v[9]);next[k].inv_event=v[10];next[k].inv_period=(int)StringToInteger(v[11]);
         next[k].inv_rank=(int)StringToInteger(v[12]);next[k].inv_coverage=StringToInteger(v[13])==1;next[k].inv_entry_event=v[14];next[k].inv_reference=StringToDouble(v[15]);next[k].inv_news_time=(datetime)StringToInteger(v[16]);next[k].inv_signal=v[17];
         if(next[k].inv_nominal<=0 || next[k].inv_nominal>.25 || next[k].inv_budget<=0 || next[k].inv_budget>40 || next[k].inv_fair<0 || next[k].inv_action<0 || next[k].inv_action>3)break;
      }
      else if(v[0]=="MACRO" && n==4)
      {
         int k=ArraySize(next_events); ArrayResize(next_events,k+1);
         next_events[k].start=(datetime)StringToInteger(v[1]); next_events[k].finish=(datetime)StringToInteger(v[2]); next_events[k].flatten=(datetime)StringToInteger(v[3]);
      }
      else if(v[0]=="END" && n==2) { expected=(int)StringToInteger(v[1]); end=true; break; }
      else break;
   }
   FileClose(f);
   if(!header || !modes || !shared || !end || expected!=rows-1 || stamp<generated || stamp>TimeGMT()+5) return false;
   for(int j=0;j<ArraySize(next);j++)if(next[j].bridge_mask!=15)return false;
   // Preserve known event obligations even if a later feed drops the record.
   for(int i=0;i<ArraySize(next);i++)
   {
      int old=FindContract(next[i].symbol);
      if(old>=0 && contracts[old].earnings_end>=TimeGMT() && (next[i].earnings_close==0 || contracts[old].earnings_close<next[i].earnings_close))
      { next[i].earnings_close=contracts[old].earnings_close; next[i].earnings_end=contracts[old].earnings_end; }
      if(old>=0) next[i].sec_close_until=MathMax(next[i].sec_close_until,contracts[old].sec_close_until);
   }
   ArrayResize(contracts,ArraySize(next)); for(int j=0;j<ArraySize(next);j++) contracts[j]=next[j];
   ArrayCopy(events,next_events);
   investment_ready=inv_ready;investment_observed=inv_observed;day_enabled=next_day;investment_enabled=next_inv;
   swing_ready=next_sw_ready;swing_observed=next_sw_observed;swing_enabled=next_sw_enabled;
   position_fraction=sr[0];aggregate_fraction=sr[1];sector_fraction=sr[2];daily_fraction=sr[3];
   lifetime_limit=sr[4];drawdown_limit=sr[5];weekly_limit=sr[6];gross_multiple=sr[7];sector_multiple=sr[8];stress_fraction=sr[9];stress_budget_fraction=sr[10];
   generated=stamp; run_mode=next_mode; snapshot_ready=ready; observation_ready=observed;
   decision_context=next_context;
   target_risk=risk[0]; absolute_risk=risk[1]; aggregate_risk=risk[2]; loss_limit=risk[3]; reserve=risk[4]; max_entries=entries; cooldown=wait;
   SaveRiskLimits();
   for(int i=0;i<ArraySize(contracts);i++) if(SymbolSelect(contracts[i].symbol,true))
   { AddEngine(contracts[i].symbol,0); AddEngine(contracts[i].symbol,1); }
   return true;
}
bool MacroBlocked(datetime now,bool flatten=false)
{
   for(int i=0;i<ArraySize(events);i++) if(now>=(flatten ? events[i].flatten : events[i].start) && now<=events[i].finish) return true;
   return false;
}
bool CloseTicket(ulong ticket,string reason)
{
   if(!PositionSelectByTicket(ticket) || !Ours((ulong)PositionGetInteger(POSITION_MAGIC)) || !MayWrite()) return false;
   string symbol=PositionGetString(POSITION_SYMBOL); ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   Set(PK(id,"close"),1); GlobalVariablesFlush();
   trade.SetExpertMagicNumber((ulong)PositionGetInteger(POSITION_MAGIC)); trade.SetTypeFillingBySymbol(symbol);
   bool sent=trade.PositionClose(ticket,20);
   Audit("EXIT",symbol,reason,trade.ResultRetcode(),ticket);
   return sent && trade.ResultRetcode()==TRADE_RETCODE_DONE;
}
int CountOurs(int style=-1)
{
   int count=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0) continue; ulong magic=(ulong)PositionGetInteger(POSITION_MAGIC);
      if(Ours(magic) && (style<0 || PositionStyle()==style)) count++;
   }
   return count;
}
void Exposure()
{
   foreign_exposure=false;
   for(int i=PositionsTotal()-1;i>=0;i--)
      if(PositionGetTicket(i)>0 && !Ours((ulong)PositionGetInteger(POSITION_MAGIC))) foreign_exposure=true;
   for(int i=OrdersTotal()-1;i>=0;i--)
      if(OrderGetTicket(i)>0 && !Ours((ulong)OrderGetInteger(ORDER_MAGIC))) foreign_exposure=true;
}
void CancelOurs()
{
   if(!MayWrite()) return;
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      ulong ticket=OrderGetTicket(i);
      if(ticket>0 && Ours((ulong)OrderGetInteger(ORDER_MAGIC)) && MayWrite()) trade.OrderDelete(ticket);
   }
}
void UpdateLedger(datetime now)
{
   double equity=AccountInfoDouble(ACCOUNT_EQUITY); int today=NyDay(now);
   if(Get("ledger_init")==0 && Get("baseline",0)>0)Set("ledger_init",1); // Preserve an existing ledger.
   if(Get("ledger_init")==0)
   {
      if(CountOurs()>0) { Set("unreconciled",1); return; }
      Set("baseline",equity); Set("day",today); Set("cash_checked",(double)TimeCurrent());Set("ledger_init",1);
   }
   MqlDateTime week_date;datetime local=now+NyOffset(now);TimeToStruct(local,week_date);
   int week_day=(week_date.day_of_week+6)%7;datetime monday=local-week_day*86400;
   MqlDateTime md;TimeToStruct(monday,md);int week=md.year*10000+md.mon*100+md.day;
   if(Get("capital_init")==0)
   {
      Set("capital_base",1000);Set("capital_peak",MathMax(1000,equity));
      Set("week_base",Get("baseline"));Set("week",week);Set("capital_init",1);
   }
   datetime cash_checked=(datetime)Get("cash_checked",0);
   if(!HistorySelect(cash_checked,TimeCurrent())) { Set("unreconciled",1); return; }
   double cash=0;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong ticket=HistoryDealGetTicket(i); long type=HistoryDealGetInteger(ticket,DEAL_TYPE);
      if(type==DEAL_TYPE_BALANCE || type==DEAL_TYPE_CREDIT)
      {
         string key="cash."+(string)ticket;
         if(Get(key)==0)
         {
            string note=HistoryDealGetString(ticket,DEAL_COMMENT); StringToLower(note);
            bool dividend=StringFind(note,"dividend")>=0 || StringFind(note,"div ")>=0;
            bool external=StringFind(note,"deposit")==0 || StringFind(note,"withdraw")==0;
            // Dividend cash adjustments are trading P&L, not external funding.
            if(!dividend && external && type==DEAL_TYPE_BALANCE) cash+=HistoryDealGetDouble(ticket,DEAL_PROFIT);
            else if(!dividend) { Set("unreconciled",1); Audit("BLOCK","","UNCLASSIFIED_CASHFLOW",ticket); }
            Set(key,1);
         }
      }
   }
   if(cash!=0)
   {
      Set("baseline",Get("baseline")+cash);Set("capital_base",Get("capital_base")+cash);
      Set("capital_peak",Get("capital_peak")+cash);Set("week_base",Get("week_base")+cash);
   }
   Set("cash_checked",(double)TimeCurrent());
   Set("capital_peak",MathMax(Get("capital_peak"),equity));
   if(equity<=Get("capital_base")-lifetime_limit || equity<=Get("capital_peak")-drawdown_limit)
      {Set("hard_halt",1);Set("must_flat",1);}
   if(equity<=Get("week_base")-weekly_limit){Set("week_halt",1);Set("must_flat",1);}
   double daily=DTScaledLimit(loss_limit,Get("baseline"),daily_fraction);
   if(equity<=Get("baseline")-daily) { Set("halt",1); Set("must_flat",1); }
   if(Get("must_flat")>0 && CountOurs()==0 && OrdersTotal()==0) Set("must_flat",0);
   if((int)Get("day")!=today)
   {
      bool continuous=last_timer>0 && now-last_timer<=15;
      if(!continuous) Set("unreconciled",1);
      else if(DTCanRollDay(continuous,Get("must_flat")>0,Get("unreconciled")==0))
      { Set("day",today); Set("baseline",equity); Set("halt",0); Set("entries",0); }
   }
   if((int)Get("week")!=week)
   {
      if(last_timer<=0 || now-last_timer>15)Set("unreconciled",1);
      else if(Get("must_flat")==0 && Get("unreconciled")==0 && Get("hard_halt")==0)
         {Set("week",week);Set("week_base",equity);Set("week_halt",0);}
   }
   Set("last_seen",(double)now); Set("equity",equity); GlobalVariablesFlush();
   if(Get("halt")>0 || Get("hard_halt")>0 || Get("week_halt")>0)
   {
      CancelOurs();
      for(int i=PositionsTotal()-1;i>=0;i--) { ulong ticket=PositionGetTicket(i); if(ticket>0) CloseTicket(ticket,"ACCOUNT_LOSS_LIMIT"); }
   }
}
double StopLossMoney(string symbol,bool buy,double volume,double entry,double stop)
{
   double p=0;
   if(stop<=0 || volume<=0 || !OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,symbol,volume,entry,stop,p)) return -1;
   return MathMax(0.0,-p);
}
double UnitCost(int ci,int style,bool buy,bool investment=false)
{
   // Commission is conservatively normalized to minimum volume by the collector.
   return contracts[ci].commission+(style>0 ? (investment || style==2 ? 180 : 7)*(buy ? contracts[ci].finance_long : contracts[ci].finance_short) : 0);
}
double OpenRisk(bool from_market)
{
   double total=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0 || !Ours((ulong)PositionGetInteger(POSITION_MAGIC))) continue;
      string s=PositionGetString(POSITION_SYMBOL); int ci=FindContract(s);
      if(ci<0) return 1e100;
      bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
      double volume=PositionGetDouble(POSITION_VOLUME),stop=PositionGetDouble(POSITION_SL),point=SymbolInfoDouble(s,SYMBOL_POINT);
      double entry=PositionGetDouble(from_market ? POSITION_PRICE_CURRENT : POSITION_PRICE_OPEN);
      double loss=StopLossMoney(s,buy,volume,entry,stop+(buy ? -1 : 1)*contracts[ci].slippage*point);
      if(loss<0) return 1e100;
      total+=loss+volume*UnitCost(ci,PositionStyle(),buy,Get(PK((ulong)PositionGetInteger(POSITION_IDENTIFIER),"policy"))==200);
   }
   return total;
}
double GroupRisk(int ci)
{
   double result=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0 || !Ours((ulong)PositionGetInteger(POSITION_MAGIC)))continue;
      string symbol=PositionGetString(POSITION_SYMBOL);int other=FindContract(symbol);
      if(other<0)return 1e100;
      if(contracts[other].risk_group!=contracts[ci].risk_group)continue;
      bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;double v=PositionGetDouble(POSITION_VOLUME);
      double loss=StopLossMoney(symbol,buy,v,PositionGetDouble(POSITION_PRICE_OPEN),PositionGetDouble(POSITION_SL)+(buy ? -1 : 1)*contracts[other].slippage*SymbolInfoDouble(symbol,SYMBOL_POINT));
      if(loss<0)return 1e100;
      result+=loss+v*UnitCost(other,PositionStyle(),buy);
   }
   return result;
}
double GrossExposure(int ci=-1)
{
   double total=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0 || !Ours((ulong)PositionGetInteger(POSITION_MAGIC)))continue;
      string symbol=PositionGetString(POSITION_SYMBOL);int other=FindContract(symbol);
      if(other<0)return 1e100;
      if(ci>=0 && contracts[other].risk_group!=contracts[ci].risk_group)continue;
      total+=PositionGetDouble(POSITION_VOLUME)*PositionGetDouble(POSITION_PRICE_CURRENT)*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE);
   }
   return total;
}
double OpenStress()
{
   double total=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0 || !Ours((ulong)PositionGetInteger(POSITION_MAGIC)))continue;
      string symbol=PositionGetString(POSITION_SYMBOL);int ci=FindContract(symbol);if(ci<0)return 1e100;
      bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;double v=PositionGetDouble(POSITION_VOLUME),price=PositionGetDouble(POSITION_PRICE_CURRENT);
      double stop=StopLossMoney(symbol,buy,v,price,PositionGetDouble(POSITION_SL)+(buy ? -1 : 1)*contracts[ci].slippage*SymbolInfoDouble(symbol,SYMBOL_POINT));
      if(stop<0)return 1e100;
      total+=MathMax(stop,v*price*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE)*stress_fraction)+v*UnitCost(ci,PositionStyle(),buy);
   }
   return total;
}
bool SharedEntryGate(int style)
{
   if(foreign_exposure || Get("halt")>0 || Get("hard_halt")>0 || Get("week_halt")>0 || Get("unreconciled")>0 || Get("must_flat")>0 || PendingIntent() || CountOurs(style)>0 || Get("entries")>=max_entries)return false;
   return true;
}
double SharedBudget(int ci,double target)
{
   double equity=AccountInfoDouble(ACCOUNT_EQUITY),downside=OpenRisk(true);
   double money=DTRiskBudget(DTScaledLimit(target,equity,position_fraction),absolute_risk,
      DTScaledLimit(aggregate_risk,equity,aggregate_fraction),OpenRisk(false),equity,Get("baseline"),
      DTScaledLimit(loss_limit,Get("baseline"),daily_fraction),downside);
   money=MathMin(money,MathMax(0,equity*sector_fraction-GroupRisk(ci)));
   return MathMin(money,DTAccountCapacity(equity,Get("capital_base",1000),Get("capital_peak",1000),Get("week_base",1000),lifetime_limit,drawdown_limit,weekly_limit,downside));
}
double SharedVolume(int ci,bool buy,double entry,double unit_risk,double unit_cost,double proposed)
{
   string symbol=contracts[ci].symbol;double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double nominal=entry*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE);
   double stress=MathMax(unit_risk,nominal*stress_fraction+unit_cost);
   double volume=MathMin(proposed,DTExposureVolume(equity,GrossExposure(),GrossExposure(ci),nominal,gross_multiple,sector_multiple));
   volume=MathMin(volume,DTStressVolume(equity,OpenStress(),stress,stress_budget_fraction));
   double minimum=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP),maximum=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX);
   volume=FloorVolume(volume,minimum,maximum,step);double native_margin=0;
   if(volume<=0 || !OrderCalcMargin(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,symbol,volume,entry,native_margin))return 0;
   double per_lot=MathMax(native_margin/volume,nominal*contracts[ci].margin_rate);
   if(per_lot<=0)return 0;
   return FloorVolume(MathMin(volume,MathMax(0,equity*(1-reserve)-WorstMargin())/per_lot),minimum,maximum,step);
}
bool SharedFillAllowed(int ci)
{
   double eq=AccountInfoDouble(ACCOUNT_EQUITY),downside=OpenRisk(true);
   return OpenRisk(false)<=DTScaledLimit(aggregate_risk,eq,aggregate_fraction)+.01 &&
      GroupRisk(ci)<=eq*sector_fraction+.01 && GrossExposure()<=eq*gross_multiple+.01 &&
      GrossExposure(ci)<=eq*sector_multiple+.01 && OpenStress()<=eq*stress_budget_fraction+.01 &&
      WorstMargin()<=eq*(1-reserve)+.01 &&
      downside<=eq-Get("baseline")+DTScaledLimit(loss_limit,Get("baseline"),daily_fraction)+.01 &&
      downside<=eq-Get("capital_base",1000)+lifetime_limit+.01 &&
      downside<=eq-Get("capital_peak",1000)+drawdown_limit+.01 &&
      downside<=eq-Get("week_base",1000)+weekly_limit+.01;
}
double WorstMargin()
{
   double sum=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      if(PositionGetTicket(i)==0) continue;
      string s=PositionGetString(POSITION_SYMBOL); int ci=FindContract(s);
      if(ci<0 || !contracts[ci].verified) return 1e100;
      double p=PositionGetDouble(POSITION_PRICE_CURRENT),v=PositionGetDouble(POSITION_VOLUME),margin=0;
      bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
      if(!OrderCalcMargin(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,s,v,p,margin)) return 1e100;
      sum+=MathMax(margin,p*v*SymbolInfoDouble(s,SYMBOL_TRADE_CONTRACT_SIZE)*contracts[ci].margin_rate);
   }
   return sum;
}
double PaidCosts(ulong id)
{
   double paid=0;
   if(!HistorySelectByPosition(id)) return -1;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong ticket=HistoryDealGetTicket(i);
      paid+=MathMax(0,-HistoryDealGetDouble(ticket,DEAL_COMMISSION))+MathMax(0,-HistoryDealGetDouble(ticket,DEAL_FEE))+MathMax(0,-HistoryDealGetDouble(ticket,DEAL_SWAP));
   }
   return paid;
}
void Reconcile()
{
   MigrateInvestmentIntent();
   for(int style=0;style<3;style++)
   {
      string k="intent."+(string)style+".";
      if(Get(k+"time")==0) continue;
      bool found=false;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong ticket=PositionGetTicket(i); if(ticket==0) continue;
         ulong magic=(ulong)PositionGetInteger(POSITION_MAGIC);
         ulong expected=(ulong)Get(k+"magic",(double)MagicFor(style));
         if(!Ours(magic) || magic!=expected || (style==1 && PositionStyle()==2)) continue;
         found=true; ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
         string symbol=PositionGetString(POSITION_SYMBOL); int ci=FindContract(symbol);
         if(Get(PK(id,"r"))==0)
         {
            double entry=PositionGetDouble(POSITION_PRICE_OPEN),sl=PositionGetDouble(POSITION_SL);
            Set(PK(id,"r"),MathAbs(entry-sl)); Set(PK(id,"initial_sl"),sl); Set(PK(id,"style"),style);
            Set(PK(id,"close_time"),Get(k+"close_time"));
            Set(PK(id,"budget"),Get(k+"budget"));
            Set(PK(id,"entry_bar"),Get(k+"entry_bar")); Set(PK(id,"strategy"),Get(k+"strategy"));
            Set(PK(id,"intent_time"),Get(k+"time"));
            Set(PK(id,"policy"),Get(k+"policy"));Set(PK(id,"early"),Get(k+"early"));
            Set(PK(id,"period"),Get(k+"period"));Set(PK(id,"opened"),Get(k+"time"));
            Audit("POSITION_STATE",symbol,(string)id,style,Get(k+"entry_bar"),Get(k+"strategy"));
         }
         bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY; double volume=PositionGetDouble(POSITION_VOLUME);
         double fill=PositionGetDouble(POSITION_PRICE_OPEN),slip=ci>=0 ? contracts[ci].slippage*SymbolInfoDouble(symbol,SYMBOL_POINT) : 0;
         double loss=StopLossMoney(symbol,buy,volume,fill,PositionGetDouble(POSITION_SL)+(buy ? -slip : slip)),gain=0;
         bool calculated=OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,symbol,volume,fill,PositionGetDouble(POSITION_TP),gain);
         double costs=ci>=0 ? volume*UnitCost(ci,style,buy,Get(PK(id,"policy"))==200) : 1e100;
         if(Get(PK(id,"original_risk"))==0) Set(PK(id,"original_risk"),loss+costs);
         if(ci<0 || loss<0 || loss+costs>Get(k+"budget")+.01 || (Get(PK(id,"policy"))!=200 && (PositionGetDouble(POSITION_TP)<=0 || !calculated || (gain-costs)<1.5*(loss+costs))) || (Get(PK(id,"policy"))==200 && (PositionGetInteger(POSITION_TYPE)!=POSITION_TYPE_BUY || ci<0 || (contracts[ci].inv_fair-fill)*volume*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE)-costs<1.5*(loss+costs))))
            CloseTicket(ticket,"FILL_RISK_OR_PROTECTION");
         else if(!SharedFillAllowed(ci))CloseTicket(ticket,"SHARED_FILL_RISK");
      }
      bool pending=false;
      for(int i=OrdersTotal()-1;i>=0;i--) if(OrderGetTicket(i)>0 && (ulong)OrderGetInteger(ORDER_MAGIC)==(ulong)Get(k+"magic",(double)MagicFor(style))) pending=true;
      if(found && !pending) Set(k+"time",0);
      // Reconcile a fill that opened and closed before the next timer callback.
      if(!found && !pending && TimeGMT()-(datetime)Get(k+"time")>=60)
      {
         datetime since=(datetime)Get(k+"time")+(TimeTradeServer()-TimeGMT())-2;
         if(HistorySelect(since,TimeCurrent()))
         {
            double filled=0,closed=0; ulong expected=(ulong)Get(k+"magic",(double)MagicFor(style));
            for(int d=0;d<HistoryDealsTotal();d++)
            {
               ulong ticket=HistoryDealGetTicket(d);
               if((ulong)HistoryDealGetInteger(ticket,DEAL_MAGIC)!=expected) continue;
               long entry=HistoryDealGetInteger(ticket,DEAL_ENTRY);
               if(entry==DEAL_ENTRY_IN) filled+=HistoryDealGetDouble(ticket,DEAL_VOLUME);
               if(entry==DEAL_ENTRY_OUT || entry==DEAL_ENTRY_OUT_BY) closed+=HistoryDealGetDouble(ticket,DEAL_VOLUME);
            }
            if(filled>0 && closed+1e-8>=filled) { Set(k+"time",0); Audit("RECONCILED","","HISTORY_FULLY_CLOSED",style); }
         }
      }
      // No evidence of a final outcome: keep blocking, never blindly retry.
   }
}
// Partial reductions use a durable target volume, never repeated "half" orders.
void ReduceInvestment(ulong ticket,double target,string event)
{
   if(!MayWrite() || !PositionSelectByTicket(ticket))return;
   ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);string symbol=PositionGetString(POSITION_SYMBOL);
   string key=PK(id,"red."+StringSubstr(event,0,16));
   if(Get(key+".done")>0)return;
   double volume=PositionGetDouble(POSITION_VOLUME),minimum=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
   if(Get(key+".target",-1)<0)Set(key+".target",target);
   target=Get(key+".target");
   if(volume<=target+step/2){Set(key+".done",1);Set(key+".sent",0);Set("reduction_pending",0);return;}
   if(Get(key+".sent")>0)return; // Ambiguous partial fill needs reconciliation, not a blind retry.
   if(target<minimum){CloseTicket(ticket,"INVESTMENT_REDUCE_MIN_LOT");return;}
   double amount=FloorVolume(volume-target,minimum,volume,step);
   if(amount<=0)return;
   Set(key+".sent",(double)TimeGMT());Set("reduction_pending",1);GlobalVariablesFlush();
   trade.SetExpertMagicNumber((ulong)PositionGetInteger(POSITION_MAGIC));trade.SetTypeFillingBySymbol(symbol);
   bool sent=trade.PositionClosePartial(ticket,amount,20);uint code=trade.ResultRetcode();
   Audit("INVESTMENT_REDUCE",symbol,event,code,target,amount);
   if(sent && code==TRADE_RETCODE_DONE){Set(key+".sent",0);Set(key+".done",1);Set("reduction_pending",0);}
   else if(code==TRADE_RETCODE_REJECT || code==TRADE_RETCODE_MARKET_CLOSED || code==TRADE_RETCODE_INVALID_VOLUME || code==TRADE_RETCODE_TRADE_DISABLED){Set(key+".sent",0);Set("reduction_pending",0);}
   GlobalVariablesFlush();
}
void ManageInvestment(ulong ticket,int ci,datetime now)
{
   if(!PositionSelectByTicket(ticket))return;
   ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);string symbol=PositionGetString(POSITION_SYMBOL);
   double sl=PositionGetDouble(POSITION_SL),volume=PositionGetDouble(POSITION_VOLUME);
   if(sl<=0 || Get(PK(id,"r"))<=0 || PositionGetInteger(POSITION_TYPE)!=POSITION_TYPE_BUY)
   {CloseTicket(ticket,"INVESTMENT_PROTECTION");return;}
   // A missing research snapshot never removes a persisted protective obligation.
   if(ci<0)return;
   if(contracts[ci].inv_action==1){CloseTicket(ticket,"INVESTMENT_THESIS_EXIT");return;}
   if(contracts[ci].inv_action==2 && contracts[ci].inv_event!="")
      ReduceInvestment(ticket,FloorVolume(volume/2,SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),volume,SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP)),contracts[ci].inv_event);
   if(!PositionSelectByTicket(ticket))return;
   if(Get(PK(id,"early"))>0 && (now-Get(PK(id,"opened"))>=90*86400 || contracts[ci].inv_period>Get(PK(id,"period"))))
   {
      if(contracts[ci].inv_ready && contracts[ci].inv_score>=70)Set(PK(id,"early"),0);
      else {CloseTicket(ticket,"EARLY_THESIS_EXPIRED");return;}
   }
   double unit=UnitCost(ci,1,true,true),loss=StopLossMoney(symbol,true,1,PositionGetDouble(POSITION_PRICE_OPEN),sl-contracts[ci].slippage*SymbolInfoDouble(symbol,SYMBOL_POINT));
   double paid=PaidCosts(id),budget=Get(PK(id,"budget"));
   if(loss<0 || paid<0)return;
   volume=PositionGetDouble(POSITION_VOLUME);
   double maximum=FloorVolume(MathMin(MathMax(0,budget-paid)/(loss+unit),AccountInfoDouble(ACCOUNT_EQUITY)*.25/(PositionGetDouble(POSITION_PRICE_CURRENT)*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE))),SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),volume,SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP));
   if(maximum<volume-SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP)/2)
      ReduceInvestment(ticket,maximum,"reserve."+(string)(long)(now/86400));
}
void EvaluateInvestment(int ci,datetime now)
{
   // Fundamental discretionary entry reviews are not implemented by the technical agent.
   if(agent_review_required)return;
   if(!investment_enabled || !investment_ready || !investment_observed || !contracts[ci].inv_ready || !contracts[ci].inv_allowed || now-generated>60 || run_mode!="demo" || !MayWrite() || !EntryControl())return;
   if(now<contracts[ci].open || now>=contracts[ci].close-1800 || MacroBlocked(now) || !SharedEntryGate(2))return;
   string symbol=contracts[ci].symbol;
   if(contracts[ci].inv_signal=="" || Get("invsig."+contracts[ci].inv_signal)>0)return;
   if(Get("reduction_pending")>0 || contracts[ci].inv_action==1 || contracts[ci].inv_action==2)return;
   if(contracts[ci].inv_score<(contracts[ci].inv_early ? 60 : 70))return;
   if(contracts[ci].inv_early && (contracts[ci].inv_nominal>.125 || contracts[ci].inv_budget>20 || contracts[ci].inv_entry_event==""))return;
   if(contracts[ci].inv_early && Get("newsentry."+StringSubstr(contracts[ci].inv_entry_event,0,16))>0)return;
   for(int i=PositionsTotal()-1;i>=0;i--)if(PositionGetTicket(i)>0 && PositionGetString(POSITION_SYMBOL)==symbol)return;
   if(now-Get("entry."+symbol)<cooldown)return;
   int ei=FindEngine(symbol,1);if(ei<0)return;
   MqlTick q;double atr;
   if(!SymbolInfoTick(symbol,q) || now-(q.time-contracts[ci].server_offset)>30 || now<q.time-contracts[ci].server_offset-5 || !ReadValue(engines[ei].trend_atr,0,1,atr) || atr<=0)return;
   double entry=q.ask,point=SymbolInfoDouble(symbol,SYMBOL_POINT),size=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE),cs=SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE);
   if(entry<10 || q.ask<q.bid || q.ask-q.bid>MathMin(entry*.001,atr*.15) || size<=0 || cs<=0)return;
   // Recheck valuation at executable price: collector's quote is not an order limit.
   if(entry>contracts[ci].inv_fair*(contracts[ci].inv_early ? 1 : .85))return;
   if(contracts[ci].inv_early && (contracts[ci].inv_reference<=0 || now<contracts[ci].inv_news_time || now-contracts[ci].inv_news_time>72*3600 || entry-contracts[ci].inv_reference>MathMin(.05*contracts[ci].inv_reference,1.5*atr)))return;
   long mode=SymbolInfoInteger(symbol,SYMBOL_TRADE_MODE);if(mode!=SYMBOL_TRADE_MODE_FULL && mode!=SYMBOL_TRADE_MODE_LONGONLY)return;
   double stop=PriceFloor(entry-4*atr,size),cost=UnitCost(ci,1,true,true)+contracts[ci].slippage*point*cs;
   double minimum=MathMax(SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL),SymbolInfoInteger(symbol,SYMBOL_TRADE_FREEZE_LEVEL))*point;
   if(stop<=0 || entry-stop<minimum || cost>entry*cs*.05)return;
   double loss=StopLossMoney(symbol,true,1,entry,stop-contracts[ci].slippage*point),gain=(contracts[ci].inv_fair-entry)*cs;
   if(loss<=0 || (gain-cost)/(loss+cost)<1.5)return;
   double budget=SharedBudget(ci,MathMin(target_risk,contracts[ci].inv_budget));
   double v=FloorVolume(MathMin(budget/(loss+cost),AccountInfoDouble(ACCOUNT_EQUITY)*contracts[ci].inv_nominal/(entry*cs)),SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX),SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP));
   v=SharedVolume(ci,true,entry,loss+cost,cost,v);
   if(v<=0)return;
   Set("invsig."+contracts[ci].inv_signal,(double)now);
   if(contracts[ci].inv_early)Set("newsentry."+StringSubstr(contracts[ci].inv_entry_event,0,16),(double)now);
   GlobalVariablesFlush();
   SubmitOrder(2,ci,true,v,stop,0,budget,now,iTime(symbol,PERIOD_D1,1),5,200,contracts[ci].inv_early,contracts[ci].inv_period);
}

void Manage(datetime now)
{
   if(!MayWrite()) return;
   if(!foreign_exposure && WorstMargin()>AccountInfoDouble(ACCOUNT_EQUITY)*(1-reserve))
   {
      for(int style=0;style<3;style++)
         for(int i=PositionsTotal()-1;i>=0 && WorstMargin()>AccountInfoDouble(ACCOUNT_EQUITY)*(1-reserve);i--)
         { ulong t=PositionGetTicket(i); if(t>0 && Ours((ulong)PositionGetInteger(POSITION_MAGIC)) && PositionStyle()==style) CloseTicket(t,"MARGIN_RESERVE"); }
   }
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong ticket=PositionGetTicket(i); if(ticket==0) continue;
      ulong magic=(ulong)PositionGetInteger(POSITION_MAGIC); if(!Ours(magic)) continue;
      int style=PositionStyle(); ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      string symbol=PositionGetString(POSITION_SYMBOL); int ci=FindContract(symbol),ei=FindEngine(symbol,style==2 ? 1 : style);
      if(Get(PK(id,"close"))>0) { CloseTicket(ticket,"PERSISTED_EXIT"); continue; }
      double sl=PositionGetDouble(POSITION_SL),tp=PositionGetDouble(POSITION_TP),r=Get(PK(id,"r"));
      if(Get(PK(id,"policy"))==200)
      { ManageInvestment(ticket,ci,now); continue; }
      if(sl<=0 || tp<=0 || r<=0) { CloseTicket(ticket,"MISSING_PROTECTION_OR_STATE"); continue; }
      int time_exit=DTExitPolicy(style,now,(datetime)Get(PK(id,"close_time")),0,0,MacroBlocked(now,true),false,false,false);
      if(time_exit>0) { CloseTicket(ticket,time_exit==4 ? "DAY_CLOSE" : "MACRO"); continue; }
      if(ci>=0)
      {
         if(contracts[ci].earnings_close>0)
         { Set(PK(id,"earnings_close"),(double)contracts[ci].earnings_close); Set(PK(id,"earnings_end"),(double)contracts[ci].earnings_end); }
         if(contracts[ci].sec_close_until>=now) Set(PK(id,"close"),1);
      }
      if(Get(PK(id,"close"))>0 || (Get(PK(id,"earnings_close"))>0 && now>=Get(PK(id,"earnings_close"))))
      { CloseTicket(ticket,"ISSUER_EVENT"); continue; }
      if(ei<0 || ci<0) continue;
      bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
      if(style==1)
      {
         double f1,f2,s1,s2;
         if(ReadValue(engines[ei].fast,0,1,f1) && ReadValue(engines[ei].fast,0,2,f2) && ReadValue(engines[ei].slow,0,1,s1) && ReadValue(engines[ei].slow,0,2,s2))
            if(buy ? f2>=s2 && f1<s1 : f2<=s2 && f1>s1) { CloseTicket(ticket,"DAILY_CROSS"); continue; }
      }
      MqlTick tick; double atr;
      if(!SymbolInfoTick(symbol,tick) || !ReadValue(engines[ei].atr,0,1,atr) || atr<=0) continue;
      double entry=PositionGetDouble(POSITION_PRICE_OPEN),price=buy ? tick.bid : tick.ask;
      double move=(buy ? 1 : -1)*(price-entry),candidate=sl;
      double v=PositionGetDouble(POSITION_VOLUME),point=SymbolInfoDouble(symbol,SYMBOL_POINT),size=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE),unitprofit=0;
      if(!OrderCalcProfit(ORDER_TYPE_BUY,symbol,v,entry,entry+size,unitprofit) || unitprofit<=0) continue;
      double paid=PaidCosts(id); if(paid<0) continue;
      double costs=MathMax(v*contracts[ci].commission,paid+v*contracts[ci].commission/2)+MathMax(0,-PositionGetDouble(POSITION_SWAP));
      double offset=costs/unitprofit*size+contracts[ci].slippage*point;
      if(move>=r) candidate=buy ? MathMax(candidate,entry+offset) : MathMin(candidate,entry-offset);
      if(move>=1.5*r) candidate=buy ? MathMax(candidate,price-2*atr) : MathMin(candidate,price+2*atr);
      candidate=buy ? PriceFloor(candidate,size) : PriceCeil(candidate,size);
      double minimum=MathMax(SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL),SymbolInfoInteger(symbol,SYMBOL_TRADE_FREEZE_LEVEL))*point;
      bool tighten=buy ? candidate>sl+size/2 && candidate<price-minimum : candidate<sl-size/2 && candidate>price+minimum;
      if(tighten && MayWrite())
      {
         trade.SetExpertMagicNumber(magic);
         bool sent=trade.PositionModify(ticket,candidate,tp);uint code=trade.ResultRetcode();
         // CTrade's bool only confirms local request validation. Re-read the
         // broker position as well; failure is visible and retried next timer.
         bool selected=PositionSelectByTicket(ticket);
         bool confirmed=DTStopConfirmed(sent,(int)code,(int)TRADE_RETCODE_DONE,selected,
            selected ? PositionGetDouble(POSITION_SL) : 0,selected ? PositionGetDouble(POSITION_TP) : 0,candidate,tp,size);
         if(!confirmed) Audit("TRAIL_REJECT",symbol,(string)code,candidate,ticket);
         else Audit("TRAIL_CONFIRMED",symbol,"BROKER_STOP_VERIFIED",candidate,ticket);
      }
   }
}

void Evaluate(int ei,datetime now)
{
   string symbol=engines[ei].symbol; int style=engines[ei].style,ci=FindContract(symbol);
   if(ci<0 || (style==0 && !day_enabled) || (style==1 && !swing_enabled)) return;
   ENUM_TIMEFRAMES tf=engines[ei].tf;
   datetime bar=iTime(symbol,tf,0); string bk="bar."+symbol+"."+(string)style;
   if(bar<=0 || bar<=(datetime)Get(bk)) return;
   if((style==0 && !contracts[ci].pool) || !contracts[ci].verified) return;
   datetime bar_utc=bar-contracts[ci].server_offset;
   if(now<bar_utc || now-bar_utc>60) { Audit("REJECT",symbol,"STALE_ENTRY_BAR"); return; }
   MqlRates rates[]; ArraySetAsSeries(rates,true);
   if(CopyRates(symbol,tf,0,192,rates)!=192 || !FXValidHistory(rates,190)) { Audit("REJECT",symbol,"HISTORY"); return; }
   FXStrategyContext c={};
   double trend_atr,trend_adx,fast,prior_fast,slow,plus,minus,exhaust;
   if(!ReadValue(engines[ei].atr,0,1,c.atr) || !ReadValue(engines[ei].adx,0,1,c.adx) ||
      !ReadValue(engines[ei].adx,1,1,plus) || !ReadValue(engines[ei].adx,2,1,minus) ||
      !ReadValue(engines[ei].rsi,0,1,c.rsi) || !ReadValue(engines[ei].rsi,0,2,c.prior_rsi) ||
      !ReadValue(engines[ei].ma,0,1,c.entry_ma) || !ReadValue(engines[ei].ma,0,2,c.prior_ma) ||
      !ReadValue(engines[ei].bands,0,1,c.middle_band) || !ReadValue(engines[ei].bands,1,1,c.upper_band) || !ReadValue(engines[ei].bands,2,1,c.lower_band) ||
      !ReadValue(engines[ei].trend_atr,0,1,trend_atr) || !ReadValue(engines[ei].trend_adx,0,1,trend_adx) ||
      !ReadValue(engines[ei].fast,0,1,fast) || !ReadValue(engines[ei].fast,0,2,prior_fast) || !ReadValue(engines[ei].slow,0,1,slow) ||
      !ReadValue(engines[ei].exhaust,0,1,exhaust) || c.atr<=0 || trend_atr<=0) return;
   double slope=(fast-prior_fast)/trend_atr;
   c.regime=FXRegime(trend_adx,slope,20,18,.03);
   c.up=c.regime==1 && c.adx>=16 && TrendDirection(true,fast,prior_fast,slow,iClose(symbol,engines[ei].trend,1),plus,minus);
   c.down=c.regime==1 && c.adx>=16 && TrendDirection(false,fast,prior_fast,slow,iClose(symbol,engines[ei].trend,1),plus,minus);
   if(MathAbs(iClose(symbol,engines[ei].trend,1)-fast)>3*trend_atr) { c.up=false; c.down=false; }
   c.now=TimeCurrent();
   string rk="retest."+symbol+"."+(string)style+".";
   c.retest_dir=(int)Get(rk+"dir"); c.retest_bar=(datetime)Get(rk+"bar"); c.retest_level=Get(rk+"level");
   FXZone zones[];
   FXBuildZones(rates,2,c.atr,.30,2,128,true,PeriodSeconds(tf)/60,rates[1].time,zones);
   MqlRates higher[]; ArraySetAsSeries(higher,true);
   if(CopyRates(symbol,engines[ei].trend,0,96,higher)!=96) return;
   FXBuildZones(higher,2,trend_atr,.30,2,128,true,PeriodSeconds(engines[ei].trend)/60,rates[1].time,zones);
   if(style==0)
   {
      MqlRates h4[]; ArraySetAsSeries(h4,true);
      double context_atr;
      if(CopyRates(symbol,PERIOD_H4,0,96,h4)!=96 || !ReadValue(engines[ei].context_atr,0,1,context_atr)) return;
      FXBuildZones(h4,2,context_atr,.30,2,128,true,240,rates[1].time,zones);
   }
   if(contracts[ci].previous_high>0) FXAddDailyLevel(contracts[ci].previous_high,false,rates[1].time,c.atr,zones);
   if(contracts[ci].previous_low>0) FXAddDailyLevel(contracts[ci].previous_low,true,rates[1].time,c.atr,zones);
   MqlTick tick;
   if(!SymbolInfoTick(symbol,tick) || now-(tick.time-contracts[ci].server_offset)>30 || now<tick.time-contracts[ci].server_offset-5) return;
   double entry=tick.ask,point=SymbolInfoDouble(symbol,SYMBOL_POINT),size=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   if(tick.bid<=0 || tick.ask<tick.bid || size<=0) return;
   // Missing history/indicators/quote can retry inside the original 60s window.
   // Once evidence is available a real strategy decision still consumes once.
   Set(bk,(double)bar); GlobalVariablesFlush();
   c.support_index=FXNearestZone(zones,true,rates[1].close,c.atr);
   c.resistance_index=FXNearestZone(zones,false,rates[1].close,c.atr);
   FXStrategySettings settings={}; settings.rejection=style==0; settings.range=style==0;
   settings.breakout=true; settings.pullback=true; settings.retest=true; settings.strict_retest=true; settings.strict_rejection=true;
   settings.breakout_bars=20; settings.retest_max_bars=8; settings.level_tolerance=.30; settings.retest_tolerance=.1;
   settings.range_adx=18; settings.max_retest_atr=1; settings.max_retest_rsi=70; settings.min_rejection_score=65;
   FXCandidate candidates[]; FXEvaluateStrategies(settings,rates,zones,c,candidates);
   Set(rk+"dir",c.retest_dir); Set(rk+"bar",(double)c.retest_bar); Set(rk+"level",c.retest_level);
   int votes=0; bool conflict=false; int selected=FXSelectCandidate(candidates,65,votes,conflict);
   if(selected<0) { Audit("SIGNAL",symbol,conflict ? "CONFLICT" : "NO_SETUP",style,c.regime); return; }
   FXCandidate candidate=candidates[selected]; bool buy=candidate.direction>0;
   if(style==1 && (!contracts[ci].swing_ready || !contracts[ci].swing_allowed ||
      !DTSwingDirection(contracts[ci].swing_long_only,candidate.direction) || !DTSwingSetup(candidate.setup)))
      {Audit("REJECT",symbol,"SWING_MARKET_OR_SETUP",style);return;}
   if(!FXEntryRSIAllowed(candidate.direction,exhaust,70)) { Audit("REJECT",symbol,"EXHAUSTION",style); return; }
   entry=buy ? tick.ask : tick.bid;
   if(entry<10 || tick.ask<tick.bid || tick.ask-tick.bid>MathMin(entry*.001,c.atr*.15) || size<=0) return;
   double stop=candidate.stop_anchor+(buy ? -1 : 1)*.25*c.atr;
   double distance=(buy ? 1 : -1)*(entry-stop);
   if(distance< c.atr) stop=entry+(buy ? -1 : 1)*c.atr;
   stop=buy ? PriceFloor(stop,size) : PriceCeil(stop,size); distance=MathAbs(entry-stop);
   if(distance>3*c.atr || distance< c.atr*.999) { Audit("REJECT",symbol,"STOP_DISTANCE",style); return; }
   double tp=entry+(buy ? 1 : -1)*2*distance;
   if(candidate.objective>0) tp=buy ? MathMin(tp,candidate.objective) : MathMax(tp,candidate.objective);
   if(!FXCapTarget(zones,buy,entry,c.atr,.10,tp)) return;
   tp=buy ? PriceFloor(tp,size) : PriceCeil(tp,size);
   double unit_loss=StopLossMoney(symbol,buy,1,entry,stop+(buy ? -1 : 1)*contracts[ci].slippage*point),unit_gain=0;
   if(unit_loss<0 || !OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,symbol,1,entry,tp,unit_gain)) return;
   double cost=UnitCost(ci,style,buy),rr=(unit_gain-cost)/(unit_loss+cost);
   if(unit_loss+cost<=0 || rr<1.5) { Audit("REJECT",symbol,"NET_RR",style,rr); return; }
   bool event_ok=!MacroBlocked(now) && contracts[ci].entry_allowed;
   bool sentiment_ok=!(buy ? contracts[ci].veto_buy : contracts[ci].veto_sell);
   // Optional council can veto a new swing, but unavailable social evidence is not market data.
   bool council_ok=style==0 || !contracts[ci].council_ready ||
      DTCouncilAllows(style,now-contracts[ci].council_time<=60 && now>=contracts[ci].council_time-5,contracts[ci].council_buy,contracts[ci].council_sell,buy);
   // All shadow variants receive the same executable entry/stop/target, even when
   // an event/sentiment veto blocks the real candidate. No trading occurs here.
   int sf=FileOpen(BridgeFolder+"\\candidates.csv",FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ,'|');
   if(sf!=INVALID_HANDLE)
   {
      FileSeek(sf,0,SEEK_END); FileWrite(sf,(long)now,symbol,style,candidate.setup,buy ? 1 : -1,entry,stop,tp,unit_loss+cost,unit_gain-cost,event_ok ? 1 : 0,sentiment_ok ? 1 : 0,c.atr,council_ok ? 1 : 0,contracts[ci].council_ready ? "COUNCIL" : "COUNCIL_UNAVAILABLE"); FileFlush(sf); FileClose(sf);
   }
   if(now<contracts[ci].open || now>=contracts[ci].close-1800 || !event_ok || !sentiment_ok) { Audit("REJECT",symbol,"SESSION_EVENT_OR_SENTIMENT",style); return; }
   if(!council_ok) { Audit("REJECT",symbol,"SWING_COUNCIL",style,buy ? 1 : -1); return; }
   if(!(style==0 ? snapshot_ready : swing_ready) || now-generated>60 || !SharedEntryGate(style)) return;
   for(int i=PositionsTotal()-1;i>=0;i--) if(PositionGetTicket(i)>0 && PositionGetString(POSITION_SYMBOL)==symbol) return;
   if(now-Get("entry."+symbol)<cooldown) return;
   long direction=SymbolInfoInteger(symbol,SYMBOL_TRADE_MODE);
   if(direction!=SYMBOL_TRADE_MODE_FULL && direction!=(buy ? SYMBOL_TRADE_MODE_LONGONLY : SYMBOL_TRADE_MODE_SHORTONLY)) return;
   double budget=SharedBudget(ci,target_risk);
   double volume=FloorVolume(budget/(unit_loss+cost),SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(symbol,SYMBOL_VOLUME_MAX),SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP));
   volume=SharedVolume(ci,buy,entry,unit_loss+cost,cost,volume);
   if(volume<=0) { Audit("REJECT",symbol,"MINIMUM_LOT_OR_MARGIN",style); return; }
   Audit("QUALIFIED",symbol,candidate.setup,style,volume,budget);
   int strategy=candidate.setup=="S/R rejection" ? 1 : candidate.setup=="Donchian retest" ? 2 : candidate.setup=="EMA pullback" ? 3 : 4;
   if(agent_review_required)
   {
      double support_low=0,support_high=0,resistance_low=0,resistance_high=0;
      if(c.support_index>=0){support_low=zones[c.support_index].low;support_high=zones[c.support_index].high;}
      if(c.resistance_index>=0){resistance_low=zones[c.resistance_index].low;resistance_high=zones[c.resistance_index].high;}
      AgentQueue(style,ci,buy,entry,stop,tp,c.atr,budget,volume,now,rates[1].time,strategy,style==1 ? 300 : 0,
         candidate.score,support_low,support_high,resistance_low,resistance_high);
      return;
   }
   SubmitOrder(style,ci,buy,volume,stop,tp,budget,now,rates[1].time,strategy,style==1 ? 300 : 0);
}

// Sole entry side effect for day, technical swing and investment. Tests exercise
// this exact function in Strategy Tester, including its permission/risk gates.
bool SubmitOrder(int style,int ci,bool buy,double volume,double stop,double tp,double budget,
                  datetime now,datetime entry_bar,int strategy,int policy=0,bool early=false,int period=0)
{
   if(style<0 || style>2 || ci<0 || ci>=ArraySize(contracts) || !SharedEntryGate(style) ||
      run_mode!="demo" || !MayWrite() || !EntryControl(style) || now<generated || now-generated>60)return false;
   if(!AgentPermit(style,contracts[ci].symbol,buy,stop,tp,budget,entry_bar,now))return false;
   bool ready=style==0 ? day_enabled && snapshot_ready && (observation_ready || DayObservationWaiver()) :
      style==1 ? swing_enabled && swing_ready && swing_observed && contracts[ci].swing_ready && contracts[ci].swing_allowed :
      investment_enabled && investment_ready && investment_observed && contracts[ci].inv_ready && contracts[ci].inv_allowed;
   if(!ready || !contracts[ci].verified || now<contracts[ci].open || now>=contracts[ci].close-1800 || MacroBlocked(now))return false;
   if(style<2 && (!contracts[ci].entry_allowed || (buy ? contracts[ci].veto_buy : contracts[ci].veto_sell)))return false;
   if((style==1 && (!buy || policy!=300)) || (style==2 && (!buy || policy!=200 || contracts[ci].inv_action==1 || contracts[ci].inv_action==2)))return false;
   string symbol=contracts[ci].symbol;
   if(now-Get("entry."+symbol)<cooldown)return false;
   for(int i=PositionsTotal()-1;i>=0;i--)if(PositionGetTicket(i)>0 && PositionGetString(POSITION_SYMBOL)==symbol)return false;
   MqlTick q;if(!SymbolInfoTick(symbol,q) || now-(q.time-contracts[ci].server_offset)>30 || now<q.time-contracts[ci].server_offset-5)return false;
   double entry=buy ? q.ask : q.bid,point=SymbolInfoDouble(symbol,SYMBOL_POINT),unit=UnitCost(ci,style,buy);
   double loss=StopLossMoney(symbol,buy,1,entry,stop+(buy ? -1 : 1)*contracts[ci].slippage*point),gain=0;
   if(volume<=0 || !MathIsValidNumber(volume) || budget<=0 || !MathIsValidNumber(budget) || loss<=0 ||
      (buy ? stop>=q.bid : stop<=q.ask) || q.ask<q.bid || entry<=0)return false;
   if(style==2)
   {
      double cs=SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE);
      double fraction=early ? .125 : .25;
      if(tp!=0 || contracts[ci].inv_nominal<=0 || contracts[ci].inv_nominal>fraction ||
         volume*entry*cs>AccountInfoDouble(ACCOUNT_EQUITY)*contracts[ci].inv_nominal+1e-8 ||
         early!=contracts[ci].inv_early || contracts[ci].inv_score<(early ? 60 : 70) ||
         (early && (budget>20 || contracts[ci].inv_budget>20)) ||
         entry>contracts[ci].inv_fair*(early ? 1 : .85) || unit>entry*cs*.05 ||
         (contracts[ci].inv_fair-entry)*cs-unit<1.5*(loss+unit))return false;
   }
   if(style<2 && (!OrderCalcProfit(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,symbol,1,entry,tp,gain) || gain-unit<1.5*(loss+unit)))return false;
   double allowed=SharedBudget(ci,MathMin(target_risk,style==2 ? contracts[ci].inv_budget : target_risk));
   if(volume*(loss+unit)>MathMin(budget,allowed)+1e-8 || SharedVolume(ci,buy,entry,loss+unit,unit,volume)<volume-1e-8)return false;
   AgentConsume();
   string ik="intent."+(string)style+".";
   Set(ik+"magic",(double)MagicFor(style));Set(ik+"policy",policy);Set(ik+"early",early ? 1 : 0);Set(ik+"period",period);
   Set(ik+"budget",MathMin(budget,allowed));Set(ik+"close_time",style==0 ? (double)contracts[ci].close : 0);
   Set(ik+"entry_bar",(double)entry_bar);Set(ik+"strategy",strategy);Set(ik+"time",(double)now);
   Set("entries",Get("entries")+1);Set("entry."+symbol,(double)now);GlobalVariablesFlush();
   trade.SetAsyncMode(false);trade.SetExpertMagicNumber(MagicFor(style));trade.SetTypeFillingBySymbol(symbol);
   trade.SetDeviationInPoints((ulong)MathCeil(contracts[ci].slippage));
   string comment=style==2 ? (early ? "DT:INV:EARLY" : "DT:INV") : style==1 ? "DT:SWING" : "DT:DAY";
   bool sent=buy ? trade.Buy(volume,symbol,0,stop,tp,comment) : trade.Sell(volume,symbol,0,stop,tp,comment);
   uint code=trade.ResultRetcode();Audit("ORDER_RESULT",symbol,(string)code,style,volume,trade.ResultDeal());
   if(code==TRADE_RETCODE_REJECT || code==TRADE_RETCODE_INVALID || code==TRADE_RETCODE_INVALID_VOLUME || code==TRADE_RETCODE_INVALID_STOPS || code==TRADE_RETCODE_NO_MONEY || code==TRADE_RETCODE_MARKET_CLOSED || code==TRADE_RETCODE_TRADE_DISABLED)Set(ik+"time",0);
   Reconcile();return sent && code==TRADE_RETCODE_DONE;
}

bool CouncilPoolReady(datetime now)
{
   bool found=false;
   for(int i=0;i<ArraySize(contracts);i++)if(contracts[i].pool)
   {
      found=true;
      if(!contracts[i].council_ready || now-contracts[i].council_time>60 || now<contracts[i].council_time-5)return false;
   }
   return found;
}
void ExportStatus(datetime now)
{
   int f=FileOpen(BridgeFolder+"\\ea-status.tmp",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(f==INVALID_HANDLE) return;
   string s="{\"version\":\"0.3.1\",\"time\":"+(string)(long)now+",\"demo\":"+(Identity() ? "true" : "false")+",\"mode\":\""+run_mode+"\",\"orders_enabled\":"+(MayWrite() ? "true" : "false")+
      ",\"agent_review_required\":"+(agent_review_required ? "true" : "false")+",\"agent_nonce\":\""+agent_nonce+"\",\"decision_context\":\""+decision_context+"\""+
      ",\"day_observed\":"+(observation_ready ? "true" : "false")+",\"day_observation_waiver_active\":"+(DayObservationWaiver() ? "true" : "false")+
      ",\"day_observation_waiver_until\":"+(string)DayObservationWaiverUntil+
      ",\"snapshot_ready\":"+(snapshot_ready ? "true" : "false")+",\"snapshot_age\":"+(string)(long)(now-generated)+",\"foreign_exposure\":"+(foreign_exposure ? "true" : "false")+
      ",\"halt\":"+(Get("halt")>0 ? "true" : "false")+",\"unreconciled\":"+(Get("unreconciled")>0 ? "true" : "false")+
      ",\"investment_ready\":"+(investment_ready ? "true" : "false")+",\"investment_observed\":"+(investment_observed ? "true" : "false")+",\"equity\":"+DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)+",\"baseline\":"+DoubleToString(Get("baseline"),2)+
      ",\"swing_ready\":"+(swing_ready ? "true" : "false")+",\"swing_observed\":"+(swing_observed ? "true" : "false")+
      ",\"hard_halt\":"+(Get("hard_halt")>0 ? "true" : "false")+",\"week_halt\":"+(Get("week_halt")>0 ? "true" : "false")+
      ",\"day_positions\":"+(string)CountOurs(0)+",\"swing_positions\":"+(string)CountOurs(1)+",\"investment_positions\":"+(string)CountOurs(2)+
      ",\"planned_risk\":"+DoubleToString(OpenRisk(false),2)+",\"stress_loss\":"+DoubleToString(OpenStress(),2)+
      ",\"capital_loss\":"+DoubleToString(Get("capital_base",1000)-AccountInfoDouble(ACCOUNT_EQUITY),2)+
      ",\"intent_pending\":"+(PendingIntent() ? "true" : "false")+",\"council_ready\":"+(CouncilPoolReady(now) ? "true" : "false")+"}";
   FileWriteString(f,s); FileFlush(f); FileClose(f);
   FileMove(BridgeFolder+"\\ea-status.tmp",FILE_COMMON,BridgeFolder+"\\ea-status.json",FILE_REWRITE|FILE_COMMON);
}
bool DayObservationWaiver()
{
   return StringLen(decision_context)==64 && EntryControl(0) &&
      DTDayObservationWaiver((long)TimeGMT(),DayObservationWaiverUntil,0,Identity(),agent_review_required,
         DayObservationWaiverContext,decision_context,DayObservationWaiverAuthorization);
}
bool EntryControl(int style=-1)
{
   // Independent stop channel: a stopped collector cannot delay disabling entries.
   int f=FileOpen(BridgeFolder+"\\control.txt",FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
   if(f==INVALID_HANDLE) return false;
   string mode=FileReadString(f),sections=FileIsEnding(f) ? "" : FileReadString(f);
   string expiry=FileIsEnding(f) ? "" : FileReadString(f);FileClose(f);
   if(mode!="demo")return false;
   if(agent_review_required)
   {
      string bound[];if(StringSplit(expiry,'|',bound)!=2 || bound[0]!="expires")return false;
      datetime until=(datetime)StringToInteger(bound[1]);if(TimeGMT()>=until || until>TimeGMT()+86400)return false;
   }
   string allowed[];int n=StringSplit(sections,'|',allowed);
   if(n<2 || allowed[0]!="styles")return false;
   bool seen[3];ArrayInitialize(seen,false);
   for(int i=1;i<n;i++)
   {
      int k=allowed[i]=="day" ? 0 : allowed[i]=="swing" ? 1 : allowed[i]=="investment" ? 2 : -1;
      if(k<0 || seen[k])return false;seen[k]=true;
   }
   return style<0 || (style<3 && seen[style]);
}
void ExportContracts(datetime now)
{
   int f=FileOpen(BridgeFolder+"\\broker-native.tmp",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   if(f==INVALID_HANDLE) return;
   FileWrite(f,"CLOCK",(long)now,(long)TimeTradeServer(),(long)TimeCurrent());
   for(int i=0;i<ArraySize(contracts);i++)
   {
      string s=contracts[i].symbol;
      for(int day=0;day<7;day++) for(uint n=0;n<10;n++)
      {
         datetime start,finish;
         if(!SymbolInfoSessionTrade(s,(ENUM_DAY_OF_WEEK)day,n,start,finish)) break;
         FileWrite(f,"SESSION",s,day,n,(long)start,(long)finish);
      }
   }
   FileClose(f); FileMove(BridgeFolder+"\\broker-native.tmp",FILE_COMMON,BridgeFolder+"\\broker-native.txt",FILE_REWRITE|FILE_COMMON);
}
int OnInit()
{
   agent_review_required=RequireAgentReview;
   agent_nonce=(string)(long)TimeLocal()+"."+(string)GetTickCount();
   if(!Identity() || DayMagic==SwingMagic || DayMagic==InvestmentMagic || SwingMagic==InvestmentMagic) { Print("Pinned USD demo identity and unique strategy identifiers required"); return INIT_PARAMETERS_INCORRECT; }
   prefix="DT."+(string)ExpectedLogin+".";
   owner_key="SRG.Risk."+(string)ExpectedLogin+".owner";
   string beat="SRG.Risk."+(string)ExpectedLogin+".beat";
   if(!GlobalVariableCheck(owner_key)) GlobalVariableTemp(owner_key);
   double old=GlobalVariableGet(owner_key);
   if(old!=0 && GlobalVariableCheck(beat) && TimeLocal()-(datetime)GlobalVariableGet(beat)<60) return INIT_FAILED;
   locked=GlobalVariableSetOnCondition(owner_key,(double)ChartID(),old);
   if(!locked) return INIT_FAILED;
   GlobalVariableSet(beat,(double)TimeLocal());
   if(!CoreSelfTests() || !SignalRuleTests() || !FXEngineTests()) return INIT_FAILED;
   RestoreRiskLimits();MigrateInvestmentIntent();FolderCreate(BridgeFolder,FILE_COMMON); LoadSnapshot(); EventSetTimer(2);
   Audit("START","","VERSION_0.3.1"); return INIT_SUCCEEDED;
}
void OnTimer()
{
   datetime now=TimeGMT();
   if(!Identity() || !locked || GlobalVariableGet(owner_key)!=(double)ChartID())
   { Comment("DualTrade: IDENTITY / OWNERSHIP BLOCK"); return; }
   GlobalVariableSet("SRG.Risk."+(string)ExpectedLogin+".beat",(double)TimeLocal());
   LoadSnapshot(); Exposure(); UpdateLedger(now); Reconcile(); ProcessAgentClose(now); Manage(now);
   for(int i=0;i<ArraySize(engines);i++) Evaluate(i,now);
   ProcessAgentRequests(now);
   for(int rank=1;rank<=ArraySize(contracts);rank++)
      for(int i=0;i<ArraySize(contracts);i++)if(contracts[i].inv_rank==rank)EvaluateInvestment(i,now);
   ExportStatus(now); ExportContracts(now);
   Comment("DualTrade DEMO | ",run_mode,"\nDay ",CountOurs(0),"/1 | Swing ",CountOurs(1),"/1 | Investment ",CountOurs(2),"/1",
      "\nEquity $",DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2)," | Daily loss $",DoubleToString(Get("baseline")-AccountInfoDouble(ACCOUNT_EQUITY),2),
      "\nFeeds ",snapshot_ready && now-generated<=60 ? "READY" : "BLOCKED"," | Halt ",Get("halt")>0,
      " | Foreign exposure ",foreign_exposure," | Unreconciled ",Get("unreconciled")>0,
      "\nSwing ready ",swing_ready," | Investment ready ",investment_ready," | Account halt ",Get("hard_halt")>0 || Get("week_halt")>0,
      "\nOrders ",MayWrite() && run_mode=="demo" && (observation_ready || DayObservationWaiver()) ? "ENABLED" : "DISABLED");
   last_timer=now;
}
void OnTick() { /* Timer owns orchestration; broker stops remain active without it. */ }
void OnDeinit(const int reason)
{
   EventKillTimer(); GlobalVariablesFlush();
   if(locked) GlobalVariableSetOnCondition(owner_key,0,(double)ChartID());
   for(int i=0;i<ArraySize(engines);i++)
   {
      IndicatorRelease(engines[i].atr); IndicatorRelease(engines[i].adx); IndicatorRelease(engines[i].rsi);
      IndicatorRelease(engines[i].ma); IndicatorRelease(engines[i].bands); IndicatorRelease(engines[i].trend_atr);
      IndicatorRelease(engines[i].trend_adx); IndicatorRelease(engines[i].fast); IndicatorRelease(engines[i].slow); IndicatorRelease(engines[i].exhaust); IndicatorRelease(engines[i].context_atr);
   }
   Comment("");
}
