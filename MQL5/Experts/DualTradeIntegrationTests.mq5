// This harness refuses to initialize outside MT5 Strategy Tester.
// It exercises production routines with native simulated positions, never demo/live orders.
#define DUALTRADE_TESTER
#define OnInit ProductionOnInit
#define OnTick ProductionOnTick
#define OnTimer ProductionOnTimer
#define OnDeinit ProductionOnDeinit
#include "DualTrade.mq5"
#undef OnInit
#undef OnTick
#undef OnTimer
#undef OnDeinit
int test_stage=0,test_passes=0,automatic_style=0;
bool test_failed=false;
void TestAgentFlow(datetime now,double volume,double entry,double stop,double tp)
{
   agent_review_required=true;decision_context="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
   int ctrl=FileOpen(BridgeFolder+"\\control.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(ctrl!=INVALID_HANDLE){FileWriteString(ctrl,"demo\nstyles|day\nexpires|"+(string)(long)(now+7200)+"\n");FileClose(ctrl);}
   Assert(!SubmitOrder(0,0,true,volume,stop,tp,40,now,now,3),"agent gate blocks native entry without a bound review");
   Assert(!DTAgentWindow(now,now,now+56,now) && !DTAgentWindow(now,now,now+5,now+6),"agent cannot extend or reuse expired decision window");
   Assert(!DTAgentBudget(41,40,1000) && !DTAgentBudget(30,40,500),"agent cannot raise dollar or equity risk caps");
   Assert(!DTAgentPrice(true,entry,entry+1,stop,tp,1),"agent refuses a chased entry price");
   ArrayResize(agent_pending,1);ZeroMemory(agent_pending[0]);
   agent_pending[0].id="tester.0.0."+(string)(long)now;agent_pending[0].context=decision_context;
   agent_pending[0].symbol=_Symbol;agent_pending[0].style=0;agent_pending[0].strategy=3;agent_pending[0].policy=0;
   agent_pending[0].buy=true;agent_pending[0].created=now;agent_pending[0].expires=now+50;agent_pending[0].bar=now;
   agent_pending[0].entry=entry;agent_pending[0].stop=stop;agent_pending[0].target=tp;
   agent_pending[0].atr=2;agent_pending[0].ceiling=40;agent_pending[0].max_volume=10*volume;
   FolderCreate(BridgeFolder+"\\agent-decisions",FILE_COMMON);
   int f=FileOpen(BridgeFolder+"\\agent-decisions\\"+agent_pending[0].id+".txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(f!=INVALID_HANDLE){FileWriteString(f,"AGENT|1|"+agent_pending[0].id+"|"+decision_context+"|"+(string)(long)now+"|"+(string)(long)(now+50)+"|APPROVE|1|"+DoubleToString(entry,10)+"|"+DoubleToString(stop,10)+"|"+DoubleToString(tp,10)+"|OPEN\n");FileClose(f);}
   ProcessAgentRequests(now);
   Assert(CountOurs(0)==1,"agent-approved request enters through production SubmitOrder");
   Assert(OpenRisk(false)<=1.00001 && OpenRisk(false)>0,"agent budget sizes native volume below the forty dollar ceiling");
   Assert(agent_pending[0].done && Get("agent.used."+agent_pending[0].id)>0,"agent approval is consumed durably before execution");
   ProcessAgentRequests(now);Assert(CountOurs(0)==1,"replayed agent approval cannot create a duplicate position");
   ulong ticket=PositionGetTicket(0),id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
   double actual_volume=PositionGetDouble(POSITION_VOLUME);
   f=FileOpen(BridgeFolder+"\\agent-close.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(f!=INVALID_HANDLE){FileWriteString(f,"CLOSE|1|tester|"+decision_context+"|"+(string)(long)now+"|"+(string)(long)(now+50)+"|"+(string)(ticket+99)+"|"+(string)id+"|"+_Symbol+"|"+DoubleToString(actual_volume,8)+"|THESIS_INVALIDATED\n");FileClose(f);}
   ProcessAgentClose(now);Assert(CountOurs(0)==1,"agent exit cannot target a changed position ticket");
   f=FileOpen(BridgeFolder+"\\agent-close.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(f!=INVALID_HANDLE){FileWriteString(f,"CLOSE|1|tester|"+decision_context+"|"+(string)(long)now+"|"+(string)(long)(now+50)+"|"+(string)ticket+"|"+(string)id+"|"+_Symbol+"|"+DoubleToString(actual_volume,8)+"|THESIS_INVALIDATED\n");FileClose(f);}
   ProcessAgentClose(now);Assert(CountOurs()==0,"agent thesis exit closes only the bound native-owned position");
   agent_review_required=false;Set("entry."+_Symbol,0);Set("entries",0);Set("baseline",AccountInfoDouble(ACCOUNT_EQUITY));
   ctrl=FileOpen(BridgeFolder+"\\control.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(ctrl!=INVALID_HANDLE){FileWriteString(ctrl,"demo\nstyles|day|swing|investment\n");FileClose(ctrl);}
}
void Assert(bool condition,string name)
{
   if(condition) { test_passes++; Print("PASS ",name); }
   else { test_failed=true; Print("FAIL ",name); TesterStop(); }
}
int OnInit()
{
   if(!MQLInfoInteger(MQL_TESTER) || (BridgeFolder!="DualTradeTests" && StringFind(BridgeFolder,"DualTradeTests030")!=0)) { Print("TESTER ONLY: initialization refused"); return INIT_FAILED; }
   prefix="DT.Test."; run_mode="observe";
   string wa="user-20261022-day-observation-waiver";
   Assert(DTDayObservationWaiver(1792680000,1792697100,0,true,true,"ctx","ctx",wa),"dated demo-only observation exception permits its exact scope");
   Assert(!DTDayObservationWaiver(1792697100,1792697100,0,true,true,"ctx","ctx",wa),"observation exception ends exclusively at 22:25");
   Assert(!DTDayObservationWaiver(1792680000,1792697101,0,true,true,"ctx","ctx",wa),"observation exception cannot extend its deadline");
   Assert(!DTDayObservationWaiver(1792680000,1792697100,1,true,true,"ctx","ctx",wa),"observation exception cannot authorize swing");
   Assert(!DTDayObservationWaiver(1792680000,1792697100,0,false,true,"ctx","ctx",wa),"observation exception cannot authorize a real or wrong account");
   Assert(!DTDayObservationWaiver(1792680000,1792697100,0,true,true,"old","ctx",wa),"observation exception rejects changed source context");
   Assert(!DTDayObservationWaiver(1792680000,1792697100,0,true,false,"ctx","ctx",wa),"observation exception retains mandatory agent review");
   // Legacy flow tests below explicitly exercise the ungated native path.
   // Additional agent-gated order/exit tests are run in stage seven.
   agent_review_required=false;agent_nonce="tester";
   GlobalVariablesDeleteAll(prefix);
   ArrayResize(contracts,1);ZeroMemory(contracts[0]);contracts[0].symbol=_Symbol;contracts[0].verified=true;
   contracts[0].commission=.4; contracts[0].margin_rate=.2;contracts[0].slippage=20;
   Set("baseline",AccountInfoDouble(ACCOUNT_EQUITY));Set("day",NyDay(TimeGMT()));
   Set("halt",0);Set("must_flat",0);Set("unreconciled",0);Set("cash_checked",(double)TimeCurrent());
   trade.SetExpertMagicNumber(DayMagic);trade.SetTypeFillingBySymbol(_Symbol);
   AddEngine(_Symbol,0);
   Assert(CoreSelfTests() && SignalRuleTests() && FXEngineTests(),"embedded signal selftests");
   Assert(!MayWrite(),"production identity and permission fail closed");
   return INIT_SUCCEEDED;
}
void OnTick()
{
   if(!MQLInfoInteger(MQL_TESTER) || test_failed || test_stage>=9) return;
   MqlTick q;if(!SymbolInfoTick(_Symbol,q) || q.bid<10 || q.ask<q.bid) return;
   double size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE),volume=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double distance=MathMax(2,10*SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*SymbolInfoDouble(_Symbol,SYMBOL_POINT));
   if(test_stage==0)
   {
      double loss=StopLossMoney(_Symbol,true,volume,q.ask,q.ask-distance),margin;
      Assert(loss>0 && loss<40,"native profit calculation and minimum lot risk");
      Assert(OrderCalcMargin(ORDER_TYPE_BUY,_Symbol,volume,q.ask,margin) && margin>0,"native margin calculation");
      if(!trade.Buy(volume,_Symbol,0,PriceFloor(q.ask-distance,size),PriceCeil(q.ask+2*distance,size))) return;
      if(trade.ResultRetcode()!=TRADE_RETCODE_DONE) return;
      Assert(CountOurs(0)==1 && CountOurs(1)==0,"day and swing slot ownership");
      test_stage=1;
   }
   else if(test_stage==1)
   {
      ulong ticket=PositionGetTicket(0); if(ticket==0) { Assert(false,"position exists after fill");return; }
      Assert(PositionGetDouble(POSITION_SL)>0 && PositionGetDouble(POSITION_TP)>0,"native stop and target attached");
      Assert(OpenRisk(false)>0 && WorstMargin()>0,"production open-risk and margin inspect native fill");
      ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);Set(PK(id,"r"),distance);
      GlobalVariablesFlush();Assert(Get(PK(id,"r"))==distance,"stable position identifier persistence");
      Set("baseline",AccountInfoDouble(ACCOUNT_EQUITY)+201);UpdateLedger(TimeGMT());
      Assert(Get("halt")==1 && Get("must_flat")==1,"equity loss latches unresolved closure");
      Set("baseline",AccountInfoDouble(ACCOUNT_EQUITY));Set("halt",0);Set("must_flat",0);
      if(trade.PositionClose(ticket) && trade.ResultRetcode()==TRADE_RETCODE_DONE) test_stage=2;
   }
   else if(test_stage==2)
   {
      Assert(CountOurs()==0,"day position closure reconciles");
      trade.SetExpertMagicNumber(SwingMagic);
      if(!trade.Sell(volume,_Symbol,0,PriceCeil(q.bid+distance,size),PriceFloor(q.bid-2*distance,size)))return;
      if(trade.ResultRetcode()==TRADE_RETCODE_DONE)test_stage=3;
   }
   else if(test_stage==3)
   {
      ulong ticket=PositionGetTicket(0);if(ticket==0){Assert(false,"short fill exists");return;}
      Assert(CountOurs(1)==1 && CountOurs(0)==0 && PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_SELL,"native swing short ownership");
      Assert(DTExitPolicy(1,30*86400,10000,0,0,true,false,false,false)==0,"swing survives ordinary closes macro and thirty days");
      Assert(DTExitPolicy(1,30*86400,10000,100,200,false,false,false,false)==3,"missed earnings closure remains required");
      if(trade.PositionClose(ticket) && trade.ResultRetcode()==TRADE_RETCODE_DONE)test_stage=4;
   }
   else if(test_stage==4)
   {
      Assert(CountOurs()==0,"swing close reconciles");test_stage=5;
   }
   else if(test_stage==5)
   {
      tester_writes=true;
      double baseline=AccountInfoDouble(ACCOUNT_EQUITY); datetime now=TimeGMT();
      // Restart across midnight retains the old baseline and blocks new entries.
      Set("baseline",baseline+100);Set("day",NyDay(now-86400));last_timer=0;
      UpdateLedger(now);
      Assert(Get("unreconciled")==1 && Get("baseline")==baseline+100,"disconnected midnight cannot hide prior loss");
      Set("baseline",baseline+201);UpdateLedger(now);
      Assert(Get("halt")==1,"loss checked before disconnected day reset");
      Set("baseline",baseline);Set("day",NyDay(now));Set("halt",0);Set("must_flat",0);Set("unreconciled",0);
      // An unknown order outcome remains latched even after its timeout.
      Set("intent.0.time",(double)(now+120));Reconcile();
      Assert(Get("intent.0.time")>0,"unknown fill outcome is never blindly retried");
      Set("intent.0.time",0);
      trade.SetExpertMagicNumber(DayMagic);
      Assert(trade.Buy(volume,_Symbol,0,PriceFloor(q.ask-distance,size),PriceCeil(q.ask+4*distance,size)),"create partial-volume fill fixture");
      Set("intent.0.time",(double)now);Set("intent.0.budget",40);Set("intent.0.close_time",(double)(now+3600));
      Set("intent.0.entry_bar",(double)iTime(_Symbol,PERIOD_M15,1));Set("intent.0.strategy",3);
      Reconcile();
      ulong ticket=PositionGetTicket(0);ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      Assert(Get("intent.0.time")==0 && Get(PK(id,"original_risk"))>0,"smaller fill reconciles actual risk and clears completed intent");
      GlobalVariablesFlush();last_timer=0;run_mode="stop";snapshot_ready=false;generated=0;
      double saved=Get(PK(id,"r"));Manage(now);
      Assert(CountOurs(0)==1 && Get(PK(id,"r"))==saved,"restart retains stable state with research offline");
      // Protection must run in stop mode with the collector disconnected.
      Set(PK(id,"close_time"),(double)(now+299));Manage(now);
      Assert(CountOurs()==0,"day closure continues while stopped and feeds offline");
      trade.SetExpertMagicNumber(SwingMagic);
      Assert(trade.Sell(volume,_Symbol,0,PriceCeil(q.bid+distance,size),PriceFloor(q.bid-4*distance,size)),"create swing recovery fixture");
      ticket=PositionGetTicket(0);id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      Set(PK(id,"r"),distance);Set(PK(id,"close_time"),(double)(now-30*86400));
      Manage(now);Assert(CountOurs(1)==1,"offline swing survives old session close after restart");
      // A closure rejected by the write gate remains an obligation after reconnect.
      tester_writes=false;Set(PK(id,"close"),1);Manage(now);
      Assert(CountOurs(1)==1 && Get(PK(id,"close"))==1,"unavailable execution preserves required closure");
      tester_writes=true;Manage(now);Assert(CountOurs()==0,"persisted closure retries after execution returns");
      // An oversized/risky fill is closed through the production reconciliation path.
      trade.SetExpertMagicNumber(DayMagic);
      Assert(trade.Buy(volume,_Symbol,0,PriceFloor(q.ask-distance,size),PriceCeil(q.ask+4*distance,size)),"create over-budget fill fixture");
      Set("intent.0.time",(double)now);Set("intent.0.budget",.001);Reconcile();
      Assert(CountOurs()==0,"over-budget fill closes instead of increasing planned risk");
      Set("intent.0.time",0);
      // Foreign exposure pauses entries but cannot be closed by the bot.
      trade.SetExpertMagicNumber(987654);
      Assert(trade.Buy(volume,_Symbol,0,PriceFloor(q.ask-distance,size),PriceCeil(q.ask+4*distance,size)),"create foreign-position fixture");
      ticket=PositionGetTicket(0);Exposure();
      Assert(foreign_exposure && !CloseTicket(ticket,"TEST_FOREIGN"),"foreign position is detected and left untouched");
      trade.PositionClose(ticket);Exposure();
      Assert(!foreign_exposure && CountOurs()==0,"foreign closure restores clean exposure");
      tester_writes=false;Assert(!MayWrite() && !Identity(),"normal account identity and write gate remain closed");
      test_stage=6;
   }
   else if(test_stage==6)
   {
      tester_writes=true;datetime now=TimeGMT();
      int bridge_test=FileOpen(BridgeFolder+"\\snapshot.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      string header="HEADER|4|"+(string)(long)now+"|observe|0|"+(string)ExpectedLogin+"|"+ExpectedServer+"|USD|0|40|100|80|200|0.2|5|3600\n";
      string contract="CONTRACT|AAPL|"+_Symbol+"|1|0.4|20|0.2|0|0|0|0|0|0|0|0|0|0|0|0|0|0|USD|1\n";
      if(bridge_test!=INVALID_HANDLE)
      {
         FileWriteString(bridge_test,header+"MODES|1|0|0|1|1|0|1\nRISK|0.04|0.08|0.06|0.2|1000|1000|600|4|2|0.08|0.2\n"+contract+"COUNCIL|"+_Symbol+"|0|0|0|"+(string)(long)now+"\nGROUP|"+_Symbol+"|technology\nSWING|"+_Symbol+"|1|1|1\nINVESTMENT|"+_Symbol+"|1|0|100|1000|0|0.25|40|0||20260630|0|1||0|0|testsignal\nEND|8\n");FileClose(bridge_test);
      }
      Assert(LoadSnapshot(),"protocol four accepts independent investment and swing records");
      Assert(swing_ready && !swing_observed && swing_enabled,"swing readiness and observation are independent");
      Assert(investment_ready && !snapshot_ready && !day_enabled && investment_enabled,"investment readiness is independent of intraday feeds");
      bridge_test=FileOpen(BridgeFolder+"\\snapshot.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(bridge_test!=INVALID_HANDLE){FileWriteString(bridge_test,header+"MODES|0|1|1|0|0|0|0\nEND|999\n");FileClose(bridge_test);}
      Assert(!LoadSnapshot(),"incomplete bridge snapshot is rejected");
      Assert(investment_ready && !day_enabled,"rejected bridge cannot partly change execution modes");
      contracts[0].inv_fair=q.ask+100;contracts[0].finance_long=0;contracts[0].inv_ready=true;contracts[0].inv_score=100;
      contracts[0].inv_period=20260630;contracts[0].inv_action=0;
      trade.SetExpertMagicNumber(SwingMagic);
      Assert(trade.Buy(2*volume,_Symbol,0,PriceFloor(q.ask-distance,size),0,"DT:INV"),"investment opens with broker stop and no technical target");
      Set("intent.1.time",(double)now);Set("intent.1.budget",40);Set("intent.1.policy",200);Set("intent.1.early",0);Set("intent.1.period",20260630);
      Reconcile();
      Assert(CountOurs(2)==1 && CountOurs(1)==0 && Get("intent.1.time")==0 && Get("intent.2.time")==0,"legacy investment intent migrates to independent slot without TP requirement");
      ulong ticket=PositionGetTicket(0),id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      Assert(Get(PK(id,"policy"))==200 && PositionGetDouble(POSITION_TP)==0,"investment policy persists on stable identifier");
      contracts[0].earnings_close=now-1;contracts[0].earnings_end=now+86400;
      Set(PK(id,"close_time"),(double)(now-86400));Manage(now);
      Assert(CountOurs(2)==1,"investment holds through earnings and old session close");
      contracts[0].inv_action=2;contracts[0].inv_event="0123456789abcdef0123456789abcdef";Manage(now);
      PositionSelectByTicket(ticket);
      Assert(MathAbs(PositionGetDouble(POSITION_VOLUME)-volume)<1e-8,"material negative event halves native position");
      Manage(now);PositionSelectByTicket(ticket);
      Assert(MathAbs(PositionGetDouble(POSITION_VOLUME)-volume)<1e-8,"duplicate event cannot halve the position twice");
      Assert(Get("reduction_pending")==0,"completed reduction clears pending outcome");
      contracts[0].inv_action=1;Manage(now);
      Assert(CountOurs()==0,"severe material event closes native investment");
      contracts[0].inv_action=0;contracts[0].inv_ready=false;
      Assert(trade.Buy(volume,_Symbol,0,PriceFloor(q.ask-distance,size),0,"DT:INV:EARLY"),"create early investment expiry fixture");
      ticket=PositionGetTicket(0);id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      Set(PK(id,"policy"),200);Set(PK(id,"r"),distance);Set(PK(id,"early"),1);Set(PK(id,"opened"),(double)(now-91*86400));Set(PK(id,"period"),20260630);
      Manage(now);Assert(CountOurs()==0,"unconfirmed early thesis expires after ninety days");
      tester_writes=false;test_stage=7;
   }
   else if(test_stage==7)
   {
      datetime now=TimeGMT();tester_writes=true;
      // Controlled inputs only; call the same production entry and fill routines.
      run_mode="demo";generated=now;snapshot_ready=true;observation_ready=true;
      day_enabled=true;swing_enabled=true;swing_ready=true;swing_observed=true;
      investment_enabled=true;investment_ready=true;investment_observed=true;
      contracts[0].open=now-60;contracts[0].close=now+7200;contracts[0].server_offset=0;
      contracts[0].entry_allowed=true;contracts[0].swing_ready=true;contracts[0].swing_allowed=true;
      contracts[0].swing_long_only=true;contracts[0].inv_ready=true;contracts[0].inv_allowed=true;
      contracts[0].inv_budget=40;contracts[0].inv_fair=q.ask+100;contracts[0].inv_action=0;contracts[0].inv_nominal=.25;
      contracts[0].earnings_close=0;contracts[0].sec_close_until=0;contracts[0].finance_long=0;
      Set("entry."+_Symbol,0);Set("entries",0);Set("halt",0);Set("must_flat",0);Set("unreconciled",0);
      Set("baseline",AccountInfoDouble(ACCOUNT_EQUITY));Set("week_base",AccountInfoDouble(ACCOUNT_EQUITY));
      int control=FileOpen(BridgeFolder+"\\control.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(control!=INVALID_HANDLE){FileWriteString(control,"demo\nstyles|day|swing|investment\n");FileClose(control);}
      double stop=PriceFloor(q.ask-distance,size),tp=automatic_style==2 ? 0 : PriceCeil(q.ask+4*distance,size);
      int policy=automatic_style==2 ? 200 : automatic_style==1 ? 300 : 0;
      if(automatic_style==0)
      {
         control=FileOpen(BridgeFolder+"\\control.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
         if(control!=INVALID_HANDLE){FileWriteString(control,"demo\nstyles|swing\n");FileClose(control);}
         Assert(!EntryControl(0) && EntryControl(1) && !SubmitOrder(0,0,true,volume,stop,tp,40,now,now,3),"style selection immediately blocks stale day snapshot");
         control=FileOpen(BridgeFolder+"\\control.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
         if(control!=INVALID_HANDLE){FileWriteString(control,"demo\nstyles|day|swing|investment\n");FileClose(control);}
      }
      if(automatic_style==1)
      {
         swing_observed=false;
         Assert(!SubmitOrder(1,0,true,volume,stop,tp,40,now,now,3,300) && CountOurs()==0,"automatic swing cannot bypass its observation gate");
         swing_observed=true;
      }
      if(automatic_style==0)TestAgentFlow(now,volume,q.ask,stop,tp);
      Assert(SubmitOrder(automatic_style,0,true,volume,stop,tp,40,now,now,3,policy),"production automatic entry style "+(string)automatic_style);
      Assert(CountOurs(automatic_style)==1 && !PendingIntent(),"automatic fill owns its independent slot "+(string)automatic_style);
      ulong ticket=PositionGetTicket(0),id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      Assert((ulong)PositionGetInteger(POSITION_MAGIC)==MagicFor(automatic_style) && Get(PK(id,"policy"))==policy,"automatic fill preserves magic and exit policy "+(string)automatic_style);
      Assert(PositionGetDouble(POSITION_SL)>0 && SharedFillAllowed(0),"automatic fill has broker stop and shared risk acceptance "+(string)automatic_style);
      run_mode="stop";generated=0;Set(PK(id,"close"),1);Manage(now);
      Assert(CountOurs()==0,"automatic protective exit survives stopped stale research "+(string)automatic_style);
      automatic_style++;if(automatic_style>=3)test_stage=8;
   }
   else if(test_stage==8)
   {
      datetime now=TimeGMT();tester_writes=true;
      datetime current_bar=iTime(_Symbol,PERIOD_M15,0);
      // The prior scenarios can finish minutes into a candle. Wait for a real
      // fresh candle instead of pairing its old open with a future quote.
      if(q.time-current_bar>60)return;
      // Exercise the production Evaluate path: unavailable indicator data must
      // leave the same fresh bar available for a retry, but never extend it.
      int ei=FindEngine(_Symbol,0),saved_atr=engines[ei].atr;
      string bk="bar."+_Symbol+".0";
      contracts[0].pool=true;contracts[0].verified=true;contracts[0].server_offset=(int)(q.time-now);
      Set(bk,0);engines[ei].atr=INVALID_HANDLE;
      Evaluate(ei,now);
      Assert(Get(bk)==0,"missing indicator does not consume the entry bar");
      engines[ei].atr=saved_atr;
      Evaluate(ei,current_bar-contracts[0].server_offset+61);
      Assert(Get(bk)==0,"retry cannot extend the sixty-second entry window");
      Evaluate(ei,now);
      Assert(Get(bk)==(double)current_bar,"ready data consumes the retried entry bar once");
      Set("entry."+_Symbol,0);Set("entries",0);Set("baseline",AccountInfoDouble(ACCOUNT_EQUITY));
      for(int style=0;style<3;style++)
      {
         trade.SetExpertMagicNumber(MagicFor(style));
         Assert(trade.Buy(volume,_Symbol,0,PriceFloor(q.ask-distance,size),PriceCeil(q.ask+4*distance,size)),"create simultaneous account risk fixture "+(string)style);
      }
      Assert(CountOurs(0)==1 && CountOurs(1)==1 && CountOurs(2)==1 && CountOurs()==3,"all three styles contribute to the account position inventory");
      double saved_aggregate=aggregate_risk,saved_sector=sector_fraction,planned=OpenRisk(false);
      aggregate_risk=planned+.5;
      Assert(SharedBudget(0,40)>0 && SharedBudget(0,40)<=.50001,"all three books consume one aggregate risk budget");
      aggregate_risk=saved_aggregate;sector_fraction=.00001;
      Assert(SharedBudget(0,40)==0,"correlated sector risk blocks all additional exposure");sector_fraction=saved_sector;
      for(int i=PositionsTotal()-1;i>=0;i--){ulong ticket=PositionGetTicket(i);if(ticket>0)CloseTicket(ticket,"TEST_CLEANUP");}
      Set("intent.2.time",(double)(now+120));Reconcile();
      Assert(PendingIntent() && !SharedEntryGate(0) && !SharedEntryGate(1) && !SharedEntryGate(2),"uncertain investment order blocks all three entry engines");
      Set("intent.2.time",0);Assert(!PendingIntent(),"explicit reconciled fixture restores account entry capacity");
      Set("intent.1.time",(double)(now+120));Set("intent.1.policy",200);MigrateInvestmentIntent();
      Set("intent.1.time",(double)(now+120));MigrateInvestmentIntent();
      Assert(Get("intent.1.time")==0 && Get("intent.2.time")>0 && Get("unreconciled")==0,"completed migration recovers idempotently after interrupted old-intent clear");
      Set("intent.2.time",0);
      lifetime_limit=900;SaveRiskLimits();lifetime_limit=1000;RestoreRiskLimits();
      Assert(lifetime_limit==900,"restart restores stricter saved account risk limit without a bridge");
      lifetime_limit=1000;SaveRiskLimits();
      Set("capital_base",AccountInfoDouble(ACCOUNT_EQUITY)+1001);UpdateLedger(now);
      Assert(Get("hard_halt")==1,"one thousand dollar lifetime loss latches account halt");
      MigrateInvestmentIntent();Assert(Get("hard_halt")==1,"legacy migration cannot reset a hard account halt");
      tester_writes=false;test_stage=9;TesterStop();
   }

}
double OnTester()
{
   int f=FileOpen(BridgeFolder+"\\integration.json",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   bool passed=!test_failed && test_stage==9 && test_passes>=75;
   if(f!=INVALID_HANDLE) { FileWriteString(f,"{\"passed\":"+(passed ? "true" : "false")+",\"checks\":"+(string)test_passes+",\"stage\":"+(string)test_stage+"}");FileClose(f); }
   return passed ? test_passes : -1;
}
void OnDeinit(const int reason) { }
