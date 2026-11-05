#!/usr/bin/env python3
"""Pull both sewing webinar campaigns (ABO and CBO) from Meta (spend, impressions, link clicks) and Hyros
(leads, new leads, sales, revenue; last click, all sources) and write data.json.

Credentials come from the environment only: FB_TOKEN, HYROS_API_KEY. Nothing personal is
fetched: only ad names, ad ids and counts. Stdlib only so the runner needs no installs.
"""
import json, os, sys, time, datetime as dt, urllib.request, urllib.parse, urllib.error
from zoneinfo import ZoneInfo

ACCOUNT = "act_1246959949464564"
# TOF | Sewing Webinar 2026 | ABO, then TOF | Sewing Webinar 2026 | CBO (launched Nov 5)
CAMPAIGNS = ["120257051656890602", "120257100861880602"]
LAUNCH = dt.date(2026, 11, 4)
GRAPH = "https://graph.facebook.com/v21.0"
HYROS = "https://api.hyros.com/v1/api/v1.0/attribution"
FB, HK = os.environ.get("FB_TOKEN", "").strip(), os.environ.get("HYROS_API_KEY", "").strip()
if not FB or not HK:
    sys.exit("FB_TOKEN and HYROS_API_KEY must be set")


def get(url, headers=None, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:300]
            if i == tries - 1 or e.code in (400, 401, 403) and b"transient" not in body:
                sys.exit(f"HTTP {e.code} from {url.split('?')[0]}: {body!r}")
        except Exception as e:
            if i == tries - 1:
                sys.exit(f"{type(e).__name__} from {url.split('?')[0]}: {e}")
        time.sleep(3 * (i + 1))


def graph(path, **p):
    p["access_token"] = FB
    out, url = [], f"{GRAPH}/{path}?" + urllib.parse.urlencode(p)
    while url:
        r = get(url)
        out += r.get("data", [])
        url = r.get("paging", {}).get("next")
    return out


def hyros(level, ids, start, end, fields="leads,new_leads,sales,revenue"):
    out = []
    for i in range(0, len(ids), 20):
        q = urllib.parse.urlencode({"startDate": start, "endDate": end, "attributionModel": "last_click",
                                    "level": level, "fields": fields, "ids": ",".join(ids[i:i + 20])})
        out += get(f"{HYROS}?{q}", {"API-Key": HK, "Accept": "application/json"}).get("result", [])
    return out


def num(x):
    try: return float(x)
    except Exception: return 0.0


def link_clicks(row):
    if "inline_link_clicks" in row: return int(num(row["inline_link_clicks"]))
    return 0


def meta_leads(row):
    for a in row.get("actions", []) or []:
        if a.get("action_type") in ("lead", "offsite_conversion.fb_pixel_lead"):
            return int(num(a["value"]))
    return 0


today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()  # ad account time zone
since, until = LAUNCH.isoformat(), today.isoformat()
flt = json.dumps([{"field": "campaign.id", "operator": "IN", "value": CAMPAIGNS}])
fields = "ad_id,ad_name,adset_name,spend,impressions,inline_link_clicks,actions"
rng = json.dumps({"since": since, "until": until})

ins = graph(f"{ACCOUNT}/insights", level="ad", fields=fields, time_range=rng, filtering=flt, limit=500)
roster = graph(f"{ACCOUNT}/ads", fields="id,name,adset{name}", filtering=flt, limit=500)
daily_m = graph(f"{ACCOUNT}/insights", level="campaign", fields="spend,impressions,inline_link_clicks,actions",
                time_range=rng, time_increment=1, filtering=flt, limit=100)

# level=campaign with time_increment gives one row per campaign per day; merge them by date
by_day = {}
for r in daily_m:
    d = by_day.setdefault(r["date_start"], {"date_start": r["date_start"], "spend": 0.0, "impressions": 0,
                                            "inline_link_clicks": 0, "actions": [{"action_type": "lead", "value": 0}]})
    d["spend"] += num(r.get("spend")); d["impressions"] += int(num(r.get("impressions")))
    d["inline_link_clicks"] += link_clicks(r); d["actions"][0]["value"] += meta_leads(r)
daily_m = list(by_day.values())


def hyros_sum(start, end):
    t = {"leads": 0, "new_leads": 0, "sales": 0, "revenue": 0.0}
    for r in hyros("facebook_campaign", CAMPAIGNS, start, end):
        for k in t: t[k] += num(r.get(k, 0))
    return t


ads = {}
for a in roster:
    ads[a["id"]] = {"id": a["id"], "name": a["name"], "set": a.get("adset", {}).get("name", ""),
                    "spend": 0.0, "impr": 0, "clicks": 0, "ml": 0, "leads": 0, "nl": 0, "sales": 0, "rev": 0.0}
for r in ins:
    a = ads.setdefault(r["ad_id"], {"id": r["ad_id"], "name": r["ad_name"], "set": r["adset_name"], "leads": 0, "nl": 0, "sales": 0, "rev": 0.0})
    a.update(spend=num(r.get("spend")), impr=int(num(r.get("impressions"))), clicks=link_clicks(r), ml=meta_leads(r))
for r in hyros("facebook_ad", list(ads), since, until):
    if r["id"] in ads:
        a = ads[r["id"]]
        a.update(leads=int(r.get("leads", 0)), nl=int(r.get("new_leads", 0)), sales=int(r.get("sales", 0)), rev=num(r.get("revenue")))

# Campaign totals: Meta for delivery, Hyros at campaign level for leads (can exceed the ad sum)
tot_m = {"spend": sum(num(r.get("spend")) for r in daily_m), "impr": sum(int(num(r.get("impressions"))) for r in daily_m),
         "clicks": sum(link_clicks(r) for r in daily_m), "ml": sum(meta_leads(r) for r in daily_m)}
hc = hyros_sum(since, until)
total = dict(tot_m, leads=int(hc.get("leads", 0)), nl=int(hc.get("new_leads", 0)), sales=int(hc.get("sales", 0)), rev=num(hc.get("revenue")))

# Daily series
daily = []
for r in sorted(daily_m, key=lambda r: r["date_start"]):
    d = r["date_start"]
    hd = hyros_sum(d, d)
    daily.append({"date": d, "spend": num(r.get("spend")), "impr": int(num(r.get("impressions"))), "clicks": link_clicks(r),
                  "ml": meta_leads(r), "leads": int(hd.get("leads", 0)), "nl": int(hd.get("new_leads", 0)),
                  "sales": int(hd.get("sales", 0)), "rev": num(hd.get("revenue"))})

adlist = sorted(ads.values(), key=lambda a: (-a["leads"], -a["spend"]))
adlist = [a for a in adlist if a["spend"] or a["impr"] or a["leads"] or a["sales"]]
out = {"pulled_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "range": [since, until],
       "total": total, "daily": daily, "ads": adlist}
json.dump(out, open("data.json", "w"), separators=(",", ":"))
print(f"{len(adlist)} ads, {len(daily)} days, spend ${total['spend']:.2f}, leads {total['leads']}")
