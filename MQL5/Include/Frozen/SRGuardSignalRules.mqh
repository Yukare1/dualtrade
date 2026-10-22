#ifndef SR_GUARD_SIGNAL_RULES
#define SR_GUARD_SIGNAL_RULES
bool UsableLevel(const MqlRates &bars[],bool support,int pivot_index,double level,double tolerance,
                 int max_age,bool invalidate_broken)
{
   if(pivot_index<2 || pivot_index>=ArraySize(bars) || level<=0) return false;
   if(max_age>0 && pivot_index>max_age) return false;
   if(invalidate_broken)
      for(int i=2;i<pivot_index;i++)
         if(support ? bars[i].close<level-tolerance : bars[i].close>level+tolerance) return false;
   return true;
}

bool RetestConfirmed(bool buy,const MqlRates &bar,double level,double atr,double tolerance)
{
   if(level<=0 || atr<=0) return false;
   return buy ? bar.low<=level+tolerance*atr && bar.low>=level-0.5*atr &&
                bar.close>level && bar.close>bar.open :
                bar.high>=level-tolerance*atr && bar.high<=level+0.5*atr &&
                bar.close<level && bar.close<bar.open;
}

bool NextRangeState(bool previous,bool sideways,int direction,double rsi,double enter,double exit)
{
   if(!sideways) return false;
   if(!previous) return direction==1 && rsi<enter;
   return !(rsi>=exit || direction==-1);
}
bool TrendDirection(bool buy,double fast,double old_fast,double slow,double close,double plus,double minus)
{
   return buy ? fast>slow && fast>old_fast && close>slow && plus>minus :
                fast<slow && fast<old_fast && close<slow && minus>plus;
}
bool PullbackConfirmed(bool buy,const MqlRates &signal,const MqlRates &prior,
                       double ema,double prior_ema,double atr)
{
   double middle=(signal.high+signal.low)*0.5;
   return buy ? prior.close>prior_ema && signal.low<=ema && signal.low>=ema-0.5*atr &&
                signal.close>ema && signal.close>signal.open && signal.close>=middle :
                prior.close<prior_ema && signal.high>=ema && signal.high<=ema+0.5*atr &&
                signal.close<ema && signal.close<signal.open && signal.close<=middle;
}
bool BreakoutQuality(bool buy,const MqlRates &bar)
{
   double range=bar.high-bar.low;
   if(range<=0 || MathAbs(bar.close-bar.open)<0.5*range) return false;
   return buy ? bar.close>=bar.low+0.75*range : bar.close<=bar.low+0.25*range;
}
bool SignalRuleTests()
{
   bool ok=TrendDirection(true,11,10,9,11,25,15) &&
           !TrendDirection(true,11,12,9,11,25,15) &&
           !TrendDirection(true,11,10,9,11,15,25) &&
           TrendDirection(false,9,10,11,9,15,25);
   MqlRates b={},p={};
   p.close=11; b.open=10.1; b.high=11; b.low=9.8; b.close=10.9;
   ok=ok && PullbackConfirmed(true,b,p,10,10,1);
   b.low=10.01; ok=ok && !PullbackConfirmed(true,b,p,10,10,1); // never touched EMA
   b.low=9.4; ok=ok && !PullbackConfirmed(true,b,p,10,10,1); // deep breakdown
   b.low=9.8; p.close=9.9;
   ok=ok && !PullbackConfirmed(true,b,p,10,10,1); // prior below its own EMA
   p.close=11; ok=ok && !PullbackConfirmed(true,b,p,10,11.1,1); // compare prior with prior EMA
   b.open=10; b.low=10; b.high=12; b.close=11.8;
   ok=ok && BreakoutQuality(true,b);
   b.open=11.6; ok=ok && !BreakoutQuality(true,b); // wick-heavy close
   b.open=12; b.close=10.2; ok=ok && BreakoutQuality(false,b);
   MqlRates bars[]; ArrayResize(bars,8);
   for(int i=0;i<8;i++) bars[i].close=11;
   ok=ok && UsableLevel(bars,true,5,10,0.1,6,true) && !UsableLevel(bars,true,5,10,0.1,4,false);
   bars[3].close=9; ok=ok && !UsableLevel(bars,true,5,10,0.1,6,true);
   bars[3].close=12; ok=ok && !UsableLevel(bars,false,5,11,0.1,6,true);
   b.open=10.1; b.close=10.5; b.low=9.9; b.high=10.6;
   ok=ok && RetestConfirmed(true,b,10,1,0.1) && !RetestConfirmed(false,b,10,1,0.1);
   // A restart must replay this hold (RSI 65), not initialise it to flat.
   bool state=NextRangeState(false,true,1,55,60,70);
   state=NextRangeState(state,true,1,65,60,70);
   ok=ok && state && !NextRangeState(false,true,1,65,60,70) &&
           !NextRangeState(state,false,1,65,60,70) && !NextRangeState(state,true,-1,65,60,70);
   Print(ok ? "SRGuard signal rule tests: PASS" : "SRGuard signal rule tests: FAIL");
   return ok;
}
#endif
