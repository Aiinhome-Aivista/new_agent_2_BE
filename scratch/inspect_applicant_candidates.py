import json

json_path = r"c:\Users\ADMIN\Desktop\Agent-2\new_agent_2_BE\logs\baseline_inspection_p112_d250.json"
with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# Find chunk 5 text
chunks = data.get("step1_document_parsing", {}).get("chunks_sample", [])
# Look in step2 segments
segments = data.get("step2_section_detection", {}).get("all_segments", [])
for s in segments:
    if s.get("chunk_index") in [4, 5, 6]:
        print(f"Chunk {s.get('chunk_index')} seg {s.get('segment_index')} ({s.get('section')}):")
        print(s.get("snippet"))
        print("-" * 40)

# Check the canonical candidate "Admin View - View applicant list"
candidates = data.get("step3_candidate_extraction", {}).get("canonical_candidates", [])
for c in candidates:
    if "applicant" in c.get("name", "").lower() or "applicant" in c.get("description", "").lower():
        print(f"Candidate: {c.get('name')}")
        print(f"  desc: {c.get('description')}")
        print(f"  evidence: {c.get('evidence_snippet')}")
        print(f"  merged_sources: {c.get('merged_sources')}")

