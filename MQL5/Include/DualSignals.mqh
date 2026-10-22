#ifndef SR_GUARD_FX_ENGINE
#define SR_GUARD_FX_ENGINE
#include "Frozen/SRGuardCore.mqh"
#include "Frozen/SRGuardSignalRules.mqh"

// Closed-bar price zones. Scores rank rules, never estimate win probability.
struct FXZone
{
   double low,high,center,score;
   int touches,source;
   bool support;
   datetime newest;
};
struct FXCandidate
{
   int direction;
   string setup;
   double anchor,stop_anchor,score,objective;
   bool structure;
};
struct FXStrategySettings
{
   bool rejection,breakout,pullback,range,retest;
   bool strict_retest,strict_rejection;
   int breakout_bars,retest_max_bars;
   double level_tolerance,retest_tolerance,range_adx;
   double max_retest_atr,max_retest_rsi,min_rejection_score;
};
struct FXStrategyContext
{
   int support_index,resistance_index,regime,retest_dir;
   bool up,down;
   double atr,adx,rsi,prior_rsi,entry_ma,prior_ma,middle_band,upper_band,lower_band,retest_level;
   datetime now,retest_bar;
};

bool FXValidHistory(const MqlRates &bars[],int minimum)
{
   if(ArraySize(bars)<minimum) return false;
   for(int i=1;i<ArraySize(bars)-1;i++)
      if(!MathIsValidNumber(bars[i].open) || !MathIsValidNumber(bars[i].high) ||
         !MathIsValidNumber(bars[i].low) || !MathIsValidNumber(bars[i].close) ||
         bars[i].low<=0 || bars[i].high<MathMax(bars[i].open,bars[i].close) ||
         bars[i].low>MathMin(bars[i].open,bars[i].close) || bars[i].time<=bars[i+1].time)
         return false;
   return true;
}

// A level must survive all known closes AFTER it was confirmed, before the signal.
// reference_time is the signal OPEN, so an H1/H4 bar containing the signal is excluded.
void FXBuildZones(const MqlRates &bars[],int width,double atr,double tolerance_atr,
                  int minimum_touches,int max_age,bool invalidate,int source,
                  datetime reference_time,FXZone &zones[])
{
   if(atr<=0 || ArraySize(bars)<2*width+5) return;
   int newest_closed=1;
   while(newest_closed<ArraySize(bars) && bars[newest_closed-1].time>reference_time) newest_closed++;
   int first=newest_closed+width;
   double tolerance=atr*tolerance_atr;
   for(int side=0;side<2;side++)
   {
      bool used[]; ArrayResize(used,ArraySize(bars)); ArrayInitialize(used,false);
      // Most recent seed wins; members cannot be counted in overlapping clusters.
      for(int i=first;i<ArraySize(bars)-width;i++)
      {
         if(used[i] || (max_age>0 && i-newest_closed>max_age)) continue;
         bool support=side==0;
         if(!(support ? PivotLow(bars,i,width) : PivotHigh(bars,i,width))) continue;
         double seed=support ? bars[i].low : bars[i].high;
         double sum=0,low=seed,high=seed; int count=0,last_touch=-100000;
         datetime newest=0;
         int members[]; ArrayResize(members,0);
         for(int j=i;j<ArraySize(bars)-width;j++)
         {
            if(used[j] || (max_age>0 && j-newest_closed>max_age)) continue;
            if(!(support ? PivotLow(bars,j,width) : PivotHigh(bars,j,width))) continue;
            double price=support ? bars[j].low : bars[j].high;
            if(MathAbs(price-seed)>tolerance || j-last_touch<width*2+1) continue;
            bool broken=false;
            if(invalidate)
               for(int k=newest_closed;k<j-width;k++)
                  if(support ? bars[k].close<price-tolerance : bars[k].close>price+tolerance)
                  { broken=true; break; }
            if(broken) continue;
            int member=ArraySize(members); ArrayResize(members,member+1); members[member]=j;
            sum+=price; low=MathMin(low,price); high=MathMax(high,price); count++;
            last_touch=j; if(bars[j].time>newest) newest=bars[j].time;
         }
         if(count<minimum_touches) continue;
         for(int j=0;j<ArraySize(members);j++) used[members[j]]=true;
         int n=ArraySize(zones); ArrayResize(zones,n+1);
         zones[n].center=sum/count;
         // Preserve observed extent plus a small noise buffer; do not draw a magic line.
         zones[n].low=low-0.05*atr; zones[n].high=high+0.05*atr;
         zones[n].touches=count; zones[n].source=source; zones[n].support=support;
         zones[n].newest=newest;
         double age=(double)(reference_time-newest)/(source*60.0);
         zones[n].score=MathMin(90.0,35+MathMin(count,5)*5+(source>=240 ? 15 : source>=60 ? 10 : 0)
                                  +MathMax(0.0,10-age*0.15));
      }
   }
}

void FXAddDailyLevel(double price,bool support,datetime stamp,double atr,FXZone &zones[])
{
   if(price<=0 || atr<=0) return;
   int n=ArraySize(zones); ArrayResize(zones,n+1);
   zones[n].low=price-0.05*atr; zones[n].high=price+0.05*atr; zones[n].center=price;
   zones[n].score=55; zones[n].touches=1; zones[n].source=1440;
   zones[n].support=support; zones[n].newest=stamp;
}

bool FXDailyZones(const MqlRates &daily[],const MqlRates &r[],double atr,bool invalidate,
                  double tolerance,FXZone &zones[])
{
   if(ArraySize(daily)<4 || ArraySize(r)<3 || atr<=0) return false;
   int d=daily[0].time>r[1].time ? 2 : 1;
   bool high_broken=false,low_broken=false;
   for(int i=2;i<ArraySize(r) && r[i].time>=daily[d-1].time;i++)
   {
      if(r[i].close>daily[d].high+tolerance*atr) high_broken=true;
      if(r[i].close<daily[d].low-tolerance*atr) low_broken=true;
   }
   if(!invalidate || !low_broken) FXAddDailyLevel(daily[d].low,true,daily[d].time,atr,zones);
   if(!invalidate || !high_broken) FXAddDailyLevel(daily[d].high,false,daily[d].time,atr,zones);
   return true;
}

// Selection uses BOTH quality and distance. Keep the whole map for target obstacles.
int FXNearestZone(const FXZone &zones[],bool support,double reference,double atr)
{
   int selected=-1; double best=-1e100;
   if(atr<=0) return -1;
   for(int i=0;i<ArraySize(zones);i++)
   {
      if(zones[i].source==-60) continue; // Advisory Fibonacci ratios need observed price structure.
      if(zones[i].support!=support || (support ? zones[i].center>=reference : zones[i].center<=reference)) continue;
      double distance=MathAbs(reference-zones[i].center)/atr;
      double rank=zones[i].score-12.0*distance;
      if(rank>best) { best=rank; selected=i; }
   }
   return selected;
}

bool FXRejection(bool buy,const MqlRates &bar,double low,double high,double atr,double tolerance)
{
   double range=bar.high-bar.low;
   if(atr<=0 || range<=0) return false;
   return buy ? bar.low<=high+tolerance*atr && bar.low>=low-0.5*atr &&
                bar.close>high && bar.close>bar.open && bar.close>=bar.low+0.65*range :
                bar.high>=low-tolerance*atr && bar.high<=high+0.5*atr &&
                bar.close<low && bar.close<bar.open && bar.close<=bar.low+0.35*range;
}

int FXRegime(double adx,double slope,double trend_adx,double range_adx,double minimum_slope)
{
   if(adx>=trend_adx && MathAbs(slope)>=minimum_slope) return 1;
   if(adx<range_adx && MathAbs(slope)<minimum_slope) return -1;
   return 0; // Uncertain transition: do not force a strategy to trade.
}

void FXAddCandidate(FXCandidate &list[],int direction,string setup,double anchor,
                    double stop_anchor,double score,bool structure,double objective=0)
{
   if(direction==0 || anchor<=0) return;
   int n=ArraySize(list); ArrayResize(list,n+1);
   list[n].direction=direction; list[n].setup=setup; list[n].anchor=anchor;
   list[n].stop_anchor=stop_anchor; list[n].score=score;
   list[n].structure=structure; list[n].objective=objective;
}

// All eligible strategies are evaluated first. Opposite signals are a veto,
// same-direction agreement is a small bonus, not independent statistical evidence.
int FXSelectCandidate(const FXCandidate &list[],double minimum_score,int &votes,bool &conflict)
{
   votes=0; conflict=false; int best=-1,direction=0;
   for(int i=0;i<ArraySize(list);i++)
   {
      if(list[i].score<minimum_score) continue;
      if(direction!=0 && list[i].direction!=direction) conflict=true;
      direction=list[i].direction; votes++;
      if(best<0 || list[i].score>list[best].score) best=i;
   }
   return conflict ? -1 : best;
}

// Every mapped opposing obstacle ahead of the actual fill matters, including
// zones less than 0.2 ATR away. A zone intersecting entry blocks the trade.
bool FXCapTarget(const FXZone &zones[],bool buy,double entry,double atr,double buffer,
                 double &target)
{
   for(int i=0;i<ArraySize(zones);i++)
   {
      if(zones[i].source==-60) continue; // Raw ratios do not create unconfirmed hard target walls.
      if(zones[i].support==buy) continue;
      if(buy)
      {
         if(zones[i].high<entry) continue;
         if(zones[i].low<=entry) return false;
         target=MathMin(target,zones[i].low-buffer*atr);
      }
      else
      {
         if(zones[i].low>entry) continue;
         if(zones[i].high>=entry) return false;
         target=MathMax(target,zones[i].high+buffer*atr);
      }
   }
   return buy ? target>entry : target<entry;
}

double FXSetupScore(double zone_score,bool structure,bool candle,bool momentum)
{
   return MathMin(95.0,35+(structure ? MathMin(25.0,zone_score*0.30) : 0)+(candle ? 20 : 0)+(momentum ? 15 : 0));
}

bool FXDirectionAllowed(int direction,bool allow_long,bool allow_short)
{ return direction==1 ? allow_long : direction==-1 ? allow_short : false; }

// Limit directional exhaustion, not direction itself. The SELL rule is the
// exact price-reflection counterpart of BUY; no permanent long/short bias.
bool FXEntryRSIAllowed(int direction,double rsi,double limit)
{
   if(!MathIsValidNumber(rsi) || rsi<0 || rsi>100 || limit<50 || limit>100) return false;
   return direction==1 ? rsi<=limit : direction==-1 ? rsi>=100-limit : false;
}

bool FXRetestQuality(bool buy,const MqlRates &bar,double level,double atr,double rsi,
                     double max_rsi,double max_distance)
{
   if(atr<=0 || max_distance<=0 || !BreakoutQuality(buy,bar)) return false;
   double distance=buy ? bar.close-level : level-bar.close;
   bool momentum=buy ? rsi>=50 && rsi<=max_rsi : rsi>=100-max_rsi && rsi<=50;
   return momentum && distance>0 && distance<=max_distance*atr;
}

bool FXRejectionQuality(bool buy,const MqlRates &bar,const MqlRates &prior,double zone_score,double minimum_score)
{
   double range=bar.high-bar.low;
   if(range<=0 || zone_score<minimum_score) return false;
   double wick=buy ? MathMin(bar.open,bar.close)-bar.low : bar.high-MathMax(bar.open,bar.close);
   return wick>=0.20*range && (buy ? bar.close>prior.close : bar.close<prior.close);
}

// Pure strategy layer, shared by the EA and executable regression/replay harness.
// No market orders, terminal state, calendar or future candles are accessible here.
void FXEvaluateStrategies(const FXStrategySettings &s,const MqlRates &r[],const FXZone &zones[],
                          FXStrategyContext &c,FXCandidate &candidates[])
{
   if(ArraySize(r)<s.breakout_bars+2 || c.atr<=0) return;
   double upper=r[2].high,lower=r[2].low;
   for(int j=3;j<s.breakout_bars+2;j++) { upper=MathMax(upper,r[j].high); lower=MathMin(lower,r[j].low); }
   if(s.rejection)
      for(int side=0;side<2;side++)
      {
         bool buy=side==0; int z=buy ? c.support_index : c.resistance_index;
         if(z<0 || z>=ArraySize(zones) || !(buy ? c.up : c.down) ||
            !(buy ? c.rsi>=35 && c.rsi<=70 : c.rsi>=30 && c.rsi<=65)) continue;
         if(FXRejection(buy,r[1],zones[z].low,zones[z].high,c.atr,s.level_tolerance) &&
            (!s.strict_rejection || FXRejectionQuality(buy,r[1],r[2],zones[z].score,s.min_rejection_score)))
            FXAddCandidate(candidates,buy ? 1 : -1,"S/R rejection",zones[z].center,
               buy ? zones[z].low : zones[z].high,FXSetupScore(zones[z].score,true,true,true),true);
      }
   if(s.breakout)
   {
      bool confirmed=false;
      if(s.retest && c.retest_dir!=0)
      {
         // Count completed entry bars, not fixed M15 seconds. Weekends do not
         // consume fictitious H4 retest candles.
         if(c.retest_bar<r[MathMin(s.retest_max_bars+1,ArraySize(r)-1)].time ||
            (c.retest_dir>0 ? r[1].close<c.retest_level-0.5*c.atr : r[1].close>c.retest_level+0.5*c.atr))
            c.retest_dir=0;
         else if(r[1].time>c.retest_bar)
         {
            bool buy=c.retest_dir>0;
            if((buy ? c.up : c.down) && RetestConfirmed(buy,r[1],c.retest_level,c.atr,s.retest_tolerance) &&
               (!s.strict_retest || FXRetestQuality(buy,r[1],c.retest_level,c.atr,c.rsi,s.max_retest_rsi,s.max_retest_atr)))
            {
               FXAddCandidate(candidates,c.retest_dir,"Donchian retest",c.retest_level,c.retest_level,
                              FXSetupScore(60,true,true,true),true);
               c.retest_dir=0; confirmed=true;
            }
         }
      }
      if(!confirmed)
      {
         bool buy=c.up && c.rsi>=50 && c.rsi<75 && r[1].close>upper+0.05*c.atr &&
                  r[1].close>r[1].open && BreakoutQuality(true,r[1]);
         bool sell=c.down && c.rsi>25 && c.rsi<=50 && r[1].close<lower-0.05*c.atr &&
                   r[1].close<r[1].open && BreakoutQuality(false,r[1]);
         if(s.retest && (buy || sell))
         { c.retest_dir=buy ? 1 : -1; c.retest_level=buy ? upper : lower; c.retest_bar=r[1].time; }
         else if(!s.retest && (buy || sell))
            FXAddCandidate(candidates,buy ? 1 : -1,"Donchian breakout",buy ? upper : lower,
                           buy ? upper : lower,FXSetupScore(60,true,true,true),true);
      }
   }
   if(s.pullback)
   {
      bool buy=c.up && c.rsi>=40 && c.rsi<=68 && PullbackConfirmed(true,r[1],r[2],c.entry_ma,c.prior_ma,c.atr);
      bool sell=c.down && c.rsi>=32 && c.rsi<=60 && PullbackConfirmed(false,r[1],r[2],c.entry_ma,c.prior_ma,c.atr);
      if(buy || sell)
      {
         int z=buy ? c.support_index : c.resistance_index;
         bool confluence=z>=0 && z<ArraySize(zones) && MathAbs(c.entry_ma-zones[z].center)<=0.5*c.atr;
         double stop=confluence ? (buy ? zones[z].low : zones[z].high) : c.entry_ma;
         FXAddCandidate(candidates,buy ? 1 : -1,"EMA pullback",c.entry_ma,stop,
                        FXSetupScore(confluence ? zones[z].score : 0,confluence,true,true),confluence);
      }
   }
   if(s.range && c.regime==-1 && c.adx<s.range_adx)
      for(int side=0;side<2;side++)
      {
         bool buy=side==0; int z=buy ? c.support_index : c.resistance_index;
         if(z<0 || z>=ArraySize(zones)) continue;
         bool band_return=buy ? r[1].low<c.lower_band && r[1].close>c.lower_band && c.rsi<45 && c.rsi>c.prior_rsi :
                               r[1].high>c.upper_band && r[1].close<c.upper_band && c.rsi>55 && c.rsi<c.prior_rsi;
         if(band_return && FXRejection(buy,r[1],zones[z].low,zones[z].high,c.atr,s.level_tolerance))
            FXAddCandidate(candidates,buy ? 1 : -1,"Range reversion",zones[z].center,
               buy ? zones[z].low : zones[z].high,FXSetupScore(zones[z].score,true,true,true),true,c.middle_band);
      }
}

bool FXEngineTests()
{
   bool ok=FXRegime(27,0.08,20,18,0.03)==1 && FXRegime(15,0.01,20,18,0.03)==-1 &&
           FXRegime(19,0.04,20,18,0.03)==0;
   MqlRates b={}; b.open=10.2; b.low=9.9; b.high=11; b.close=10.9;
   ok=ok && FXRejection(true,b,9.95,10.05,1,0.1);
   b.close=10.3; ok=ok && !FXRejection(true,b,9.95,10.05,1,0.1);
   FXCandidate c[]; FXAddCandidate(c,1,"SR",10,9,75,true);
   FXAddCandidate(c,1,"EMA",10,9,70,false);
   int votes=0; bool conflict=false;
   ok=ok && FXSelectCandidate(c,65,votes,conflict)==0 && votes==2 && !conflict;
   FXAddCandidate(c,-1,"opposite",10,11,75,true);
   ok=ok && FXSelectCandidate(c,65,votes,conflict)==-1 && conflict;
   FXZone z[]; ArrayResize(z,1); z[0].support=false; z[0].low=10.1; z[0].high=10.2;
   double target=12;
   ok=ok && FXCapTarget(z,true,10,1,0.05,target) && MathAbs(target-10.05)<1e-8;
   target=12; ok=ok && !FXCapTarget(z,true,10.15,1,0.05,target);
   z[0].support=true; z[0].low=9.8; z[0].high=9.9; target=8;
   ok=ok && FXCapTarget(z,false,10,1,0.05,target) && MathAbs(target-9.95)<1e-8;
   Print(ok ? "FX engine tests: PASS" : "FX engine tests: FAIL");
   return ok;
}
#endif
