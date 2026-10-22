// Included after the production engine: broker side effects remain in SubmitOrder/CloseTicket.
struct AgentPending
{
   string id,context,symbol;
   int style,strategy,policy;
   bool buy,done;
   datetime created,expires,bar,reviewed,decision_expires;
   double entry,stop,target,atr,ceiling,max_volume,approved_risk;
};
AgentPending agent_pending[];
int agent_active=-1;
string agent_nonce="",decision_context="";
bool agent_review_required=true;

bool AgentPermit(int style,string symbol,bool buy,double stop,double target,double budget,datetime bar,datetime now)
{
   if(!agent_review_required)return true;
   if(agent_active<0 || agent_active>=ArraySize(agent_pending))return false;
   AgentPending p=agent_pending[agent_active];
   return !p.done && p.context==decision_context && p.style==style && p.symbol==symbol && p.buy==buy &&
      p.stop==stop && p.target==target && p.bar==bar && budget<=p.approved_risk &&
      DTAgentWindow((long)p.created,(long)p.reviewed,(long)p.decision_expires,(long)now) &&
      Get("agent.used."+p.id)==0;
}
void AgentConsume()
{
   if(!agent_review_required || agent_active<0)return;
   agent_pending[agent_active].done=true;
   Set("agent.used."+agent_pending[agent_active].id,(double)TimeGMT());GlobalVariablesFlush();
}
bool AgentQueue(int style,int ci,bool buy,double entry,double stop,double target,double atr,double ceiling,
                double maximum,datetime now,datetime bar,int strategy,int policy,double score,
                double support_low,double support_high,double resistance_low,double resistance_high)
{
   if(!agent_review_required || style<0 || style>1 || StringLen(decision_context)!=64 || agent_nonce=="")return false;
   // Retain the original fresh-entry-bar window: an agent response cannot extend it.
   ENUM_TIMEFRAMES tf=style==0 ? PERIOD_M15 : PERIOD_H4;
   datetime opened=iTime(contracts[ci].symbol,tf,0)-contracts[ci].server_offset;
   datetime expires=(datetime)MathMin((long)now+55,(long)opened+60);
   if(expires<=now || now<opened || now-opened>60)return false;
   int k=-1;
   for(int i=0;i<ArraySize(agent_pending);i++)if(agent_pending[i].done || agent_pending[i].expires<now){k=i;break;}
   if(k<0){k=ArraySize(agent_pending);if(k>=64)return false;ArrayResize(agent_pending,k+1);}
   ZeroMemory(agent_pending[k]);
   AgentPending p;ZeroMemory(p);
   p.id=agent_nonce+"."+(string)style+"."+(string)ci+"."+(string)(long)bar;
   p.context=decision_context;p.symbol=contracts[ci].symbol;p.style=style;p.strategy=strategy;p.policy=policy;
   p.buy=buy;p.created=now;p.expires=expires;p.bar=bar;
   p.entry=entry;p.stop=stop;p.target=target;p.atr=atr;p.ceiling=ceiling;p.max_volume=maximum;
   agent_pending[k]=p;
   FolderCreate(BridgeFolder+"\\agent-requests",FILE_COMMON);FolderCreate(BridgeFolder+"\\agent-decisions",FILE_COMMON);
   string file=BridgeFolder+"\\agent-requests\\"+p.id;
   int f=FileOpen(file+".tmp",FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON,'|');
   if(f==INVALID_HANDLE){agent_pending[k].done=true;return false;}
   FileWrite(f,"REQUEST",1,p.id,p.context,(long)now,(long)expires,p.symbol,style,buy ? 1 : -1,
      DoubleToString(entry,10),DoubleToString(stop,10),DoubleToString(target,10),DoubleToString(atr,10),
      DoubleToString(ceiling,8),DoubleToString(maximum,8),(long)bar,strategy,policy,score,
      DoubleToString(support_low,10),DoubleToString(support_high,10),DoubleToString(resistance_low,10),DoubleToString(resistance_high,10));
   FileFlush(f);FileClose(f);
   if(!FileMove(file+".tmp",FILE_COMMON,file+".txt",FILE_REWRITE|FILE_COMMON)){agent_pending[k].done=true;return false;}
   Audit("AGENT_REQUEST",p.symbol,p.id,style,ceiling,maximum);return true;
}
void ProcessAgentRequests(datetime now)
{
   if(!agent_review_required)return;
   for(int i=0;i<ArraySize(agent_pending);i++)
   {
      AgentPending p=agent_pending[i];if(p.done)continue;
      if(now>p.expires || p.context!=decision_context){agent_pending[i].done=true;continue;}
      int f=FileOpen(BridgeFolder+"\\agent-decisions\\"+p.id+".txt",FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
      if(f==INVALID_HANDLE)continue;
      string line=FileReadString(f),v[];bool one=FileIsEnding(f);FileClose(f);
      int n=StringSplit(line,'|',v);
      if(!one || n!=12 || v[0]!="AGENT" || v[1]!="1" || v[2]!=p.id || v[3]!=p.context)
      {agent_pending[i].done=true;Audit("AGENT_REJECT",p.symbol,"MALFORMED_OR_CONTEXT");continue;}
      datetime reviewed=(datetime)StringToInteger(v[4]),expires=(datetime)StringToInteger(v[5]);
      double approved=StringToDouble(v[7]);
      if(v[6]!="APPROVE" || !DTAgentWindow((long)p.created,(long)reviewed,(long)expires,(long)now) ||
         !DTAgentBudget(approved,p.ceiling,AccountInfoDouble(ACCOUNT_EQUITY)) ||
         MathAbs(StringToDouble(v[8])-p.entry)>1e-8 || MathAbs(StringToDouble(v[9])-p.stop)>1e-8 || MathAbs(StringToDouble(v[10])-p.target)>1e-8 || v[11]!="OPEN")
      {agent_pending[i].done=true;Audit("AGENT_REJECT",p.symbol,"DECLINED_EXPIRED_OR_CHANGED");continue;}
      int ci=FindContract(p.symbol);MqlTick q;
      if(ci<0 || !SymbolInfoTick(p.symbol,q)){agent_pending[i].done=true;continue;}
      double entry=p.buy ? q.ask : q.bid,point=SymbolInfoDouble(p.symbol,SYMBOL_POINT);
      if(!DTAgentPrice(p.buy,p.entry,entry,p.stop,p.target,p.atr) || q.ask-q.bid>MathMin(entry*.001,p.atr*.15) || q.ask<q.bid)
      {agent_pending[i].done=true;Audit("AGENT_REJECT",p.symbol,"PRICE_MOVED_OR_SPREAD");continue;}
      double unit=UnitCost(ci,p.style,p.buy),loss=StopLossMoney(p.symbol,p.buy,1,entry,p.stop+(p.buy ? -1 : 1)*contracts[ci].slippage*point);
      if(loss<=0 || unit<0 || !MathIsValidNumber(loss+unit)){agent_pending[i].done=true;continue;}
      double budget=SharedBudget(ci,approved);
      double volume=FloorVolume(MathMin(p.max_volume,budget/(loss+unit)),SymbolInfoDouble(p.symbol,SYMBOL_VOLUME_MIN),SymbolInfoDouble(p.symbol,SYMBOL_VOLUME_MAX),SymbolInfoDouble(p.symbol,SYMBOL_VOLUME_STEP));
      volume=SharedVolume(ci,p.buy,entry,loss+unit,unit,volume);
      agent_pending[i].approved_risk=approved;agent_pending[i].reviewed=reviewed;agent_pending[i].decision_expires=expires;
      agent_active=i;
      bool sent=volume>0 && loss>0 && SubmitOrder(p.style,ci,p.buy,volume,p.stop,p.target,budget,now,p.bar,p.strategy,p.policy);
      agent_active=-1;agent_pending[i].done=true;
      Audit("AGENT_DECISION",p.symbol,sent ? "SUBMITTED" : "NATIVE_GATE_BLOCKED",volume,budget);
   }
}
void ProcessAgentClose(datetime now)
{
   if(!agent_review_required || !MayWrite())return;
   int f=FileOpen(BridgeFolder+"\\agent-close.txt",FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(f==INVALID_HANDLE)return;
   string line=FileReadString(f),v[];bool one=FileIsEnding(f);FileClose(f);
   if(!one || StringSplit(line,'|',v)!=11 || v[0]!="CLOSE" || v[1]!="1" || v[2]!=agent_nonce || v[3]!=decision_context)return;
   datetime reviewed=(datetime)StringToInteger(v[4]),expires=(datetime)StringToInteger(v[5]);
   if(!DTAgentWindow((long)reviewed,(long)reviewed,(long)expires,(long)now))return;
   ulong ticket=(ulong)StringToInteger(v[6]),id=(ulong)StringToInteger(v[7]);
   if(ticket==0 || id==0 || !PositionSelectByTicket(ticket) || !Ours((ulong)PositionGetInteger(POSITION_MAGIC)) ||
      (ulong)PositionGetInteger(POSITION_IDENTIFIER)!=id || PositionGetString(POSITION_SYMBOL)!=v[8] ||
      PositionGetDouble(POSITION_VOLUME)!=StringToDouble(v[9]) || v[10]!="THESIS_INVALIDATED")return;
   Set(PK(id,"close"),1);GlobalVariablesFlush();CloseTicket(ticket,"AGENT_THESIS_EXIT");
}
