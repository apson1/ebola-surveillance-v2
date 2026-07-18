"""Terminal demo: print the latest official DRC Ebola situation reports from ReliefWeb.

Run from the repo root:  python -m scripts.fetch_live_sources
Requires RELIEFWEB_APPNAME in .env; otherwise prints the disabled notice.
"""
from src.ingestion.live_sources import fetch_recent_drc_ebola_reports


def main():
    result = fetch_recent_drc_ebola_reports(limit=10)
    print(f"[{result.mode}] {result.note}\n")
    if not result.reports:
        print("(no reports)")
        return
    for r in result.reports:
        day = (r.get("date") or "?")[:10]
        print(f"  {day} | {r.get('source') or '?':10} | {r.get('title')}")
        print(f"           {r.get('url')}")


if __name__ == "__main__":
    main()
