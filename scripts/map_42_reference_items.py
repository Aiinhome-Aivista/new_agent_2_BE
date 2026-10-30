import json
import re

ref_items = [
    (1, "Single landing page for employees within Microsoft 365 tenant"),
    (2, "Announcements, key initiatives and upcoming events display"),
    (3, "Notifications as per user activities related to the site"),
    (4, "Organizational resource links and video in landing page"),
    (5, "Employee section — praising, onboarding, upcoming birthdays"),
    (6, "Employee specific learning section in landing page"),
    (7, "News section for employees"),
    (8, "Poll or Survey section scheduled by site admin"),
    (9, "External Learning section for employees"),
    (10, "Suggestion or feedback section for employees"),
    (11, "Footer section for the site"),
    (12, "Email notification — training assignment, due, completed, birthday wishes"),
    (13, "Admin section — training compliance reports"),
    (14, "Admin section — employee praising reports via Viva Engage"),
    (15, "Admin section — create and assign training for employees"),
    (16, "Admin section — view employee suggestions and feedbacks"),
    (17, "Admin section — create polls for employees"),
    (18, "Admin section — onboarding message for new employees"),
    (19, "UI/UX design changes"),
    (20, "Automated reminder emails for due compliance trainings"),
    (21, "Digitization of the MFR form"),
    (22, "Access control to post a job requisition"),
    (23, "Access control to view posted job requisitions per GL designation hierarchy"),
    (24, "Post a job on behalf of someone else (with designation hierarchy)"),
    (25, "Tabular view of recently posted job requisitions per user access"),
    (26, "Edit specific fields for active but not-approved job requisitions"),
    (27, "Delete a job requisition and remove from display list"),
    (28, "Download job requisitions per user access and designation"),
    (29, "Admin tabular view of all job requisitions"),
    (30, "Admin approve or reject job requisitions"),
    (31, "Email trigger on status change by Admin"),
    (32, "Admin send status to owner and reporting manager"),
    (33, "Admin view list of applicants for any job requisition"),
    (34, "Admin download job requisition list"),
    (35, "Admin download applicants list for any job requisition"),
    (36, "Admin view applicant details and recommend or reject applicant"),
    (37, "View job requisition summary and apply per eligibility"),
    (38, "Redirect to applicant form on Apply click"),
    (39, "Prevent duplicate application for same job by same user"),
    (40, "Success confirmation message on application"),
    (41, "Email to user with job request details on successful application"),
    (42, "Admin reporting section — on-demand training reports"),
]

s4 = json.load(open("logs/step4_inspection_p123_d261.json", encoding="utf-8"))
records = s4.get("structured_records", [])

s8 = json.load(open("logs/step8_milestones_dag_p123_d261.json", encoding="utf-8"))
sched_items = {item.get("candidate_id"): item for item in s8.get("work_items_schedule", [])}

mapping = []
for num, ref in ref_items:
    ref_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", ref.lower())) - {"for", "and", "the", "section", "view", "with"}
    best_cand = None
    best_score = 0
    for r in records:
        name_desc = ((r.get("name") or "") + " " + (r.get("description") or "")).lower()
        score = sum(1 for w in ref_words if w in name_desc)
        if score > best_score:
            best_score = score
            best_cand = r

    cid = best_cand.get("candidate_id") if best_cand else None
    sched = sched_items.get(cid, {})

    mapping.append({
        "ref_num": num,
        "ref_text": ref,
        "candidate_id": cid,
        "candidate_name": best_cand.get("name") if best_cand else "UNMATCHED",
        "section": best_cand.get("section") if best_cand else None,
        "work_eligible": best_cand.get("work_item_eligible") if best_cand else False,
        "predicted_deadline": sched.get("proposed_deadline") or sched.get("estimated_deadline"),
        "date_provenance": sched.get("date_provenance"),
    })

print(f"Total Reference Capabilities: {len(mapping)}")
for m in mapping:
    num_str = f"#{m['ref_num']:02d}"
    print(f"{num_str} | {m['candidate_id']} | {m['candidate_name'][:35]:<35} | {m['section']:<16} | Eligible: {m['work_eligible']} | Date: {m['predicted_deadline']} ({m['date_provenance']})")

with open("logs/reference_42_mapping.json", "w", encoding="utf-8") as f:
    json.dump(mapping, f, indent=2)

