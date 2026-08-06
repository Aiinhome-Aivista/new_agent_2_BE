import collections

class DependencyGraphBuilder:
    @classmethod
    def build_and_enrich(cls, candidates: list) -> list:
        """
        Builds a deterministic dependency graph mapping blocked relationships.
        Programmatically traverses the dependency chain to assign:
        - immediate_unlocks
        - future_unlocks
        - cascade_count
        - is_root_cause
        
        Also helps determine semantic roles (Root Cause vs Waiting Dependency vs Execution Blocker).
        """
        # Graph: blocker_name -> list of blocked_names
        graph = collections.defaultdict(list)
        
        name_to_candidate = {}
        for cand in candidates:
            name = cand.get('activity')
            if not name:
                continue
            name_to_candidate[name] = cand
            
            # 'blocked_by' usually comes from the LLM or previous engine as a list of strings
            blocked_by = cand.get('blocked_by', [])
            if isinstance(blocked_by, list):
                for blocker in blocked_by:
                    # Fuzzy match the blocker to a known candidate name
                    blocker_lower = blocker.lower().strip()
                    matched_name = blocker
                    # Try to find a matching candidate
                    found = False
                    for cand_name in name_to_candidate.keys():
                        cand_name_lower = cand_name.lower().strip()
                        if blocker_lower in cand_name_lower or cand_name_lower in blocker_lower:
                            matched_name = cand_name
                            found = True
                            break
                    graph[matched_name].append(name)
                    # Also update the original list so the UI shows the canonical connection
                    if matched_name != blocker:
                        # Replace in place safely
                        idx = cand['blocked_by'].index(blocker)
                        cand['blocked_by'][idx] = matched_name
                        
                    # If this blocker is purely textual and doesn't exist as a candidate, auto-spawn it as an ACTION_ITEM
                    if not found and matched_name not in name_to_candidate:
                        name_to_candidate[matched_name] = {
                            "activity": matched_name,
                            "canonical_title": matched_name,
                            "entity_type": "ACTION_ITEM",
                            "status": "OPEN", # Active operational task
                            "blocked_by": [],
                            "evidence": f"Identified as a blocker for {name}.",
                            "reasoning": f"This operational action item is preventing progress on {name}.",
                            "recommended_action": f"Resolve this item to immediately unblock {name}.",
                            "should_create_risk": True,
                            "is_scope_creep": False,
                            "llm_confidence": 100.0,
                            "progress": 0,
                            "p_date_str": None,
                            "days_overdue": 0,
                            "days_until_due": 0,
                            "cascade_count": 0,
                            "is_root_cause": True,
                            "next_milestone_name": None,
                            "next_milestone_date": None,
                            "days_to_next_milestone": None,
                            "original_contract_sentence": ""
                        }
                    
        # Traverse graph to find all downstream elements
        def get_all_downstream(start_node):
            visited = set()
            queue = collections.deque([start_node])
            while queue:
                curr = queue.popleft()
                for neighbor in graph.get(curr, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            return list(visited)

        def get_longest_path(start_node):
            max_path = []
            def dfs(curr_node, current_path, visited):
                nonlocal max_path
                neighbors = graph.get(curr_node, [])
                if not neighbors:
                    if len(current_path) > len(max_path):
                        max_path = list(current_path)
                    return
                for neighbor in neighbors:
                    if neighbor not in visited:
                        current_path.append(neighbor)
                        visited.add(neighbor)
                        dfs(neighbor, current_path, visited)
                        visited.remove(neighbor)
                        current_path.pop()
                    else:
                        if len(current_path) > len(max_path):
                            max_path = list(current_path)
            dfs(start_node, [], set())
            return max_path

        for name, cand in name_to_candidate.items():
            immediate = graph.get(name, [])
            all_downstream = get_all_downstream(name)
            longest_path = get_longest_path(name)
            
            # Future unlocks are downstream items that aren't immediately unlocked
            future = [f for f in all_downstream if f not in immediate]
            
            cand['immediate_unlocks'] = immediate
            cand['future_unlocks'] = future
            cand['longest_path'] = longest_path
            cand['downstream_names'] = all_downstream
            cand['cascade_count'] = len(all_downstream)
            cand['is_direct_blocker'] = len(immediate) > 0
            
            # A true root cause is an item that blocks something, but isn't itself blocked by an UNRESOLVED item.
            blocked_by_list = cand.get('blocked_by', [])
            has_unresolved_blockers = False
            for blocker in blocked_by_list:
                b_cand = name_to_candidate.get(blocker)
                if b_cand:
                    b_status = b_cand.get('status', 'OPEN')
                    if b_status not in ['RESOLVED', 'COMPLETED']:
                        has_unresolved_blockers = True
                        break
                else:
                    has_unresolved_blockers = True
                    break
                    
            if len(all_downstream) > 0 and not has_unresolved_blockers:
                cand['is_root_cause'] = True
            else:
                cand['is_root_cause'] = False

        return list(name_to_candidate.values())
