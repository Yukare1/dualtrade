#ifndef DUAL_RISK
#define DUAL_RISK
// Pure shared constraints used by all three native execution paths.
double DTScaledLimit(double fixed_limit,double equity,double fraction)
{ return MathMax(0,MathMin(fixed_limit,equity*fraction)); }
double DTAccountCapacity(double equity,double capital_base,double peak,double week_base,
                         double lifetime_cap,double drawdown_cap,double weekly_cap,double downside)
{
   return MathMax(0,MathMin(equity-capital_base+lifetime_cap,
      MathMin(equity-peak+drawdown_cap,equity-week_base+weekly_cap))-downside);
}
double DTExposureVolume(double equity,double gross,double group_gross,double unit_nominal,
                         double account_multiple,double group_multiple)
{
   if(unit_nominal<=0 || equity<=0) return 0;
   return MathMax(0,MathMin(equity*account_multiple-gross,equity*group_multiple-group_gross)/unit_nominal);
}
double DTStressVolume(double equity,double open_stress,double unit_stress,double fraction)
{
   if(unit_stress<=0 || equity<=0) return 0;
   return MathMax(0,(equity*fraction-open_stress)/unit_stress);
}
#endif
