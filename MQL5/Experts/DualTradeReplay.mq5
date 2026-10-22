// Technical-only research replay. No account orders can be sent by this expert.
// Test compilation keeps the production signal, sizing, management and ledger code.
double replay_adjustment=0;
int replay_offset=10800;
double ReplayAccountInfoDouble(ENUM_ACCOUNT_INFO_DOUBLE property)
{
   double value=AccountInfoDouble(property);
   if(property==ACCOUNT_EQUITY || property==ACCOUNT_BALANCE || property==ACCOUNT_MARGIN_FREE) value+=replay_adjustment;
   return value;
}
datetime ReplayTimeGMT() { return TimeCurrent()-replay_offset; }
#define DUALTRADE_TESTER
#define AccountInfoDouble ReplayAccountInfoDouble
#define TimeGMT ReplayTimeGMT
#define OnInit ProductionOnInit
#define OnTick ProductionOnTick
#define OnTimer ProductionOnTimer
#define OnDeinit ProductionOnDeinit
#include "DualTrade.mq5"
#undef AccountInfoDouble
#undef TimeGMT
#undef OnInit
#undef OnTick
#undef OnTimer
#undef OnDeinit
input double CostMultiplier=1;
struct ReplaySession { int day,offset;datetime open,close,prior_open,prior_close; };
struct Carry { ulong id;string symbol;double cost;datetime checked; };
ReplaySession sessions[];
Carry carrying[];
int replay_session=-1,curve_handle=INVALID_HANDLE;
bool pool_frozen=false;
bool replay_valid=true;
datetime last_minute=0;

int OnInit()
{
   if(!MQLInfoInteger(MQL_TESTER) || BridgeFolder!="DualTradeTests" || CostMultiplier<1 || CostMultiplier>4) return INIT_PARAMETERS_INCORRECT;
   prefix="DT.Replay.";tester_writes=true;run_mode="demo";observation_ready=true;
   GlobalVariablesDeleteAll(prefix);
   int f=FileOpen("DualTradeTests\\universe.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   if(f==INVALID_HANDLE)return INIT_FAILED;
   while(!FileIsEnding(f))
   {
      string ticker=FileReadString(f);if(ticker=="")break;
      int i=ArraySize(contracts);ArrayResize(contracts,i+1);
      ZeroMemory(contracts[i]);
      contracts[i].ticker=ticker;contracts[i].symbol=FileReadString(f);
      contracts[i].commission=StringToDouble(FileReadString(f))*CostMultiplier;
      contracts[i].slippage=StringToDouble(FileReadString(f))*CostMultiplier;
      contracts[i].margin_rate=StringToDouble(FileReadString(f));
      contracts[i].verified=true;contracts[i].entry_allowed=true;
      SymbolSelect(contracts[i].symbol,true);AddEngine(contracts[i].symbol,0);AddEngine(contracts[i].symbol,1);
   }
   FileClose(f);
   f=FileOpen("DualTradeTests\\sessions.csv",FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   if(f==INVALID_HANDLE)return INIT_FAILED;
   while(!FileIsEnding(f))
   {
      string value=FileReadString(f);if(value=="")break;
      int i=ArraySize(sessions);ArrayResize(sessions,i+1);sessions[i].day=(int)StringToInteger(value);
      sessions[i].open=(datetime)StringToInteger(FileReadString(f));sessions[i].close=(datetime)StringToInteger(FileReadString(f));
      sessions[i].offset=(int)StringToInteger(FileReadString(f));sessions[i].prior_open=(datetime)StringToInteger(FileReadString(f));sessions[i].prior_close=(datetime)StringToInteger(FileReadString(f));
   }
   FileClose(f);
   if(ArraySize(contracts)<1 || ArraySize(sessions)<1)return INIT_FAILED;
   f=FileOpen(BridgeFolder+"\\control.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);FileWriteString(f,"demo\nstyles|day|swing\n");FileClose(f);
   string tag=CostMultiplier==1 ? "base" : "stress";
   curve_handle=FileOpen(BridgeFolder+"\\replay-"+tag+"-curve.csv",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   FileWrite(curve_handle,"utc","equity","balance","day_floating","swing_floating");
   Set("baseline",1000);Set("day",NyDay(ReplayTimeGMT()));Set("cash_checked",(double)(TimeCurrent()+1));
   return INIT_SUCCEEDED;
}

int CarryIndex(ulong id,string symbol,datetime now)
{
   for(int i=0;i<ArraySize(carrying);i++)if(carrying[i].id==id)return i;
   int i=ArraySize(carrying);ArrayResize(carrying,i+1);carrying[i].id=id;carrying[i].symbol=symbol;carrying[i].checked=now;carrying[i].cost=0;return i;
}
double EstimatedDealFee(ulong deal)
{
   string symbol=HistoryDealGetString(deal,DEAL_SYMBOL);int ci=FindContract(symbol);if(ci<0)return 0;
   double volume=HistoryDealGetDouble(deal,DEAL_VOLUME);
   // The same conservative commission and extra slippage allowance used in sizing.
   return volume*(contracts[ci].commission/2+contracts[ci].slippage*SymbolInfoDouble(symbol,SYMBOL_POINT)*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE));
}
void RefreshReplayCosts()
{
   double native=0,model=0;datetime server=TimeCurrent();
   if(HistorySelect(0,server))for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong d=HistoryDealGetTicket(i);if(!Ours((ulong)HistoryDealGetInteger(d,DEAL_MAGIC)))continue;
      native+=HistoryDealGetDouble(d,DEAL_COMMISSION)+HistoryDealGetDouble(d,DEAL_FEE)+HistoryDealGetDouble(d,DEAL_SWAP);
      model+=EstimatedDealFee(d);
   }
   for(int i=0;i<PositionsTotal();i++)
   {
      ulong ticket=PositionGetTicket(i);if(ticket==0 || !Ours((ulong)PositionGetInteger(POSITION_MAGIC)))continue;
      string symbol=PositionGetString(POSITION_SYMBOL);int ci=FindContract(symbol);ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      native+=PositionGetDouble(POSITION_SWAP);int j=CarryIndex(id,symbol,server);
      long prior=(long)carrying[j].checked/86400,current=(long)server/86400;
      for(long day=prior;day<current;day++)
      {
         MqlDateTime dt;TimeToStruct((datetime)(day*86400),dt);
         int weight=dt.day_of_week==5 ? 3 : dt.day_of_week>=1 && dt.day_of_week<=4 ? 1 : 0;
         bool buy=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY;
         double rate=SymbolInfoDouble(symbol,buy ? SYMBOL_SWAP_LONG : SYMBOL_SWAP_SHORT);
         carrying[j].cost+=weight*MathMax(0,-rate)/100/360*PositionGetDouble(POSITION_PRICE_CURRENT)*PositionGetDouble(POSITION_VOLUME)*SymbolInfoDouble(symbol,SYMBOL_TRADE_CONTRACT_SIZE)*CostMultiplier;
      }
      carrying[j].checked=server;
      if(ci>=0)
      {
         double price=PositionGetDouble(POSITION_PRICE_CURRENT);
         contracts[ci].finance_long=MathMax(0,-SymbolInfoDouble(symbol,SYMBOL_SWAP_LONG))/100/360*price*CostMultiplier;
         contracts[ci].finance_short=MathMax(0,-SymbolInfoDouble(symbol,SYMBOL_SWAP_SHORT))/100/360*price*CostMultiplier;
      }
   }
   for(int i=0;i<ArraySize(carrying);i++)model+=carrying[i].cost;
   replay_adjustment=-native-model;
}

void FreezePool()
{
   double ranks[];ArrayResize(ranks,ArraySize(contracts));
   int places=3;
   for(int i=0;i<ArraySize(contracts);i++)
   {
      contracts[i].pool=false;ranks[i]=-1;
      for(int p=0;p<PositionsTotal();p++)if(PositionGetTicket(p)>0 && PositionGetString(POSITION_SYMBOL)==contracts[i].symbol)
      {contracts[i].pool=true;places--;break;}
      MqlTick q;double atr;int ei=FindEngine(contracts[i].symbol,0);
      if(!contracts[i].pool && SymbolInfoTick(contracts[i].symbol,q) && q.bid>=10 && q.ask>=q.bid && ReadValue(engines[ei].atr,0,1,atr) && atr>0 && q.ask-q.bid<=MathMin(q.ask*.001,atr*.15))ranks[i]=atr/q.bid;
   }
   for(int p=0;p<places;p++)
   {
      int best=ArrayMaximum(ranks);if(best<0 || ranks[best]<0)break;
      contracts[best].pool=true;ranks[best]=-1;
   }
   pool_frozen=true;
}
void OnTick()
{
   if(!MQLInfoInteger(MQL_TESTER))return;
   datetime server=TimeCurrent();if(server/60==last_minute)return;last_minute=server/60;
   // Calendar records carry the broker's assumed EET/EEST historical clock offset.
   int match=-1;
   for(int i=0;i<ArraySize(sessions);i++)if(NyDay(server-sessions[i].offset)==sessions[i].day){match=i;break;}
   if(match<0)return;
   replay_offset=sessions[match].offset;datetime now=ReplayTimeGMT();
   if(replay_session!=match)
   {
      replay_session=match;pool_frozen=false;
      for(int i=0;i<ArraySize(contracts);i++)
      {
         contracts[i].pool=false;contracts[i].open=sessions[match].open;contracts[i].close=sessions[match].close;contracts[i].server_offset=replay_offset;
         MqlRates prior[];
         int count=CopyRates(contracts[i].symbol,PERIOD_M15,sessions[match].prior_open+replay_offset,sessions[match].prior_close+replay_offset-1,prior);
         contracts[i].previous_high=0;contracts[i].previous_low=0;
         for(int k=0;k<count;k++){contracts[i].previous_high=MathMax(contracts[i].previous_high,prior[k].high);contracts[i].previous_low=contracts[i].previous_low==0 ? prior[k].low : MathMin(contracts[i].previous_low,prior[k].low);}
      }
   }
   // Refresh negative interest estimates for every symbol, including future entries.
   for(int i=0;i<ArraySize(contracts);i++)
   {
      MqlTick q;if(!SymbolInfoTick(contracts[i].symbol,q))continue;
      contracts[i].finance_long=MathMax(0,-SymbolInfoDouble(contracts[i].symbol,SYMBOL_SWAP_LONG))/100/360*q.ask*CostMultiplier;
      contracts[i].finance_short=MathMax(0,-SymbolInfoDouble(contracts[i].symbol,SYMBOL_SWAP_SHORT))/100/360*q.bid*CostMultiplier;
   }
   RefreshReplayCosts();Exposure();
   for(int i=0;i<ArraySize(contracts);i++)if(contracts[i].earnings_close!=0 || contracts[i].sec_close_until!=0)
   {replay_valid=false;Print("REPLAY_INVALID unexpected issuer event in technical baseline");TesterStop();return;}
   // This replay drives from symbol ticks; use a continuous verified boundary only
   // within the same session. Overnight equity marks are unavailable at midnight.
   if((int)Get("day")!=NyDay(now))
   {
      if(ReplayAccountInfoDouble(ACCOUNT_EQUITY)<=Get("baseline")-loss_limit){Set("halt",1);Set("must_flat",1);}
      if(Get("must_flat")==0){Set("day",NyDay(now));Set("baseline",ReplayAccountInfoDouble(ACCOUNT_EQUITY));Set("entries",0);Set("halt",0);}
   }
   last_timer=now;UpdateLedger(now);Reconcile();Manage(now);
   if(!pool_frozen && now>=sessions[match].open)FreezePool();
   snapshot_ready=true;generated=now;
   // Technical-only history has no point-in-time political evidence. Bypass only
   // in this tester-only baseline; the report explicitly marks council unavailable.
   for(int i=0;i<ArraySize(contracts);i++){contracts[i].council_ready=true;contracts[i].council_buy=true;contracts[i].council_sell=true;contracts[i].council_time=now;}
   for(int i=0;i<ArraySize(engines);i++)Evaluate(i,now);
   RefreshReplayCosts();
   double floating[2]={0,0};
   for(int i=0;i<PositionsTotal();i++)if(PositionGetTicket(i)>0 && Ours((ulong)PositionGetInteger(POSITION_MAGIC)))floating[Style((ulong)PositionGetInteger(POSITION_MAGIC))]+=PositionGetDouble(POSITION_PROFIT);
   FileWrite(curve_handle,(long)now,ReplayAccountInfoDouble(ACCOUNT_EQUITY),ReplayAccountInfoDouble(ACCOUNT_BALANCE),floating[0],floating[1]);
}
double OnTester()
{
   RefreshReplayCosts();string tag=CostMultiplier==1 ? "base" : "stress";
   int f=FileOpen(BridgeFolder+"\\replay-"+tag+"-deals.csv",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   FileWrite(f,"position_id","server_time","magic","entry","symbol","volume","price","gross_pnl","estimated_fee","deal_type","comment");
   if(HistorySelect(0,TimeCurrent()))for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong d=HistoryDealGetTicket(i);if(!Ours((ulong)HistoryDealGetInteger(d,DEAL_MAGIC)))continue;
      FileWrite(f,HistoryDealGetInteger(d,DEAL_POSITION_ID),HistoryDealGetInteger(d,DEAL_TIME),HistoryDealGetInteger(d,DEAL_MAGIC),HistoryDealGetInteger(d,DEAL_ENTRY),HistoryDealGetString(d,DEAL_SYMBOL),HistoryDealGetDouble(d,DEAL_VOLUME),HistoryDealGetDouble(d,DEAL_PRICE),HistoryDealGetDouble(d,DEAL_PROFIT),EstimatedDealFee(d),HistoryDealGetInteger(d,DEAL_TYPE),HistoryDealGetString(d,DEAL_COMMENT));
   }
   FileClose(f);
   f=FileOpen(BridgeFolder+"\\replay-"+tag+"-carry.csv",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   FileWrite(f,"position_id","estimated_financing");for(int i=0;i<ArraySize(carrying);i++)FileWrite(f,carrying[i].id,carrying[i].cost);FileClose(f);
   if(curve_handle!=INVALID_HANDLE){FileFlush(curve_handle);FileClose(curve_handle);curve_handle=INVALID_HANDLE;}
   Print("REPLAY_RESULT cost_multiplier=",CostMultiplier," equity=",ReplayAccountInfoDouble(ACCOUNT_EQUITY));
   f=FileOpen(BridgeFolder+"\\replay-"+tag+"-valid.json",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   // This adapter lacks the new point-in-time swing/benchmark/shared-book feed.
   // Never label a current-source diagnostic as the historical 0.1.4 model.
   FileWriteString(f,"{\"valid\":false,\"technical_consistency\":"+(replay_valid ? "true" : "false")+",\"version\":\"0.3.0\",\"reason\":\"THREE_BOOK_REPLAY_NOT_VALIDATED\"}");FileClose(f);
   return ReplayAccountInfoDouble(ACCOUNT_EQUITY)-1000;
}
void OnDeinit(const int reason){if(curve_handle!=INVALID_HANDLE)FileClose(curve_handle);}
