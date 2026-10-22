#ifndef DUAL_POLICY
#define DUAL_POLICY
// Pure production functions exercised by the native-body compatibility tests.
double DTRiskBudget(double target,double ceiling,double aggregate,double open_planned,
                    double equity,double baseline,double daily_limit,double open_downside)
{
   return MathMax(0,MathMin(target,MathMin(ceiling,MathMin(aggregate-open_planned,equity-(baseline-daily_limit)-open_downside))));
}
int DTExitPolicy(int style,datetime now,datetime session_close,datetime earnings_close,
                 datetime earnings_end,bool macro_exit,bool halt,bool cross,bool margin)
{
   if(halt) return 1;
   if(margin) return 2;
   if(earnings_close>0 && now>=earnings_close) return 3;
   if(style==0 && session_close>0 && now>=session_close-300) return 4;
   if(style==0 && macro_exit) return 5;
   if(style==1 && cross) return 6;
   return 0;
}
bool DTCanRollDay(bool continuous,bool unresolved,bool reconciled)
{ return continuous && !unresolved && reconciled; }
bool DTCouncilAllows(int style,bool ready,bool buy_allowed,bool sell_allowed,bool buy)
{
   if(style==0) return true;
   return ready && (buy ? buy_allowed : sell_allowed);
}
#endif
