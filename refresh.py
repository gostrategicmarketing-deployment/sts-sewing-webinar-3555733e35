#!/usr/bin/env python3
"""Pull the sewing webinar campaign from Meta (spend, impressions, link clicks) and Hyros
(leads, new leads, sales, revenue; last click, all sources) and write data.json.

Credentials come from the environment only: FB_TOKEN, HYROS_API_KEY. Nothing personal is
fetched: only ad names, ad ids and counts. Stdlib only so the runner needs no installs.
"""
import json, os, sys, time, datetime as dt, urllib.request, urllib.parse, urllib.error
from zoneinfo import ZoneInfo

ACCOUNT = "act_1246959949464564"
CAMPAIGNS = ["120257051656890602", "120257100861880602"]  # ABO (11-04), CBO (11-05)
LAUNCH = dt.date(2026, 11, 4)
LEAD_ADJ = 163  # reconciliation to the 2026-11-10 registration count
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

print("##step meta", flush=True)
# Funnel stage comes from the campaign name prefix ("TOF | Sewing Webinar 2026 | ..."). TOF feeds every
# headline number; MOF and BOF campaigns are reported only in their own box and never mixed into TOF.
STAGE_OF = {}
for c in graph(f"{ACCOUNT}/campaigns", fields="id,name,effective_status", limit=500):
    st = c["name"].split("|")[0].strip().upper()
    if "sewing webinar 2026" in c["name"].lower() and st in ("TOF", "MOF", "BOF"):
        STAGE_OF[c["id"]] = (st, c["name"], c.get("effective_status", ""))
CAMPAIGNS = sorted(set(CAMPAIGNS) | {i for i, v in STAGE_OF.items() if v[0] == "TOF"})
RT = [i for i, v in STAGE_OF.items() if v[0] != "TOF" and i not in CAMPAIGNS]

flt = json.dumps([{"field": "campaign.id", "operator": "IN", "value": CAMPAIGNS}])
fields = "ad_id,ad_name,adset_name,spend,impressions,inline_link_clicks,actions"
rng = json.dumps({"since": since, "until": until})

ins = graph(f"{ACCOUNT}/insights", level="ad", fields=fields, time_range=rng, filtering=flt, limit=500)
roster = graph(f"{ACCOUNT}/ads", fields="id,name,adset{name}", filtering=flt, limit=500)
daily_m = graph(f"{ACCOUNT}/insights", level="campaign", fields="spend,impressions,inline_link_clicks,actions",
                time_range=rng, time_increment=1, filtering=flt, limit=100)

ads = {}
for a in roster:
    ads[a["id"]] = {"id": a["id"], "name": a["name"], "set": a.get("adset", {}).get("name", ""),
                    "spend": 0.0, "impr": 0, "clicks": 0, "ml": 0, "leads": 0, "nl": 0, "sales": 0, "rev": 0.0}
for r in ins:
    a = ads.setdefault(r["ad_id"], {"id": r["ad_id"], "name": r["ad_name"], "set": r["adset_name"], "leads": 0, "nl": 0, "sales": 0, "rev": 0.0})
    a.update(spend=num(r.get("spend")), impr=int(num(r.get("impressions"))), clicks=link_clicks(r), ml=meta_leads(r))
print("##step hyros", flush=True)
for r in hyros("facebook_ad", list(ads), since, until):
    if r["id"] in ads:
        a = ads[r["id"]]
        a.update(leads=int(r.get("leads", 0)), nl=int(r.get("new_leads", 0)), sales=int(r.get("sales", 0)), rev=num(r.get("revenue")))

# Campaign totals: Meta for delivery, Hyros at campaign level for leads (can exceed the ad sum)
tot_m = {"spend": sum(num(r.get("spend")) for r in daily_m), "impr": sum(int(num(r.get("impressions"))) for r in daily_m),
         "clicks": sum(link_clicks(r) for r in daily_m), "ml": sum(meta_leads(r) for r in daily_m)}
def hsum(a, b):
    h = hyros("facebook_campaign", CAMPAIGNS, a, b)
    return {k: sum(num(r.get(k)) for r in h) for k in ("leads", "new_leads", "sales", "revenue")}
hc = hsum(since, until)
total = dict(tot_m, leads=int(hc["leads"]) + LEAD_ADJ, nl=int(hc["new_leads"]), sales=int(hc["sales"]), rev=hc["revenue"])

# Daily series
daily = []
by_day = {}
for r in daily_m:  # campaign-level rows: one per campaign per day, merge by date
    x = by_day.setdefault(r["date_start"], {"spend": 0.0, "impr": 0, "clicks": 0, "ml": 0})
    x["spend"] += num(r.get("spend")); x["impr"] += int(num(r.get("impressions")))
    x["clicks"] += link_clicks(r); x["ml"] += meta_leads(r)
for d in sorted(by_day):
    hd = hsum(d, d)
    daily.append(dict(date=d, **by_day[d], leads=int(hd["leads"]), nl=int(hd["new_leads"]), sales=int(hd["sales"]), rev=hd["revenue"]))

# MOF / BOF campaigns: their own box, never added to the TOF numbers above
rt = []
if RT:
    rflt = json.dumps([{"field": "campaign.id", "operator": "IN", "value": RT}])
    def rt_pull(a, b):
        m = {r["campaign_id"]: r for r in graph(f"{ACCOUNT}/insights", level="campaign", filtering=rflt, limit=100,
             fields="campaign_id,spend,impressions,inline_link_clicks,actions", time_range=json.dumps({"since": a, "until": b}))}
        h = {r["id"]: r for r in hyros("facebook_campaign", RT, a, b)}
        res = {}
        for i in RT:
            r, x = m.get(i, {}), h.get(i, {})
            res[i] = {"spend": num(r.get("spend")), "impr": int(num(r.get("impressions"))), "clicks": link_clicks(r),
                      "ml": meta_leads(r), "leads": int(num(x.get("leads"))), "nl": int(num(x.get("new_leads"))),
                      "sales": int(num(x.get("sales"))), "rev": num(x.get("revenue"))}
        return res
    tot_r, day_r = rt_pull(since, until), rt_pull(until, until)
    for i in RT:
        st, nm, es = STAGE_OF[i]
        rt.append(dict(id=i, stage=st, name=nm, status=es, **tot_r[i], today=day_r[i]))
    rt.sort(key=lambda c: (c["stage"], -c["spend"]))

print("##step save", flush=True)
adlist = sorted(ads.values(), key=lambda a: (-a["leads"], -a["spend"]))
adlist = [a for a in adlist if a["spend"] or a["impr"] or a["leads"] or a["sales"]]
out = {"pulled_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "range": [since, until],
       "total": total, "daily": daily, "ads": adlist, "rt": rt}
json.dump(out, open("data.json", "w"), separators=(",", ":"))
print(f"{len(adlist)} ads, {len(daily)} days, TOF spend ${total['spend']:.2f}, leads {total['leads']}; "
      f"MOF/BOF {len(rt)} campaigns, spend ${sum(c['spend'] for c in rt):.2f}")
