from __future__ import annotations
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import io
import os
import re
import time
from pathlib import Path
import requests
from .storage import Store, read_json, fingerprint


class ProviderError(RuntimeError): pass


def verified_macro(source, manifest, now):
    from urllib.parse import urlparse
    hosts = {"BLS": "www.bls.gov", "BEA": "www.bea.gov", "FED": "www.federalreserve.gov"}
    if (source not in hosts or manifest.get("verified") is not True or
        not 0 <= now-manifest.get("checked_at", 0) <= 86400 or
        manifest.get("coverage_from", now+1) > now or manifest.get("coverage_until", 0) < now+30*86400):
        raise ProviderError("VERIFIED_MACRO_MANIFEST_STALE_OR_INCOMPLETE")
    if urlparse(manifest.get("source", "")).hostname != hosts[source]:
        raise ProviderError("VERIFIED_MACRO_SOURCE_INVALID")
    result=[]
    for e in manifest.get("events", []):
        if urlparse(e.get("source", "")).hostname != hosts[source] or not isinstance(e.get("time"), (float,int)):
            raise ProviderError("VERIFIED_MACRO_EVENT_INVALID")
        result.append(e)
    return result


class Providers:
    def __init__(self, cfg, store: Store):
        self.cfg, self.store = cfg, store
        self.session = requests.Session()
        self.model = None
        self._model_signature = None

    def model_signature(self):
        directory = self.cfg["research"].get("sentiment_model_directory")
        if not directory:return None
        if self._model_signature is None:
            root=Path(directory)
            if not root.is_dir():raise ProviderError("PINNED_SENTIMENT_MODEL_MISSING")
            digest=hashlib.sha256()
            for path in sorted(p for p in root.rglob('*') if p.is_file()):
                digest.update(str(path.relative_to(root)).encode())
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            self._model_signature=digest.hexdigest()
        return self._model_signature

    def request(self, provider, url, *, params=None, headers=None, method="GET", data=None, auth=None, allow_redirects=True):
        limit = {"alpha": self.cfg["research"]["alpha_daily_limit"], "marketaux": self.cfg["research"]["marketaux_daily_limit"]}.get(provider)
        if limit:
            self.store.reserve(provider, datetime.now(timezone.utc).date().isoformat(), limit)
        try:
            r = self.session.request(method, url, params=params, headers=headers, data=data, auth=auth, timeout=(5, 15), allow_redirects=allow_redirects)
            if r.status_code != 200: raise ProviderError(f"{provider}: HTTP {r.status_code}")
            return r
        except requests.RequestException:
            # Requests' default error message can include credential-bearing URLs.
            raise ProviderError(f"{provider}: network failure") from None

    def key(self, name):
        value = os.environ.get(name)
        if not value: raise ProviderError(f"{name} not configured")
        return value

    def alpha(self, function, **params):
        r = self.request("alpha", "https://www.alphavantage.co/query", params={"function": function, "apikey": self.key("ALPHAVANTAGE_API_KEY"), **params})
        if function == "EARNINGS_CALENDAR":
            if not r.text.startswith("symbol,"): raise ProviderError("alpha: calendar unavailable or quota response")
            return list(csv.DictReader(io.StringIO(r.text)))
        result = r.json()
        if not result or any(k in result for k in ("Information", "Note", "Error Message")):
            raise ProviderError("alpha: unavailable dataset or quota response")
        return result

    def sec(self, path):
        ua = self.key("SEC_USER_AGENT")
        if "@" not in ua or len(ua.split()) < 2: raise ProviderError("SEC_USER_AGENT requires a real contact name and email")
        time.sleep(.2)
        return self.request("SEC", "https://data.sec.gov/" + path, headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}).json()

    def submissions(self, cik):
        result = self.sec(f"submissions/CIK{cik}.json")
        result['historical'] = []
        # Eight quarters often predate the recent block; preserve exact acceptances.
        for item in result.get('filings', {}).get('files', []):
            name = item.get('name', '')
            if re.fullmatch(r'CIK\d{10}-submissions-\d+\.json', name):
                cache_key='SEC:history:'+name
                cached=self.store.get(cache_key,{})
                if time.time()-cached.get('time',0)>86400:
                    cached={'time':time.time(),'data':self.sec('submissions/'+name)};self.store.set(cache_key,cached)
                result['historical'].append(cached['data'])
                dates = [d for h in result['historical'] for d in h.get('filingDate', [])]
                if dates and min(dates) <= (datetime.now(timezone.utc).date()-timedelta(days=1100)).isoformat(): break
        return result

    def news(self, symbols):
        result = self.request("marketaux", "https://api.marketaux.com/v1/news/all", params={
            "api_token": self.key("MARKETAUX_API_TOKEN"), "symbols": ",".join(symbols),
            "filter_entities": "true", "group_similar": "true", "language": "en", "limit": 3}).json()
        if not isinstance(result.get("data"), list): raise ProviderError("marketaux: invalid response")
        out = []
        retrieved = time.time()
        found=result.get('meta',{}).get('found')
        self.last_news_coverage={'complete': len(result['data'])<3 or isinstance(found,int) and found<=3,
                                 'returned':len(result['data']),'found':found}
        for article in result["data"]:
            if not isinstance(article, dict): continue
            try:
                published = datetime.fromisoformat(article["published_at"].replace("Z", "+00:00"))
                if published.tzinfo is None: raise ValueError("Missing publication timezone")
                stamp = published.timestamp()
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                self.store.audit("NEWS_ROW_REJECTED", {"reason": "INVALID_PUBLICATION_TIME"})
                continue
            entities = article.get("entities")
            if not isinstance(entities, list): continue
            for e in entities:
                if not isinstance(e, dict): continue
                if e.get("symbol") not in symbols or e.get("sentiment_score") is None: continue
                try: score = float(e["sentiment_score"])
                except (TypeError, ValueError): continue
                if isinstance(e["sentiment_score"], bool) or not -1 <= score <= 1: continue
                out.append({"ticker": e["symbol"], "time": stamp, "retrieved_at": retrieved,
                            "provider": "Marketaux", "score": score, "sentiment_valid": True, "reliable": False,
                            "cluster": article.get("uuid") if isinstance(article.get("uuid"), str) and article['uuid'] else str(article.get("url", "")), "publisher": article.get("source", ""),
                            "url": article.get("url", ""), "title": article.get("title", ""),
                            "description": article.get("description", ""), "snippet": article.get("snippet", ""), "author": ""})
        return out

    def classify(self, text):
        directory = self.cfg["research"].get("sentiment_model_directory")
        if not directory: raise ProviderError("PINNED_SENTIMENT_MODEL_NOT_CONFIGURED")
        if self.model is None:
            from transformers import pipeline
            # A local snapshot is mandatory: no mutable remote model execution/download.
            self.model = pipeline("text-classification", model=directory, tokenizer=directory,
                                  model_kwargs={"local_files_only": True}, top_k=None, device=-1)
        scores = self.model(text[:2000], truncation=True)[0]
        labels = {x["label"].lower(): x["score"] for x in scores}
        if not {"positive", "negative", "neutral"} <= labels.keys(): raise ProviderError("UNSUPPORTED_SENTIMENT_LABELS")
        return labels["positive"] - labels["negative"], max(labels.values()) >= .8 and labels["neutral"] < .5

    def social(self, symbols):
        raise ProviderError('REDDIT_RETIRED_USE_OFFICIAL_DOCUMENT_AGENT')

    def macro(self, now):
        """Official ICS feeds plus explicitly verified Fed dates; missing coverage blocks."""
        from icalendar import Calendar
        from .calendar import NY
        events, covered = [], set()
        feeds = {"BLS": "https://www.bls.gov/schedule/news_release/bls.ics",
                 "BEA": "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"}
        for source, url in feeds.items():
            try: raw = self.request(source, url).content
            except ProviderError: continue
            cal = Calendar.from_ical(raw)
            records = list(cal.walk("VEVENT"))
            if not records: raise ProviderError(source + ": empty calendar")
            future = False
            for e in records:
                dt = e.decoded("DTSTART")
                if not isinstance(dt, datetime): continue
                if dt.tzinfo is None: dt = dt.replace(tzinfo=NY)
                stamp = dt.timestamp(); title = str(e.get("SUMMARY", ""))
                future |= stamp >= now
                if re.search(r"consumer price|employment situation|gross domestic|\bGDP\b|personal income|personal consumption", title, re.I):
                    events.append({"time": int(stamp), "name": title, "source": url})
            if future: covered.add(source)
        try:
            events.extend(self.fed_events(now))
            covered.add("FED")
        except (ProviderError, ValueError, KeyError):
            pass
        verified = read_json(Path(self.cfg["research"]["macro_override_file"]), {})
        for source, manifest in verified.get("sources", {}).items():
            if source in covered: continue
            events.extend(verified_macro(source, manifest, now))
            covered.add(source)
        if covered != {"BLS", "BEA", "FED"}: raise ProviderError("MACRO_COVERAGE_INCOMPLETE: " + ",".join(sorted({"BLS", "BEA", "FED"}-covered)))
        return list({(e["time"],e["name"]):e for e in events}.values())

    def fed_events(self, now):
        from bs4 import BeautifulSoup
        from datetime import timedelta
        from .calendar import NY
        import calendar
        url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        soup = BeautifulSoup(self.request("FED", url).text, "html.parser")
        today = datetime.fromtimestamp(now, NY).date()
        dates = set()
        found_year = False
        for panel in soup.select(".panel"):
            heading = panel.select_one(".panel-heading")
            year_match = re.search(r"(20\d{2}) FOMC Meetings", heading.get_text(" ", strip=True) if heading else "")
            if not year_match: continue
            year = int(year_match[1]); found_year |= year == today.year
            for row in panel.select(".fomc-meeting"):
                month = row.select_one(".fomc-meeting__month"); days = row.select_one(".fomc-meeting__date")
                if not month or not days: continue
                month_text = month.get_text(strip=True).split("/")[-1]
                try: month_number = list(calendar.month_name).index(month_text)
                except ValueError: continue
                numbers = re.findall(r"\d+", days.get_text(strip=True))
                if not numbers: continue
                d = datetime(year, month_number, int(numbers[-1])).date()
                if today - timedelta(days=1) <= d <= today + timedelta(days=45): dates.add(d)
        if not found_year: raise ProviderError("FED_MEETING_DATES_UNAVAILABLE")
        result = []
        for year, month in sorted({(d.year, d.month) for d in dates}):
            source = f"https://www.federalreserve.gov/newsevents/{year}-{calendar.month_name[month].lower()}.htm"
            monthly = BeautifulSoup(self.request("FED", source).text, "html.parser")
            result.extend(parse_fed_month(monthly, year, month, dates, source))
        if any(len([e for e in result if datetime.fromtimestamp(e["time"], NY).date() == d]) < 2 for d in dates):
            raise ProviderError("FED_PRECISE_EVENT_TIMES_UNAVAILABLE")
        return result


def parse_fed_month(soup, year, month, expected_dates, source):
    from .calendar import NY
    result = []
    for row in soup.select("div.row"):
        clock, content, days = row.select_one(":scope > .col-xs-2"), row.select_one(":scope > .col-xs-7"), row.select_one(":scope > .col-xs-3")
        if not all((clock, content, days)): continue
        name = content.get_text(" ", strip=True)
        if not re.search(r"FOMC (Meeting|Press Conference)", name) or "Minutes" in name: continue
        match = re.search(r"(\d{1,2}):(\d{2})\s*([ap])\.m\.", clock.get_text(" ", strip=True), re.I)
        if not match: continue
        hour = int(match[1]) % 12 + (12 if match[3].lower() == "p" else 0)
        for day in re.findall(r"\d+", days.get_text(" ", strip=True)):
            dt = datetime(year, month, int(day), hour, int(match[2]), tzinfo=NY)
            if dt.date() in expected_dates:
                result.append({"time": int(dt.timestamp()), "name": "FOMC Press Conference" if "Press Conference" in name and "FOMC Meeting" not in name else "FOMC Decision", "source": source})
    return result
