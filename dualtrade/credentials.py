"""Optional Windows CurrentUser DPAPI vault; environment variables take precedence."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from .storage import write_json, read_json

ALLOWED={'ALPHAVANTAGE_API_KEY','MARKETAUX_API_TOKEN','SEC_USER_AGENT','X_BEARER_TOKEN'}
VAULT=Path(__file__).resolve().parents[1]/'runtime/credentials.dpapi.json'


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def crypt(raw, protect):
    if os.name!='nt':raise RuntimeError('Windows DPAPI vault requires the same Windows user')
    api=getattr(ctypes.windll.crypt32,'CryptProtectData' if protect else 'CryptUnprotectData')
    api.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    api.restype=wintypes.BOOL
    free=ctypes.windll.kernel32.LocalFree;free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
    buffer=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw);source=Blob(len(raw),buffer);target=Blob()
    if not api(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise RuntimeError('Cannot access local credentials as this Windows user')
    try:return ctypes.string_at(target.data,target.size)
    finally:free(target.data)


def save_local_credentials(values, path=None):
    path=Path(path) if path else VAULT
    if set(values)-ALLOWED:raise ValueError('Unsupported credential name')
    existing=read_json(path,{})
    for key,value in values.items():
        if not isinstance(value,str) or not value.strip():raise ValueError('Empty credential')
        existing[key]=crypt(value.encode('utf-16-le'),True).hex()
    write_json(path,existing)


def load_local_credentials(path=None):
    path=Path(path) if path else VAULT
    if not path.exists():return
    if os.name!='nt':raise RuntimeError('Windows DPAPI vault requires the same Windows user')
    for key,value in json.loads(path.read_text(encoding='utf-8-sig')).items():
        if key in ALLOWED and not os.environ.get(key):os.environ[key]=crypt(bytes.fromhex(value),False).decode('utf-16-le')


def status(cfg, verify=False):
    from .config import ROOT
    from .providers import Providers, ProviderError
    from .storage import Store
    import time
    result={'configured':{key:bool(os.environ.get(key)) for key in sorted(ALLOWED)},
            'context_provider':cfg['research'].get('context_provider', 'official_agent'), 'reddit_enabled':False,
            'checks':{},'time':time.time()}
    if verify:
        provider=Providers(cfg,Store(ROOT/'runtime/state.sqlite3'))
        checks={'alpha':lambda:provider.alpha('EARNINGS_CALENDAR',horizon='3month'),
                'marketaux':lambda:provider.news(['AAPL']),
                'SEC':lambda:provider.submissions('0000320193')}
        for name,callback in checks.items():
            try:callback();result['checks'][name]={'ok':True}
            except Exception as exc:result['checks'][name]={'ok':False,'reason':str(exc) if isinstance(exc,ProviderError) else type(exc).__name__}
        result['budgets']=provider.store.budgets()
        write_json(ROOT/'runtime/credential-check.json',result)
    return result
