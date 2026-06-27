import pprint
from src.orchestrator import run_scan

history_path = "data/history.csv"
scenarios = [
    ("new_zone", "data/incoming/incoming_new_zone.json"),
    ("spike", "data/incoming/incoming_spike.json"),
    ("data_gap", "data/incoming/incoming_data_gap.json"),
    ("cfr_shift", "data/incoming/incoming_cfr_shift.json"),
    ("multi_signal", "data/incoming/incoming_multi_signal.json"),
]

def main():
    for name, path in scenarios:
        print(f"\n=================== RUNNING SCENARIO: {name} ===================")
        try:
            # Run scan through orchestrator
            result = run_scan(path, history_path)
            print("Status:", result["status"])
            print("\nDRAFTED ALERT:")
            print("-" * 60)
            print(result["alert"])
            print("-" * 60)
            print("\nRanked Flags:")
            pprint.pprint(result["flags"])
            
        except Exception as e:
            print(f"FAILED scenario {name}: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    main()
