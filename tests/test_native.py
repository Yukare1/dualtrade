"""Execute actual production MQL function bodies in a C# compatibility host.

This verifies signal/policy logic, not MT5 broker execution semantics.
"""
import re
import subprocess
from pathlib import Path
import pytest
from native_helpers import functions, adapt_body, SHIM, COMPILER

ROOT=Path(__file__).resolve().parents[1]


def production_code():
    include=ROOT/'MQL5/Include'
    funcs={k:v for k,v in functions(include/'Frozen/SRGuardCore.mqh').items() if k in ('PivotHigh','PivotLow')}
    funcs.update({k:v for k,v in functions(include/'Frozen/SRGuardSignalRules.mqh').items() if k in ('RetestConfirmed','PullbackConfirmed','BreakoutQuality','TrendDirection')})
    funcs.update(functions(include/'DualSignals.mqh'))
    funcs.update(functions(include/'DualPolicy.mqh'))
    funcs.update(functions(include/'DualRisk.mqh'))
    funcs.update(functions(include/'DualSwing.mqh'))
    funcs.update(functions(include/'DualAgent.mqh'))
    funcs.update(functions(include/'DualExecution.mqh'))
    refs={'ArrayResize':[0]}; signatures={}
    for name,(kind,params,body) in funcs.items():
        parameters=[];indices=[]
        for index,param in enumerate(params.split(',') if params.strip() else []):
            if '&' in param and 'const ' not in param: indices.append(index)
            param=param.strip().replace('const ','').replace('datetime','long')
            param=re.sub(r'(\w+)\s*&\s*(\w+)\[\]',r'\1[] \2',param).replace('&',' ')
            parameters.append(('ref ' if index in indices else '')+param.strip())
        if indices: refs[name]=indices
        signatures[name]='static '+kind+' '+name+'('+','.join(parameters)+')'
    return '\n'.join(signatures[name]+adapt_body(body.replace('datetime','long'),refs) for name,(_,_,body) in funcs.items())


@pytest.mark.skipif(not COMPILER.exists(),reason='Windows C# compatibility compiler required')
def test_native_strategy_and_risk_invariants(tmp_path):
    source=tmp_path/'Engine.cs';exe=tmp_path/'Engine.exe'
    source.write_text(SHIM+production_code()+(ROOT/'tests/native_cases.cs.txt').read_text(),encoding='utf-8')
    compiled=subprocess.run([str(COMPILER),'/nologo',f'/out:{exe}',str(source)],capture_output=True,text=True,errors='replace',timeout=30)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    result=subprocess.run([str(exe)],capture_output=True,text=True,errors='replace',timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert result.stdout.count('PASS ')>=100
    (ROOT/'build/native-test-results.txt').write_text(result.stdout,encoding='utf-8')
