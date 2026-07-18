"""Phase 2 demo: fetch a ReliefWeb report body by id, extract candidate records, validate them
with the independent checker, and print the results with snippets and reject reasons.

Run from the repo root:
    python -m scripts.extract_from_url <reliefweb_report_id> [report_date]
    e.g.  python -m scripts.extract_from_url 4221419 2026-07-14

Makes live Gemini calls (one extract + one validate). Writes nothing to history.
"""
import sys

from src.ingestion.live_sources import fetch_report_body
from src.live.extract_report import extract_report
from src.live.validate_extraction import validate_extraction


def main():
    if len(sys.argv) < 2:
        print("usage: python -m scripts.extract_from_url <reliefweb_report_id> [report_date]")
        return
    report_id = sys.argv[1]
    report_date = sys.argv[2] if len(sys.argv) > 2 else ""
    report_url = f"https://reliefweb.int/node/{report_id}"

    body = fetch_report_body(report_id)
    if not body:
        print(f"No body fetched for report {report_id} (appname unset or unavailable).")
        return
    print(f"Fetched body ({len(body)} chars) for report {report_id}\n")

    extracted = extract_report(body, report_url, report_date)
    print(f"EXTRACTION: {extracted.note}")
    for d in extracted.dropped:
        print(f"  dropped ({d['reason']})")
    if not extracted.records:
        print("  no candidate records extracted.")
        return

    validated = validate_extraction(extracted.records, body)
    print(f"\nVALIDATION: {validated.note}\n")

    print("--- validated candidate records ---")
    for r in validated.validated:
        print(f"  {r['health_zone']} ({r['province']}) {r['date']}: "
              f"confirmed={r['confirmed_cases']} deaths={r['deaths']} suspected={r['suspected_cases']}")
        print(f"     snippet: {r['snippet']!r}")
    if validated.rejected:
        print("\n--- rejected ---")
        for rej in validated.rejected:
            print(f"  {rej['record'].get('health_zone')}: {rej['reason']}")


if __name__ == "__main__":
    main()
