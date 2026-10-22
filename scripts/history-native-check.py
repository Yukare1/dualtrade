"""Exercise production zone code over actual exported broker histories."""
from pathlib import Path
import sys,subprocess,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from test_native import production_code
from native_helpers import SHIM,COMPILER
tail=r'''
static double Number(string value) { return double.Parse(value,System.Globalization.CultureInfo.InvariantCulture); }
static void Main(string[] args) {
 int windows=0,zonesCount=0;
 foreach(string file in System.IO.Directory.GetFiles(args[0],"*.csv")) {
  var lines=System.IO.File.ReadAllLines(file);var bars=new MqlRates[lines.Length-1];
  for(int i=1;i<lines.Length;i++) {var a=lines[i].Split(',');bars[i-1]=new MqlRates {time=long.Parse(a[0]),open=Number(a[1]),high=Number(a[2]),low=Number(a[3]),close=Number(a[4])};}
  for(int end=192;end<bars.Length;end+=13) {
   var b=new MqlRates[192];for(int i=0;i<192;i++)b[i]=bars[end-i];
   if(!FXValidHistory(b,190))throw new Exception("Invalid history: "+file);
   double atr=0;for(int i=1;i<=14;i++)atr+=Math.Max(b[i].high-b[i].low,Math.Max(Math.Abs(b[i].high-b[i+1].close),Math.Abs(b[i].low-b[i+1].close)))/14;
   if(atr<=0)continue;var z=new FXZone[0];
   FXBuildZones(b,2,atr,.30,2,192,true,15,b[1].time,ref z);
   string before="";foreach(var q in z)before+=q.center+":"+q.touches+";";
   b[0].high*=100;b[0].low=.0001;b[0].close=b[0].high;b[1].high*=100;b[1].low=.0001;
   var after=new FXZone[0];FXBuildZones(b,2,atr,.30,2,192,true,15,b[1].time,ref after);
   string check="";foreach(var q in after)check+=q.center+":"+q.touches+";";
   if(before!=check)throw new Exception("Future/forming candle changed confirmed map: "+file);
   windows++;zonesCount+=z.Length;
  }
 }
 Console.WriteLine("{\"passed\":true,\"historical_windows\":"+windows+",\"confirmed_zones\":"+zonesCount+",\"scope\":\"Production closed-bar zone invariance; not execution or performance validation\"}");
 if(windows<100)throw new Exception("Insufficient broker history");
}
}
'''
source=ROOT/'build/HistoryCheck.cs';exe=ROOT/'build/HistoryCheck.exe'
source.write_text(SHIM+production_code()+tail,encoding='utf-8')
subprocess.run([str(COMPILER),'/nologo',f'/out:{exe}',str(source)],check=True,capture_output=True)
run=subprocess.run([str(exe),str(ROOT/'runtime/history')],capture_output=True,text=True,check=True)
report=json.loads(run.stdout)
(ROOT/'runtime/history/native-check.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
