#ifndef DUAL_AGENT_MQH
#define DUAL_AGENT_MQH
// These gates never grant account permission. The dated observation exception
// below changes only that one prerequisite after explicit user authorization.
bool DTDayObservationWaiver(long now,long until,int style,bool pinned_demo,bool review_required,
                           string context,string expected_context,string authorization)
{
   return style==0 && pinned_demo && review_required && now>=1792676700 && now<1792697100 &&
      until==1792697100 && context!="" && context==expected_context &&
      authorization=="user-20261022-day-observation-waiver";
}
bool DTAgentWindow(long created,long reviewed,long expires,long now)
{
   return created>0 && reviewed>=created && reviewed<=now && expires>=reviewed &&
      expires<=created+55 && now<=expires;
}
bool DTAgentBudget(double requested,double ceiling,double equity)
{
   return MathIsValidNumber(requested) && MathIsValidNumber(ceiling) && MathIsValidNumber(equity) &&
      requested>0 && requested<=ceiling && requested<=40 && requested<=equity*.04;
}
bool DTAgentPrice(bool buy,double reference,double entry,double stop,double target,double atr)
{
   return MathIsValidNumber(reference) && MathIsValidNumber(entry) && MathIsValidNumber(stop) &&
      MathIsValidNumber(target) && MathIsValidNumber(atr) && reference>0 && entry>0 && atr>0 &&
      MathAbs(entry-reference)<=.10*atr && MathAbs(entry-stop)>=.999*atr && MathAbs(entry-stop)<=3*atr &&
      (buy ? stop<entry && target>entry : stop>entry && target<entry);
}
bool DTStopConfirmed(bool sent,int code,int done,bool selected,double actual_sl,double actual_tp,
                     double requested_sl,double requested_tp,double tick_size)
{
   return sent && code==done && selected && tick_size>0 &&
      MathIsValidNumber(actual_sl) && MathIsValidNumber(actual_tp) &&
      MathAbs(actual_sl-requested_sl)<=tick_size/2 && MathAbs(actual_tp-requested_tp)<=tick_size/2;
}
#endif
