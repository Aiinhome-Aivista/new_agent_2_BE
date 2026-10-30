import sys, os, json
sys.path.insert(0, 'new_agent_2_BE')

from services.step8_inspector import Step8Inspector
from services.deadline_inference_service import DeadlineInferenceService

with open('new_agent_2_BE/logs/step4_inspection_p126_d264.json', encoding='utf-8') as f:
    s4 = json.load(f)

records = s4['structured_records']

# Filter items:
delivery_items = []
for r in records:
    st = (r.get('scope_status') or r.get('scope_type') or '').upper()
    if st != 'IN_SCOPE':
        continue
    rtype = r.get('record_type') or ''
    if rtype == 'Milestone':
        continue
    if rtype == 'Vendor Responsibility':
        desc = (r.get('description') or '').lower()
        nm = (r.get('name') or '').lower()
        # Governance check
        if any(k in desc or k in nm for k in ['steering', 'fee', 'powers to decide', 'member', 'decide on project']):
            continue
    if r.get('work_item_eligible') is True:
        delivery_items.append(r)

print('Delivery items to schedule:', len(delivery_items))

milestones = [
    {"name": "Assessment + Implementation Phase", "planned_date": "2027-02-15", "simulated_id": 1, "module": "General"},
    {"name": "Internal Job Posting - Implement & UAT Deployment", "planned_date": "2027-02-11", "simulated_id": 2, "module": "Internal Job Posting"},
    {"name": "Employee Collaboration - Implement & UAT Deployment", "planned_date": "2027-02-12", "simulated_id": 3, "module": "Employee Collaboration"},
    {"name": "UAT Phase", "planned_date": "2027-03-31", "simulated_id": 4, "module": "General"},
    {"name": "Employee Collaboration - UAT Sign-Off & Go-Live", "planned_date": "2027-03-18", "simulated_id": 5, "module": "Employee Collaboration"},
    {"name": "Employee Collaboration - Post Go-live Hyper care support", "planned_date": "2027-04-01", "simulated_id": 6, "module": "Employee Collaboration"},
    {"name": "Hyper-care Phase", "planned_date": "2027-04-30", "simulated_id": 7, "module": "General"},
    {"name": "Overall Project Timeline", "planned_date": "2027-04-30", "simulated_id": 8, "module": "General"},
]

scheduled, conflicts = DeadlineInferenceService.schedule_work_items(
    in_scope_items=delivery_items,
    milestones=milestones,
    timeline_facts={"contract_start": "2027-01-22", "contract_end": "2027-04-30", "support_end": "2027-07-31"}
)

print('Scheduled count:', len(scheduled))
print('Conflicts count:', len(conflicts))
print('First scheduled item:', scheduled[0]['item_name'], scheduled[0]['predicted_deadline'])
print('Last scheduled item:', scheduled[-1]['item_name'], scheduled[-1]['predicted_deadline'])

