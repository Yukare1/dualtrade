#ifndef SR_GUARD_CORE
#define SR_GUARD_CORE
// Shared by the EA, calendar exporter and native regression tests.
struct NewsWindow
{
   datetime start, finish, flatten;
   string currency, name;
};

bool AddNewsCurrency(string currency,string &currencies[])
{
   string allowed[]={"EUR","USD","JPY","GBP","CHF","CAD","AUD","NZD"};
   bool known=false;
   for(int i=0;i<ArraySize(allowed);i++) if(currency==allowed[i]) known=true;
   if(!known) return false;
   for(int i=0;i<ArraySize(currencies);i++) if(currencies[i]==currency) return true;
   int n=ArraySize(currencies); ArrayResize(currencies,n+1); currencies[n]=currency;
   return true;
}

bool CompleteNewsCoverage(const string &currencies[],const bool &covered[])
{
   if(ArraySize(currencies)==0 || ArraySize(currencies)!=ArraySize(covered)) return false;
   for(int i=0;i<ArraySize(covered);i++) if(!covered[i]) return false;
   return true;
}

datetime DayStart(datetime time)
{
   MqlDateTime d; TimeToStruct(time,d);
   d.hour=0; d.min=0; d.sec=0;
   return StructToTime(d);
}

bool IsPolicyEvent(const MqlCalendarEvent &e)
{
   string code=e.event_code; StringToLower(code);
   return e.sector==CALENDAR_SECTOR_MONEY || StringFind(code,"interest-rate")>=0 ||
          StringFind(code,"monetary-policy")>=0 || StringFind(code,"fomc")>=0 ||
          StringFind(code,"press-conference")>=0;
}

bool RelevantEvent(const MqlCalendarEvent &e)
{
   return e.importance==CALENDAR_IMPORTANCE_HIGH ||
          (IsPolicyEvent(e) && e.importance>=CALENDAR_IMPORTANCE_MODERATE);
}

void MakeNewsWindow(datetime time,const MqlCalendarEvent &e,string currency,NewsWindow &w)
{
   bool policy=IsPolicyEvent(e);
   w.start=time-(policy ? 120 : 60)*60;
   w.finish=time+(policy ? 240 : 90)*60;
   w.flatten=time-(policy ? 30 : 15)*60;
   if(e.time_mode!=CALENDAR_TIMEMODE_DATETIME)
   {
      w.start=DayStart(time)-120*60;
      w.finish=DayStart(time)+86400+240*60;
      w.flatten=w.start;
   }
   w.currency=currency; w.name=e.event_code;
}

bool NewsActive(const NewsWindow &w,datetime now)
{ return now>=w.start && now<=w.finish; }

bool NeedsFlatten(const NewsWindow &w,datetime now)
{ return now>=w.flatten && now<=w.finish; }

void SortNews(NewsWindow &windows[],int left,int right)
{
   int i=left,j=right;
   datetime pivot=windows[(left+right)/2].start;
   while(i<=j)
   {
      while(windows[i].start<pivot) i++;
      while(windows[j].start>pivot) j--;
      if(i<=j) { NewsWindow t=windows[i]; windows[i]=windows[j]; windows[j]=t; i++; j--; }
   }
   if(left<j) SortNews(windows,left,j);
   if(i<right) SortNews(windows,i,right);
}

int LastStartedWindow(const NewsWindow &windows[],datetime now)
{
   int lo=0,hi=ArraySize(windows)-1,result=-1;
   while(lo<=hi)
   {
      int mid=(lo+hi)/2;
      if(windows[mid].start<=now) { result=mid; lo=mid+1; } else hi=mid-1;
   }
   return result;
}

// All indexes are series indexes. A pivot is usable only after right-hand bars close.
bool PivotHigh(const MqlRates &r[],int index,int width)
{
   if(index-width<1 || index+width>=ArraySize(r)) return false;
   for(int k=1;k<=width;k++)
      if(r[index].high<=r[index-k].high || r[index].high<=r[index+k].high) return false;
   return true;
}

bool PivotLow(const MqlRates &r[],int index,int width)
{
   if(index-width<1 || index+width>=ArraySize(r)) return false;
   for(int k=1;k<=width;k++)
      if(r[index].low>=r[index-k].low || r[index].low>=r[index+k].low) return false;
   return true;
}

double FloorVolume(double raw,double minimum,double maximum,double step)
{
   if(raw<=0 || step<=0 || minimum<=0) return 0;
   double v=NormalizeDouble(MathFloor((MathMin(raw,maximum)+1e-10)/step)*step,8);
   if(v<minimum-1e-10) return 0; // Never round up to the broker minimum.
   return v;
}

double PriceFloor(double price,double tick)
{ return tick>0 ? MathFloor(price/tick+1e-9)*tick : 0; }
double PriceCeil(double price,double tick)
{ return tick>0 ? MathCeil(price/tick-1e-9)*tick : 0; }

bool CoreSelfTests()
{
   bool ok=true;
   string currencies[];
   ok=ok && AddNewsCurrency("AUD",currencies) && AddNewsCurrency("USD",currencies) &&
      AddNewsCurrency("AUD",currencies) && ArraySize(currencies)==2 && !AddNewsCurrency("BAD",currencies);
   bool covered[]={false,true};
   ok=ok && !CompleteNewsCoverage(currencies,covered);
   covered[0]=true; ok=ok && CompleteNewsCoverage(currencies,covered);
   MqlCalendarEvent e={}; e.sector=CALENDAR_SECTOR_MONEY;
   e.importance=CALENDAR_IMPORTANCE_MODERATE; e.time_mode=CALENDAR_TIMEMODE_DATETIME;
   e.event_code="rate-decision";
   datetime t=D'2026.10.20 15:00'; NewsWindow w;
   MakeNewsWindow(t,e,"USD",w);
   ok=ok && RelevantEvent(e) && w.start==t-7200 && w.finish==t+14400;
   ok=ok && !NewsActive(w,w.start-1) && NewsActive(w,w.start) && NewsActive(w,w.finish);
   ok=ok && !NewsActive(w,w.finish+1) && !NeedsFlatten(w,t-1801) && NeedsFlatten(w,t-1800);
   e.sector=CALENDAR_SECTOR_JOBS; e.event_code="payrolls"; e.importance=CALENDAR_IMPORTANCE_HIGH;
   MakeNewsWindow(t,e,"USD",w);
   ok=ok && w.start==t-3600 && w.finish==t+5400 && w.flatten==t-900;
   e.time_mode=CALENDAR_TIMEMODE_TENTATIVE; MakeNewsWindow(t,e,"USD",w);
   ok=ok && NewsActive(w,DayStart(t)+23*3600);
   ok=ok && FloorVolume(0.009,0.01,100,0.01)==0;
   ok=ok && MathAbs(FloorVolume(0.0299,0.01,100,0.01)-0.02)<1e-8;
   ok=ok && MathAbs(FloorVolume(0.16,0.1,100,0.1)-0.1)<1e-8;
   ok=ok && MathAbs(FloorVolume(101,0.01,100,0.01)-100)<1e-8;
   MqlRates r[]; ArrayResize(r,12); ArraySetAsSeries(r,true);
   for(int i=0;i<12;i++) { r[i].high=10; r[i].low=5; }
   r[4].high=15; r[4].low=1;
   ok=ok && PivotHigh(r,4,2) && PivotLow(r,4,2);
   r[0].high=1000; // Forming bar must not invalidate a confirmed pivot.
   ok=ok && PivotHigh(r,4,2) && !PivotHigh(r,2,2);
   r[3].high=15; ok=ok && !PivotHigh(r,4,2);
   NewsWindow sorted[]; ArrayResize(sorted,3);
   sorted[0].start=t+30; sorted[1].start=t; sorted[2].start=t+10;
   SortNews(sorted,0,2);
   ok=ok && sorted[0].start==t && sorted[2].start==t+30;
   ok=ok && LastStartedWindow(sorted,t-1)==-1 && LastStartedWindow(sorted,t+10)==1;
   ok=ok && PriceFloor(1.23456,0.0001)<1.23456 && PriceCeil(1.23456,0.0001)>1.23456;
   Print(ok ? "SRGuard core regression tests: PASS" : "SRGuard core regression tests: FAIL");
   return ok;
}
#endif
