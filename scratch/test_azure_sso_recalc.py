import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from services.risk_scoring_engine import RiskScoringEngine, _parse_due_date

def _is_title_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a_norm = a.lower().strip()
    b_norm = b.lower().strip()
    if a_norm == b_norm:
        return True
    if a_norm in b_norm or b_norm in a_norm:
        return True
        
    keywords = [
        ("azure ad sso", "azure ad single sign-on"),
        ("sso", "single sign-on"),
        ("crm", "crm integration"),
        ("vpn", "vpn access"),
        ("api credentials", "crm api credentials"),
        ("sit", "system integration testing"),
        ("uat", "user acceptance testing"),
    ]
    for kw1, kw2 in keywords:
        if (kw1 in a_norm and kw2 in b_norm) or (kw2 in a_norm and kw1 in b_norm):
            return True

    stop_words = {"for", "and", "the", "with", "to", "of", "in", "a", "an", "on", "is"}
    a_tokens = {w for w in re.findall(r'\b\w+\b', a_norm) if w not in stop_words}
    b_tokens = {w for w in re.findall(r'\b\w+\b', b_norm) if w not in stop_words}
    overlap = a_tokens & b_tokens
    if len(overlap) >= 2:
        return True
    if "sso" in overlap or "crm" in overlap or "vpn" in overlap or "sit" in overlap or "uat" in overlap:
        return True

    return False

conn = get_db_connection()
project_id = 46
completed_title = "Azure AD SSO"

cursor = conn.cursor(dictionary=True)

# 1. Fetch completed scope items and milestones
cursor.execute("SELECT name, scope_item_normalized FROM scope_items WHERE project_id = %s AND completion_status = 'COMPLETED'", (project_id,))
completed_scope_rows = cursor.fetchall() or []
completed_scope_names = {r["name"] for r in completed_scope_rows if r.get("name")} | {r["scope_item_normalized"] for r in completed_scope_rows if r.get("scope_item_normalized")}
if completed_title:
    completed_scope_names.add(completed_title)

print("Completed scope items:", completed_scope_names)

# 2. Mark any open tracker item matching completed scope items as RESOLVED!
cursor.execute("SELECT id, title FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
open_items_check = cursor.fetchall() or []
for it in open_items_check:
    title = it["title"]
    if any(_is_title_match(title, c_name) for c_name in completed_scope_names):
        print(f"Resolving tracker item #{it['id']} '{title}' because scope item is COMPLETED")
        cursor.execute("""
            UPDATE tracker_items 
            SET status = 'RESOLVED', execution_status = 'RESOLVED', risk_status = 'RESOLVED',
                execution_priority_score = 0, risk_score = 0,
                resolution = 'Deliverable completed.', resolved_at = NOW()
            WHERE id = %s
        """, (it["id"],))

conn.commit()

# 3. Now run graph rebuild on the remaining open items!
cursor.execute("SELECT * FROM tracker_items WHERE project_id = %s AND status = 'OPEN'", (project_id,))
open_items = cursor.fetchall() or []

cursor.execute("SELECT title FROM tracker_items WHERE project_id = %s AND status = 'RESOLVED'", (project_id,))
resolved_rows = cursor.fetchall() or []
resolved_titles = {r["title"] for r in resolved_rows if r.get("title")} | completed_scope_names

print(f"\nRemaining OPEN items ({len(open_items)}): {[it['title'] for it in open_items]}")
print(f"Resolved titles: {resolved_titles}")

raw_graph = {}
for item in open_items:
    title = item["title"]
    try:
        r = json.loads(item.get("reasoning") or "{}") if isinstance(item.get("reasoning"), str) else (item.get("reasoning") or {})
    except Exception:
        r = {}
        
    blocks_list = r.get("blocks", [])
    if isinstance(blocks_list, list):
        for b in blocks_list:
            if isinstance(b, str) and b.strip():
                raw_graph.setdefault(title, []).append(b.strip())
                
    chain = r.get("execution_chain", [])
    if isinstance(chain, list) and len(chain) > 1:
        for idx in range(len(chain) - 1):
            src = chain[idx]
            dst = chain[idx + 1]
            if isinstance(src, str) and isinstance(dst, str):
                if dst not in raw_graph.get(src, []):
                    raw_graph.setdefault(src, []).append(dst)

print("\nRAW GRAPH:", raw_graph)

# Filter out resolved titles using _is_title_match
graph = {}
for src, targets in raw_graph.items():
    if any(_is_title_match(src, res) for res in resolved_titles):
        continue
    filtered_targets = []
    for t in targets:
        if not any(_is_title_match(t, res) for res in resolved_titles):
            filtered_targets.append(t)
    if filtered_targets:
        graph[src] = filtered_targets

print("\nFILTERED RUNTIME GRAPH:", graph)

def _bfs_cascade(start_node: str) -> int:
    visited = set()
    queue = [start_node]
    while queue:
        curr = queue.pop(0)
        for child in graph.get(curr, []):
            child_canonical = None
            for k in graph.keys():
                if _is_title_match(k, child):
                    child_canonical = k
                    break
            target_key = child_canonical or child
            if target_key not in visited:
                visited.add(target_key)
                queue.append(target_key)
    return len(visited)

print("\n--- RECALCULATING REMAINING OPEN ITEMS ---")
for item in open_items:
    title = item["title"]
    is_scope_creep = bool(item.get("is_out_of_scope", 0)) or item.get("graph_role") == "SCOPE_CREEP"
    
    if is_scope_creep:
        new_graph_role = "SCOPE_CREEP"
        cascade = 0
    else:
        has_incoming = False
        for src, targets in graph.items():
            if not _is_title_match(src, title):
                for t in targets:
                    if _is_title_match(t, title):
                        has_incoming = True
                        break
                if has_incoming:
                    break
                    
        has_outgoing = False
        for src in graph.keys():
            if _is_title_match(src, title):
                if len(graph[src]) > 0:
                    has_outgoing = True
                    break

        if not has_incoming and has_outgoing:
            new_graph_role = "ROOT_CAUSE"
            cascade = _bfs_cascade(title)
        elif has_incoming and has_outgoing:
            new_graph_role = "INTERMEDIATE_BLOCKER"
            cascade = _bfs_cascade(title)
        elif has_incoming and not has_outgoing:
            new_graph_role = "TERMINAL_ACTIVITY"
            cascade = 0
        else:
            new_graph_role = "ISOLATED"
            cascade = 0

    risk_severity = item.get("risk_severity_score") or item.get("risk_score") or 50
    days_until_due = item.get("days_until_due") or 9999
    owner = item.get("owner") or "Internal"
    
    score_res = RiskScoringEngine.calculate(
        status=item.get("status", "OPEN"),
        blocked_by=[],
        graph_role=new_graph_role,
        cascade_count=cascade,
        is_scope_creep=is_scope_creep,
        days_until_due=days_until_due,
        dependency_owner=owner,
        execution_unlock_count=cascade,
        criticality_score=float(risk_severity),
    )
    new_exec_score = score_res["execution_priority"]

    old_exec_status = item.get("execution_status") or "OPEN"
    if new_graph_role == "ROOT_CAUSE":
        if old_exec_status in ["BLOCKED", "IN_PROGRESS", "OPEN"]:
            new_exec_status = "IN_PROGRESS" if owner != "Customer" else "WAITING_ON_CUSTOMER"
            new_rec_action = f"Prerequisites satisfied ({completed_title} completed). Ready for implementation."
        else:
            new_exec_status = "WAITING_ON_CUSTOMER" if owner == "Customer" else "NOT_STARTED"
            new_rec_action = item.get("recommended_action") or "Prioritize execution."
    elif new_graph_role == "INTERMEDIATE_BLOCKER":
        new_exec_status = "BLOCKED"
        new_rec_action = item.get("recommended_action") or "Awaiting completion of upstream prerequisite."
    elif new_graph_role == "TERMINAL_ACTIVITY":
        new_exec_status = "BLOCKED"
        new_rec_action = item.get("recommended_action") or "Track execution progress."
    else:
        new_exec_status = item.get("execution_status") or "IN_PROGRESS"
        new_rec_action = item.get("recommended_action") or "Track execution progress."

    # Update reasoning JSON
    try:
        r_json = json.loads(item.get("reasoning") or "{}") if isinstance(item.get("reasoning"), str) else (item.get("reasoning") or {})
        if isinstance(r_json, dict):
            if new_graph_role == "ROOT_CAUSE" and old_exec_status in ["BLOCKED", "IN_PROGRESS"]:
                if "business_impact" in r_json and isinstance(r_json["business_impact"], dict):
                    r_json["business_impact"]["immediate"] = "Prerequisites satisfied (CRM and SSO completed). Unblocked and ready for execution."
                r_json["executive_summary"] = f"Prerequisites completed; {title} is now unblocked and ready for implementation."
            updated_reasoning_str = json.dumps(r_json)
        else:
            updated_reasoning_str = item.get("reasoning")
    except Exception:
        updated_reasoning_str = item.get("reasoning")

    print(f"  [GRAPH RECALC] '{title}': graph_role {item.get('graph_role')} -> {new_graph_role} (cascade={cascade}), "
          f"score {item.get('execution_priority_score')} -> {new_exec_score}, status -> {new_exec_status}")
          
    cursor.execute("""
        UPDATE tracker_items
        SET execution_priority_score = %s,
            graph_role = %s,
            execution_status = %s,
            recommended_action = %s,
            reasoning = %s,
            risk_score = %s
        WHERE id = %s
    """, (new_exec_score, new_graph_role, new_exec_status, new_rec_action, updated_reasoning_str, new_exec_score, item["id"]))

conn.commit()
cursor.close()
conn.close()
