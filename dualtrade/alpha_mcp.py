"""Stdio gateway to the official remote Alpha Vantage MCP, sharing our quota.

Only one-call fundamental/calendar tools are exposed. No execution tools or
unbounded bulk-query tools. Credentials are read from the local environment.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timezone
import requests
from .config import load, ROOT
from .storage import Store

ALLOWED={"OVERVIEW","INCOME_STATEMENT","BALANCE_SHEET","CASH_FLOW","EARNINGS","EARNINGS_CALENDAR"}


class Gateway:
    def __init__(self, cfg, store):
        self.cfg,self.store=cfg,store
        self.session=requests.Session();self.session_id=None

    def forward(self, message):
        key=os.environ.get("ALPHAVANTAGE_API_KEY")
        if not key:raise ValueError("ALPHAVANTAGE_API_KEY not configured locally")
        method=message.get("method")
        if method=="tools/call":
            if message.get("params",{}).get("name") not in ALLOWED:raise ValueError("Only budgeted fundamental/calendar research tools are enabled")
            self.store.reserve("alpha",datetime.now(timezone.utc).date().isoformat(),self.cfg["research"]["alpha_daily_limit"])
        elif method not in ("initialize","notifications/initialized","tools/list","ping"):raise ValueError("Unsupported research method")
        headers={"Accept":"application/json, text/event-stream","Content-Type":"application/json"}
        if self.session_id:headers["Mcp-Session-Id"]=self.session_id
        response=self.session.post("https://mcp.alphavantage.co/mcp",params={"apikey":key},headers=headers,json=message,timeout=(5,30))
        if response.status_code not in (200,202):raise ValueError("Official MCP unavailable or authentication required")
        self.session_id=response.headers.get("Mcp-Session-Id",self.session_id)
        if "id" not in message:return None
        if response.headers.get("Content-Type","").startswith("text/event-stream"):
            matches=[json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith("data:")]
            result=next(x for x in matches if x.get("id")==message["id"])
        else:result=response.json()
        if method=="tools/list" and "result" in result:
            result["result"]["tools"]=[t for t in result["result"].get("tools",[]) if t["name"] in ALLOWED]
        # Never let an upstream diagnostic echo the credential-bearing request URL.
        return json.loads(json.dumps(result).replace(key,"[REDACTED]"))


def main():
    from .credentials import load_local_credentials
    load_local_credentials()
    gateway=Gateway(load(),Store(ROOT/"runtime/state.sqlite3"))
    for line in sys.stdin:
        request={}
        try:
            request=json.loads(line);answer=gateway.forward(request)
        except Exception as exc:
            answer={"jsonrpc":"2.0","id":request.get("id"),"error":{"code":-32000,"message":str(exc) if isinstance(exc,ValueError) else "Research gateway unavailable; check local credentials/quota"}}
        if answer is not None and "id" in request:
            print(json.dumps(answer),flush=True)


if __name__=="__main__":main()
