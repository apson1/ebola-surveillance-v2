"""The single authoritative definition of the history/candidate data contract (Phase B).

This is a LEAF module: it must NOT import from ingestion.py, history_store.py, candidate_store.py
(or anything else in the project). Those modules import these constants FROM here, so keeping this
module dependency-free is what guarantees no circular import can ever surface.

Phase B added `disaster_id` as the leading partition key so one history file can hold multiple
outbreaks. Columns are addressed by NAME everywhere, so the leading position is purely for human
readability (it reads as "which outbreak is this row").
"""

# 9-column contract. `disaster_id` first (partition key), then the original eight.
CONTRACT_COLUMNS = [
    "disaster_id",
    "date", "province", "health_zone",
    "suspected_cases", "confirmed_cases", "deaths",
    "source_url", "report_date",
]

# Count columns: suspected_cases may be null (live-promoted rows); confirmed/deaths never null.
COUNT_COLUMNS = ["suspected_cases", "confirmed_cases", "deaths"]

# Identity / dedup key. Includes disaster_id so the same zone/date/source in two different
# outbreaks does not collide in the shared file (upsert keeps them distinct).
IDENTITY_COLUMNS = ["disaster_id", "date", "province", "health_zone", "source_url"]
