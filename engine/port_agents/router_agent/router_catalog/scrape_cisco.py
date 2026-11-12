"""Every Cisco router model from cisco.com's support pages (series pages list each model
with its status). Uses the template agent's real browser (cisco.com blocks plain HTTP).

    python3.11 router_catalog/scrape_cisco.py   -> router_catalog/scraped/cisco.json
"""
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "switch_template_agent"))
from template_agent.sources import _browser_get  # noqa: E402

BASE = "https://www.cisco.com/c/en/us/support/routers/"
SERIES = {   # series name -> support page (hardware routers only: no virtual, software, optical)
    "900 ISR": "900-series-integrated-services-routers-isr/series.html",
    "1000 ISR": "1000-series-integrated-services-routers-isr/series.html",
    "1900 ISR": "1900-series-integrated-services-routers-isr/series.html",
    "2900 ISR": "2900-series-integrated-services-routers-isr/series.html",
    "3900 ISR": "3900-series-integrated-services-routers-isr/series.html",
    "4000 ISR": "4000-series-integrated-services-routers-isr/series.html",
    "8100 Secure Router": "8100-series-secure-routers/series.html",
    "8200 Secure Router": "8200-series-secure-routers/series.html",
    "8300 Secure Router": "8300-series-secure-routers/series.html",
    "8400 Secure Router": "8400-series-secure-routers/series.html",
    "8500 Secure Router": "8500-series-secure-routers/series.html",
    "8600 Secure Router": "8600-series-secure-routers/series.html",
    "ASR 900": "asr-903-series-aggregation-services-routers/series.html",
    "ASR 901": "asr-901-series-aggregation-services-routers/series.html",
    "ASR 901 10G": "asr-901-10g-series-aggregation-services-routers/series.html",
    "ASR 920": "asr-920-series-aggregation-services-router/series.html",
    "ASR 1000": "asr-1000-series-aggregation-services-routers/series.html",
    "ASR 9000": "asr-9000-series-aggregation-services-routers/series.html",
    "800 Series": "800-series-routers/series.html",
    "RV Series (Small Business)": "small-business-rv-series-routers/series.html",
    "8000 Series": "8000-series-routers/series.html",
    "Catalyst 8200 Edge": "catalyst-8200-series-edge-platforms/series.html",
    "Catalyst 8200 Edge uCPE": "catalyst-8200-series-edge-ucpe/series.html",
    "Catalyst 8300 Edge": "catalyst-8300-series-edge-platforms/series.html",
    "Catalyst 8300 Edge uCPE": "catalyst-8300-series-edge-ucpe/series.html",
    "Catalyst 8500 Edge": "catalyst-8500-series-edge-platforms/series.html",
    "Catalyst 8500L Edge": "catalyst-8500l-series-edge-platforms/series.html",
    "NCS 520": "network-convergence-system-520-router/model.html",
    "NCS 540": "network-convergence-system-540-series-routers/series.html",
    "NCS 560": "network-convergence-system-560-series-routers/series.html",
    "NCS 5000": "network-convergence-system-5000-series/series.html",
    "NCS 5500": "network-convergence-system-5500-series/series.html",
    "NCS 5700": "network-convergence-system-5700-series/series.html",
    "NCS 6000": "network-convergence-system-6000-series-router/series.html",
    "Carrier Routing System": "carrier-routing-system/series.html",
    "800 Industrial ISR": "800-series-industrial-routers/series.html",
    "1000 Connected Grid": "1000-series-connected-grid-routers/series.html",
    "2000 Connected Grid": "2010-connected-grid-router/model.html",
    "IR1000 Rugged": "ir1000-series-rugged-secure-routers/series.html",
    "IR1100 Catalyst Rugged": "1101-industrial-integrated-services-router/model.html",
    "IR1800 Catalyst Rugged": "catalyst-ir1800-rugged-series-routers/series.html",
    "IR8100 Catalyst Heavy Duty": "catalyst-ir8100-heavy-duty-series-routers/series.html",
    "IR8300 Catalyst Rugged": "catalyst-ir8300-rugged-series-router/series.html",
    "cBR Converged Broadband": "https://www.cisco.com/c/en/us/support/video/cbr-series-converged-broadband-routers/series.html",
}


def text(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s))).strip()


def scrape(series, path):
    url = path if path.startswith("http") else BASE + path
    h = _browser_get(url, settle_ms=4000) or ""
    flat = text(h)
    m = re.search(r"Status\s+(Available|End of Sale|End of Support|Retired|Orderable)", flat)
    series_status = m.group(1) if m else ""
    models = []
    if path.endswith("model.html"):          # a one-model "series"
        t = re.search(r"<title>(.*?)</title>", h, re.S)
        name = text(t.group(1)).split(" - ")[0].replace("Cisco ", "") if t else series
        pids = max((c.split(",") for c in re.findall(r'content="((?:[A-Z0-9][A-Z0-9/+=.-]{2,},){10,}[^"]*)"', h)),
                   key=len, default=[])
        return series_status, [{"model": name, "status": series_status, "url": url}], [p.strip() for p in pids]
    for u, t in re.findall(r'<a[^>]+href="([^"]*/model\.html)"[^>]*>(.*?)</a>', h, re.S):
        name = text(t).replace("Cisco ", "")
        if not name or len(name) > 90 or any(m["model"] == name for m in models):
            continue
        st = re.search(re.escape(name) + r" Status: ([A-Za-z ]+?)(?: \||$| [0-9A-Z][a-z]+ )", flat)
        models.append({"model": name, "status": st.group(1).strip() if st else series_status,
                       "url": u if u.startswith("http") else "https://www.cisco.com" + u})
    # every product ID of the series, from the page's metadata (incl. bundles and licences;
    # reduced to hardware models by build_full.py)
    pids = max((c.split(",") for c in re.findall(r'content="((?:[A-Z0-9][A-Z0-9/+=.-]{2,},){10,}[^"]*)"', h)),
               key=len, default=[])
    return series_status, models, [p.strip() for p in pids if p.strip()]


def main():
    out = {}
    for series, path in SERIES.items():
        try:
            st, models, pids = scrape(series, path)
        except Exception as e:  # keep going: one bad page must not lose the rest
            st, models, pids = f"error: {e}", [], []
        out[series] = {"status": st, "models": models, "pids": pids}
        print(f"{series}: {len(models)} model pages, {len(pids)} product IDs ({st})", flush=True)
        (HERE / "scraped" / "cisco.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
