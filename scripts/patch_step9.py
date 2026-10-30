with open("new_agent_2_BE/services/step9_inspector.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace commitment blocks to include "name"
content = content.replace(
    '"commitment_name": name or c_name,',
    '"name": name or c_name,\n                    "commitment_name": name or c_name,'
)

content = content.replace(
    '"commitment_name": name or "Client Deliverable Feedback & Deemed Acceptance SLA",',
    '"name": name or "Client Deliverable Feedback & Deemed Acceptance SLA",\n                    "commitment_name": name or "Client Deliverable Feedback & Deemed Acceptance SLA",'
)

content = content.replace(
    '"commitment_name": ms_name,',
    '"name": ms_name,\n                    "commitment_name": ms_name,'
)

with open("new_agent_2_BE/services/step9_inspector.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Step 9 patched successfully!")

