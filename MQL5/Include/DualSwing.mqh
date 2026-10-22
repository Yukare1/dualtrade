#ifndef DUAL_SWING
#define DUAL_SWING
// A technical swing policy is distinct from policy 200 fundamental investment.
// Legacy swing positions retain policy 0 and their existing exit behavior.
bool DTSwingDirection(bool long_only,int direction)
{ return direction==1 || (!long_only && direction==-1); }
bool DTSwingSetup(string setup)
{ return setup=="EMA pullback" || setup=="Donchian retest"; }
#endif
