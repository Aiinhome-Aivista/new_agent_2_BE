import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection
from services.risk_scoring_engine import RiskScoringEngine, _parse_due_date

conn = get_db_connection()
project_id = 46

def test_graph_recalc(db, project_id, completed_title="CRM Integration for customer information and ticket"):
    cursor = db.cursor(dictionary=True)
    
    # Fetch all current OPEN tracker_items
    cursor.execute("""
        SELECT * FROM tracker_items 
        WHERE project_id = %s AND status = 'OPEN'
    """, (project_id,))
    open_items = cursor.fetchall() or []
    
    cursor.execute("""
        SELECT title FROM tracker_items 
        WHERE project_id = %s AND status = 'RESOLVED'
    """, (project_id,))
    resolved_rows = cursor.fetchall() or []
    resolved_titles = {r["title"].lower().strip() for r in resolved_rows if r.get("title")}
    if completed_title:
        resolved_titles.add(completed_title.lower().strip())
        
    print(f"OPEN ITEMS ({len(open_items)}): {[it['title'] for it in open_items]}")
    print(f"RESOLVED TITLES: {resolved_titles}")

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

    # Filter out completed items
    graph = {}
    for src, targets in raw_graph.items():
        src_norm = src.lower().strip()
        if src_norm in resolved_titles:
            continue
        filtered_targets = []
        for t in targets:
            t_norm = t.lower().strip()
            if t_norm not in resolved_titles:
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
                    if k.lower().strip() == child.lower().strip() or k.lower() in child.lower() or child.lower() in k.lower():
                        child_canonical = k
                        break
                target_key = child_canonical or child
                if target_key not in visited:
                    visited.add(target_key)
                    queue.append(target_key)
        return len(visited)

    recalculated = []
    print("\n--- RECALCULATING OPEN ITEMS ---")
    for item in open_items:
        title = item["title"]
        title_norm = title.lower().strip()
        is_scope_creep = bool(item.get("is_out_of_scope", 0)) or item.get("graph_role") == "SCOPE_CREEP"
        
        if is_scope_creep:
            new_graph_role = "SCOPE_CREEP"
            cascade = 0
        else:
            has_incoming = False
            for src, targets in graph.items():
                if src.lower().strip() != title_norm:
                    for t in targets:
                        if t.lower().strip() == title_norm or t.lower() in title_norm or title_norm in t.lower():
                            has_incoming = True
                            break
                    if has_incoming:
                        break
                        
            has_outgoing = False
            for src in graph.keys():
                if src.lower().strip() == title_norm or src.lower() in title_norm or title_norm in src.lower():
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
        
        if days_until_due == 9999:
            try:
                r_obj = json.loads(item.get("reasoning") or "{}") if isinstance(item.get("reasoning"), str) else (item.get("reasoning") or {})
                due_date_str = r_obj.get("due_date")
                if due_date_str:
                    d_parsed = _parse_due_date(due_date_str)
                    if d_parsed is not None:
                        days_until_due = d_parsed
            except Exception:
                pass

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
                new_rec_action = "Prerequisites satisfied (CRM Integration completed). Ready for implementation."
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

        print(f"  [GRAPH RECALC] '{title}': graph_role {item.get('graph_role')} -> {new_graph_role} (cascade={cascade}), "
              f"score {item.get('execution_priority_score')} -> {new_exec_score}, status -> {new_exec_status}")
              
        cursor.execute("""
            UPDATE tracker_items
            SET execution_priority_score = %s,
                graph_role = %s,
                execution_status = %s,
                recommended_action = %s,
                risk_score = %s
            WHERE id = %s
        """, (new_exec_score, new_graph_role, new_exec_status, new_rec_action, new_exec_score, item["id"]))

    db.commit()
    cursor.close()

test_graph_recalc(conn, project_id)
conn.close()
