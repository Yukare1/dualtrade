#ifndef DUAL_EXECUTION
#define DUAL_EXECUTION
// Stable MetaTrader server codes. Deliberately exclude TIMEOUT, CONNECTION,
// ERROR, LOCKED, PLACED and DONE_PARTIAL: those require broker reconciliation.
bool DTDefinitiveNoFill(int code)
{
   return code==10004 || code==10006 || code==10007 || code==10013 ||
      code==10014 || code==10015 || code==10016 || code==10017 ||
      code==10018 || code==10019 || code==10020 || code==10021 ||
      code==10022 || code==10024 || code==10026 || code==10027 ||
      code==10030 || code==10032 || code==10033 || code==10034 ||
      code==10035 || code==10038 || code==10040 || code==10042 ||
      code==10043 || code==10044 || code==10045 || code==10046;
}
// 0: unresolved; 1: target complete; 2: terminal order permits a residual retry.
// Time passing alone never establishes a final outcome.
int DTReductionOutcome(bool position_open,double volume,double target,double step,
                       bool order_final,bool pending_order,bool verified_closed)
{
   if(pending_order || !MathIsValidNumber(target) || target<0 || !MathIsValidNumber(step) || step<=0)return 0;
   if(!position_open)return verified_closed ? 1 : 0;
   if(!order_final || !MathIsValidNumber(volume) || volume<=0)return 0;
   return volume<=target+step/2 ? 1 : 2;
}
#endif
