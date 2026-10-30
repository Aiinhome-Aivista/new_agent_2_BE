import json

with open('new_agent_2_BE/logs/step4_inspection_p126_d264.json', encoding='utf-8') as f:
    s4 = json.load(f)

recs = [r for r in s4['structured_records'] if r.get('record_type') == 'Vendor Responsibility']
print(f"Total Vendor Responsibilities: {len(recs)}")
for r in recs:
    print(f"ID: {r.get('candidate_id')} | Name: {r.get('name')} | Text: {(r.get('raw_text') or r.get('description'))[:100]}")

