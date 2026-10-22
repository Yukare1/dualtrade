"""Execute production reconciliation/diagnostic bodies with deterministic broker fixtures.

This tests the state machine and ownership checks, not real MT5 server semantics.
"""
import subprocess
from pathlib import Path

import pytest
from native_helpers import COMPILER, SHIM, functions
from test_native import production_code

ROOT = Path(__file__).resolve().parents[1]

BROKER = r'''
static string prefix="test.";
static System.Collections.Generic.Dictionary<string,double> globals=new System.Collections.Generic.Dictionary<string,double>();
struct Position { public ulong id,magic; public double volume; public string symbol; public long type; }
struct Deal { public long entry; public double volume; public ulong magic; public string symbol; }
struct Order { public ulong id,position,magic; public long state,type,setup; }
struct DiagnosticEngine { public long diagnostic_bar; public string diagnostic_reasons,symbol; }
static DiagnosticEngine[] engines=new DiagnosticEngine[1];
static Position[] positions; static Deal[] deals; static Order order; static ulong[] pending;
static int selectedPosition,selectedDeal,selectedOrder,audits;
static bool historyAvailable;
const int POSITION_IDENTIFIER=1,POSITION_MAGIC=2,POSITION_TYPE=3,POSITION_VOLUME=4,POSITION_SYMBOL=5;
const int ORDER_POSITION_ID=6,ORDER_MAGIC=7,ORDER_TIME_SETUP=8,ORDER_STATE=9,ORDER_TYPE=10;
const int DEAL_ENTRY=11,DEAL_VOLUME=12,DEAL_MAGIC=13,DEAL_SYMBOL=14,SYMBOL_VOLUME_STEP=15;
const int DEAL_ENTRY_IN=0,DEAL_ENTRY_OUT=1,DEAL_ENTRY_OUT_BY=3;
const int POSITION_TYPE_BUY=0,ORDER_TYPE_BUY=0,ORDER_TYPE_SELL=1;
const int ORDER_STATE_FILLED=4,ORDER_STATE_CANCELED=2,ORDER_STATE_REJECTED=5,ORDER_STATE_EXPIRED=6;
static double Get(string key,double fallback=0) { double v; return globals.TryGetValue(prefix+key,out v) ? v : fallback; }
static void Set(string key,double value) { globals[prefix+key]=value; }
static int GlobalVariablesTotal() { return globals.Count; }
static string GlobalVariableName(int index) { var keys=new string[globals.Count]; globals.Keys.CopyTo(keys,0);return keys[index]; }
static void GlobalVariablesFlush() {}
static int StringFind(string text,string value) { return text.IndexOf(value,StringComparison.Ordinal); }
static int StringLen(string text) { return text.Length; }
static string StringSubstr(string text,int start,int length=-1) { return length<0 ? text.Substring(start) : text.Substring(start,length); }
static long StringToInteger(string value) { return long.Parse(value); }
static bool Ours(ulong magic) { return magic==1; }
static int PositionsTotal() { return positions.Length; }
static ulong PositionGetTicket(int index) { selectedPosition=index;return positions[index].id; }
static long PositionGetInteger(int key) { var p=positions[selectedPosition];return key==POSITION_IDENTIFIER ? (long)p.id : key==POSITION_MAGIC ? (long)p.magic : p.type; }
static double PositionGetDouble(int key) { return positions[selectedPosition].volume; }
static string PositionGetString(int key) { return positions[selectedPosition].symbol; }
static double SymbolInfoDouble(string symbol,int key) { return .1; }
static int OrdersTotal() { return pending.Length; }
static ulong OrderGetTicket(int index) { selectedOrder=index;return (ulong)(index+1); }
static long OrderGetInteger(int key) { return (long)pending[selectedOrder]; }
static bool HistorySelectByPosition(ulong id) { return historyAvailable && id==42; }
static int HistoryDealsTotal() { return deals.Length; }
static ulong HistoryDealGetTicket(int index) { selectedDeal=index;return (ulong)(index+1); }
static long HistoryDealGetInteger(ulong ticket,int key) { return key==DEAL_ENTRY ? deals[selectedDeal].entry : (long)deals[selectedDeal].magic; }
static double HistoryDealGetDouble(ulong ticket,int key) { return deals[selectedDeal].volume; }
static string HistoryDealGetString(ulong ticket,int key) { return deals[selectedDeal].symbol; }
static bool HistoryOrderSelect(ulong id) { return historyAvailable && order.id==id; }
static long HistoryOrderGetInteger(ulong id,int key) {
  return key==ORDER_POSITION_ID ? (long)order.position : key==ORDER_MAGIC ? (long)order.magic :
     key==ORDER_TIME_SETUP ? order.setup : key==ORDER_STATE ? order.state : order.type;
}
static void Audit(string type,string symbol,string reason,double a=0,double b=0,double c=0) { audits++; }
static string reduction="p.42.red.thesis";
static void Reset() {
 globals.Clear();audits=0;historyAvailable=true;
 positions=new Position[]{new Position{id=42,magic=1,volume=.8,symbol="TEST",type=POSITION_TYPE_BUY}};
 deals=new Deal[]{new Deal{entry=DEAL_ENTRY_IN,volume=1,magic=1,symbol="TEST"},new Deal{entry=DEAL_ENTRY_OUT,volume=.2,magic=1,symbol="TEST"}};
 order=new Order{id=7,position=42,magic=1,setup=100,state=ORDER_STATE_CANCELED,type=ORDER_TYPE_SELL};
 pending=new ulong[0];
 Set(reduction+".sent",100);Set(reduction+".srv",100);Set(reduction+".target",.5);Set(reduction+".step",.1);Set(reduction+".order",7);Set("reduction_pending",1);
}
static void Main() {
 Reset();ReconcileReductions();
 Check("terminal_partial_order_releases_only_residual",Get(reduction+".sent")==0 && Get(reduction+".done")==0 && Get("reduction_pending")==0);
 Reset();positions[0].volume=.5;order.state=ORDER_STATE_FILLED;ReconcileReductions();
 Check("terminal_order_at_target_is_done",Get(reduction+".done")==1 && Get("reduction_pending")==0);
 Reset();Set(reduction+".order",0);ReconcileReductions();
 Check("unknown_timeout_never_retries",Get(reduction+".sent")==100 && Get("reduction_pending")==1);
 Reset();order.position=99;ReconcileReductions();
 Check("another_positions_history_cannot_release",Get("reduction_pending")==1);
 Reset();order.magic=9;ReconcileReductions();
 Check("foreign_order_cannot_release",Get("reduction_pending")==1);
 Reset();order.setup=90;ReconcileReductions();
 Check("old_order_cannot_release",Get("reduction_pending")==1);
 Reset();Set(reduction+".srv",100-18000);order.setup=100-18000;ReconcileReductions();
 Check("negative_broker_offset_uses_server_clock",Get("reduction_pending")==0);
 Reset();order.type=ORDER_TYPE_BUY;ReconcileReductions();
 Check("entry_order_cannot_release_reduction",Get("reduction_pending")==1);
 Reset();pending=new ulong[]{42};ReconcileReductions();
 Check("working_close_order_prevents_residual_retry",Get("reduction_pending")==1);
 Reset();historyAvailable=false;ReconcileReductions();
 Check("missing_history_fails_closed",Get("reduction_pending")==1);
 Reset();positions=new Position[0];deals[1].volume=1;Set(reduction+".order",0);ReconcileReductions();
 Check("subsequent_stop_closure_clears_pending",Get(reduction+".done")==1 && Get("reduction_pending")==0);
 Reset();positions=new Position[0];ReconcileReductions();
 Check("missing_position_without_closed_volume_stays_blocked",Get("reduction_pending")==1);
 Reset();positions=new Position[0];deals[1].volume=1;deals[0].magic=9;ReconcileReductions();
 Check("foreign_position_history_never_clears_pending",Get("reduction_pending")==1);
 Reset();Set("p.99.red.other.sent",100);Set("p.99.red.other.target",.5);Set("p.99.red.other.step",.1);ReconcileReductions();
 Check("resolving_one_record_preserves_other_pending_record",Get(reduction+".sent")==0 && Get("reduction_pending")==1);
 Reset();Set(reduction+".sent",0);ReconcileReductions();
 Check("definitive_rejection_clears_derived_pending_flag",Get("reduction_pending")==0);
 engines[0]=new DiagnosticEngine{symbol="TEST",diagnostic_reasons=""};audits=0;
 Set("bar.TEST.0",12);EntryDiagnostic(0,100,"QUOTE_STALE");EntryDiagnostic(0,100,"QUOTE_STALE");
 Check("same_reason_logged_once_per_bar",audits==1);
 EntryDiagnostic(0,100,"HISTORY");EntryDiagnostic(0,101,"QUOTE_STALE");
 Check("new_reason_and_new_bar_logged",audits==3);
 Check("diagnostics_do_not_consume_signal_bar",Get("bar.TEST.0")==12);
}
}
'''


@pytest.mark.skipif(not COMPILER.exists(), reason='Windows C# compatibility compiler required')
def test_production_reconciliation_and_bounded_diagnostics(tmp_path):
    native = functions(ROOT / 'MQL5/Experts/DualTrade.mq5')
    bodies = []
    for name in ('ReconcileReductions', 'EntryDiagnostic'):
        kind, params, body = native[name]
        bodies.append(f'static {kind} {name}({params}){body}'.replace('datetime', 'long'))
    source = tmp_path / 'Reconciliation.cs'
    exe = tmp_path / 'Reconciliation.exe'
    source.write_text(SHIM + production_code() + '\n'.join(bodies) + BROKER, encoding='utf-8')
    compiled = subprocess.run([str(COMPILER), '/nologo', f'/out:{exe}', str(source)], capture_output=True, text=True, errors='replace', timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True, errors='replace', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('PASS ') == 18
