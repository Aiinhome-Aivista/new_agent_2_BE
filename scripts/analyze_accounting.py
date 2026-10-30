import json
from collections import Counter

def main():
    with open("logs/pipeline_steps_1_3_inspection_run_20261029_165954.json", "r", encoding="utf-8") as f:
        s13 = json.load(f)

    audit_recs = s13["step3_candidate_extraction"]["coverage_audit"]["audit_records"]
    merged_recs = [r for r in audit_recs if r.get("status") == "MERGED"]
    print(f"Count of merged records: {len(merged_recs)}")
    raw_names = [r["raw_name"] for r in merged_recs]
    print(f"Unique raw_names among merged: {len(set(raw_names))}")
    c = Counter(raw_names)
    duplicates = {k: v for k, v in c.items() if v > 1}
    print(f"Duplicates among merged raw_names: {duplicates}")
    for r in merged_recs:
        print(f"map_idx: {r['map_index']:3d} | raw_name: {r['raw_name'][:40]:40s} | canonical: {r['canonical_name'][:40]}")

if __name__ == "__main__":
    main()

