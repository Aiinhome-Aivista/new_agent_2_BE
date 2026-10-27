import json
import re

json_path = r"c:\Users\ADMIN\Desktop\Agent-2\new_agent_2_BE\logs\baseline_inspection_p112_d250.json"
log_path = r"c:\Users\ADMIN\Desktop\Agent-2\new_agent_2_BE\logs\baseline_p112_d250.log.txt"

with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

s3 = data.get("step3_candidate_extraction", {})
audit = s3.get("coverage_audit", {})
prov = s3.get("provenance_summary", {})
candidates = s3.get("canonical_candidates", [])
sec_breakdown = s3.get("section_breakdown", {})

print("=" * 60)
print("FINDING 1: Log vs JSON verification count")
print("=" * 60)
print(f"JSON canonical_candidates_count: {s3.get('canonical_candidates_count')}")
print(f"JSON candidates array len: {len(candidates)}")
print(f"JSON provenance_summary: {prov}")
prov_counts = {}
for c in candidates:
    st = c.get("provenance_status", "UNKNOWN")
    prov_counts[st] = prov_counts.get(st, 0) + 1
print(f"Direct count of provenance_status across candidates: {prov_counts}")

# Check log for "Verified:" or "Canonical Candidates Verified"
with open(log_path, "r", encoding="utf-8") as f:
    log_text = f.read()

log_verified_matches = re.findall(r".*Canonical Candidates Verified.*", log_text)
print("Log lines matching 'Canonical Candidates Verified':")
for m in log_verified_matches:
    print("  ", m.strip())

log_step3_complete = re.findall(r".*Step 3.*Candidates.*", log_text, re.IGNORECASE)
print("Log lines mentioning Step 3 Candidates:")
for m in log_step3_complete[-10:]:
    print("  ", m.strip())

print("\n" + "=" * 60)
print("FINDING 2: 'Admin View - View applicant list' candidate")
print("=" * 60)
f2_candidates = [c for c in candidates if "applicant" in c.get("name", "").lower() or "applicant" in c.get("description", "").lower()]
for c in f2_candidates:
    if "list" in c.get("name", "").lower() or "view" in c.get("name", "").lower() or "applicant" in c.get("name", "").lower():
        print(f"Name: {c.get('name')}")
        print(f"  Description: {c.get('description')}")
        print(f"  Section: {c.get('section')}")
        print(f"  Status: {c.get('provenance_status')}")
        print(f"  Evidence: {c.get('evidence_snippet')}")
        print(f"  Reason: {c.get('verification_reason')}")
        print("  ---")

print("\n" + "=" * 60)
print("FINDING 3: Evidence text for 'Steering Committee Constitution' and 'Remote work during travel restrictions'")
print("=" * 60)
f3_candidates = [c for c in candidates if any(k in c.get("name", "").lower() or k in c.get("description", "").lower() for k in ["constitution", "remote work", "travel restriction"])]
for c in f3_candidates:
    print(f"Name: {c.get('name')}")
    print(f"  Description: {c.get('description')}")
    print(f"  Section: {c.get('section')}")
    print(f"  Status: {c.get('provenance_status')}")
    print(f"  Evidence: {repr(c.get('evidence_snippet'))}")
    print(f"  Reason: {c.get('verification_reason')}")
    print(f"  Chunk index: {c.get('chunk_index')}, Locator: {c.get('source_locator')}")
    print("  ---")

print("\n" + "=" * 60)
print("FINDING 4: Merged candidates accounting")
print("=" * 60)
print(f"Audit Total MAP Candidates: {audit.get('total_map_candidates')}")
print(f"Audit Total Canonical Candidates: {audit.get('total_canonical_candidates')}")
print(f"Audit Retained Count: {audit.get('retained_count')}")
print(f"Audit Merged Count: {audit.get('merged_count')}")
print(f"Audit Excluded Count: {audit.get('excluded_count')}")
print(f"Audit Unresolved Count: {audit.get('unresolved_count')}")

merged_records = [c for c in candidates if c.get("merged_sources")]
total_merged_sources = sum(len(c.get("merged_sources", [])) for c in candidates)
print(f"Canonical candidates with non-empty merged_sources: {len(merged_records)}")
print(f"Sum of len(c['merged_sources']) across all canonical candidates: {total_merged_sources}")
print("Details of records with merged_sources:")
for c in merged_records:
    print(f"  Name: {c.get('name')} | len(merged_sources): {len(c.get('merged_sources', []))}")
    for ms in c.get("merged_sources", []):
        print(f"    - from: {ms.get('name')} | locator: {ms.get('source_locator') or ms.get('chunk_index')} | desc: {ms.get('description')[:50]}...")

print("\n" + "=" * 60)
print("FINDING 6: Review of the Four Partial Candidates & Unmapped Headings")
print("=" * 60)
partial_candidates = [c for c in candidates if c.get("provenance_status") == "PARTIALLY_VERIFIED"]
print(f"Total partially verified: {len(partial_candidates)}")
for c in partial_candidates:
    print(f"Name: {c.get('name')}")
    print(f"  Section: {c.get('section')}")
    print(f"  Description: {c.get('description')}")
    print(f"  Evidence: {c.get('evidence_snippet')}")
    print(f"  Reason: {c.get('verification_reason')}")
    print(f"  Locator: {c.get('source_locator')}")
    print("  ---")

step2 = data.get("step2_section_detection", {})
print("Step 2 unmapped headings:", step2.get("unmapped_headings"))
print("Step 2 categories detected:", step2.get("categories_detected"))
print("Step 2 detected sections:", step2.get("detected_sections"))

