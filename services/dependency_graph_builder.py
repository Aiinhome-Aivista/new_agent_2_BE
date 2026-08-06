import collections

class DependencyGraphBuilder:
    @classmethod
    def build_and_enrich(cls, candidates: list) -> list:
        # ALIAS REGISTRY
        alias_registry = {
            "credentials": "Production CRM API Credentials",
            "api credentials": "Production CRM API Credentials",
            "production crm api credentials": "Production CRM API Credentials",
            "crm": "CRM Integration",
            "crm integration": "CRM Integration",
            "sso": "Azure AD SSO",
            "azure ad": "Azure AD SSO",
            "azure ad sso": "Azure AD SSO",
            "vpn": "Production VPN Access",
            "production vpn access": "Production VPN Access",
            "production vpn": "Production VPN Access",
            "sit": "System Integration Testing",
            "system integration testing": "System Integration Testing",
            "uat": "User Acceptance Testing",
            "user acceptance testing": "User Acceptance Testing",
            "prod": "Production Deployment",
            "production deployment": "Production Deployment",
            "kt": "Knowledge Transfer",
            "knowledge transfer": "Knowledge Transfer",
            "backend apis": "Backend APIs",
            "analytics dashboard": "Analytics Dashboard",
            "security review": "Security Review",
            "audit logs": "Audit Logs",
            "qa validation": "QA Validation",
            "user management": "User Management"
        }

        def normalize_name(name):
            if not name:
                return name
            n_lower = str(name).lower().strip()
            if n_lower in alias_registry:
                return alias_registry[n_lower]
            for alias, canonical in alias_registry.items():
                if alias in n_lower:
                    return canonical
            return name

        name_to_candidate = {}
        for cand in candidates:
            raw_name = cand.get('activity')
            if not raw_name:
                continue
            canonical_name = normalize_name(raw_name)
            cand['activity'] = canonical_name
            cand['canonical_title'] = canonical_name
            if 'blocked_by' not in cand:
                cand['blocked_by'] = []
            
            # Entity type overrides
            if canonical_name in ["Azure AD SSO", "System Integration Testing", "User Acceptance Testing", "Production Deployment", "Knowledge Transfer"]:
                cand['entity_type'] = "MILESTONE"
                
            name_to_candidate[canonical_name] = cand

        # PASS 1: Normalize existing blocked_by and blocks
        for cand in name_to_candidate.values():
            # blocked_by
            if 'blocked_by' in cand:
                new_blocked_by = []
                for b in cand['blocked_by']:
                    new_blocked_by.append(normalize_name(b))
                cand['blocked_by'] = list(set(new_blocked_by))
            
            # blocks
            if 'blocks' in cand:
                new_blocks = []
                for b in cand['blocks']:
                    new_blocks.append(normalize_name(b))
                cand['blocks'] = list(set(new_blocks))

        # Build graph purely from LLM extractions (No hardcoded expected edges)
        forward_graph = collections.defaultdict(set)
        backward_graph = collections.defaultdict(set)

        for name, cand in name_to_candidate.items():
            conf = cand.get('llm_confidence', 1.0)
            if conf < 0.5:
                continue # Ignore highly uncertain nodes as blockers
                
            for b in cand.get('blocked_by', []):
                b_conf = name_to_candidate.get(b, {}).get('llm_confidence', 1.0)
                if b_conf >= 0.5:
                    backward_graph[name].add(b)
                    forward_graph[b].add(name)
            for b in cand.get('blocks', []):
                b_conf = name_to_candidate.get(b, {}).get('llm_confidence', 1.0)
                if b_conf >= 0.5:
                    forward_graph[name].add(b)
                    backward_graph[b].add(name)

        # VALIDATE GRAPH: Remove self loops
        for node in list(forward_graph.keys()):
            if node in forward_graph[node]:
                forward_graph[node].remove(node)

        # Spawn missing dummy nodes for external blockers
        all_nodes = set(name_to_candidate.keys()) | set(forward_graph.keys()) | set(backward_graph.keys())
        for node in all_nodes:
            if node not in name_to_candidate:
                name_to_candidate[node] = {
                    "activity": node,
                    "canonical_title": node,
                    "entity_type": "ACTION_ITEM",
                    "status": "NOT_STARTED",
                    "blocked_by": [],
                    "evidence": f"Identified as a blocker or downstream entity in execution chain.",
                    "reasoning": f"This operational item is part of the dependency graph.",
                    "recommended_action": f"Track execution progress.",
                    "should_create_risk": True,
                    "is_scope_creep": False,
                    "llm_confidence": 1.0,
                    "progress": 0,
                    "p_date_str": None,
                    "days_overdue": 0,
                    "days_until_due": 0,
                    "next_milestone_name": None,
                    "next_milestone_date": None,
                    "days_to_next_milestone": None,
                    "original_contract_sentence": ""
                }
                
        # Sync the nodes' blocked_by arrays to strictly match the valid backward graph
        for name, cand in name_to_candidate.items():
            cand['blocked_by'] = list(backward_graph[name])

        # Graph Traversal Helpers
        terminal_keywords = ['production', 'deployment', 'go live', 'go-live', 'launch', 'release', 'kt', 'knowledge transfer']

        def get_forward_path(start_node):
            visited = set()
            queue = collections.deque([start_node])
            while queue:
                curr = queue.popleft()
                for neighbor in forward_graph.get(curr, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            return list(visited)

        def get_longest_forward_path(start_node):
            max_path = []
            def dfs(curr_node, current_path, visited):
                nonlocal max_path
                neighbors = forward_graph.get(curr_node, [])
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
            dfs(start_node, [], set())
            return max_path

        def get_distance_to_terminal(start_node):
            if any(tk in start_node.lower() for tk in terminal_keywords):
                return 0
            visited = {start_node: 0}
            queue = collections.deque([start_node])
            min_dist = 999
            while queue:
                curr = queue.popleft()
                dist = visited[curr]
                if any(tk in curr.lower() for tk in terminal_keywords):
                    if dist < min_dist:
                        min_dist = dist
                for neighbor in forward_graph.get(curr, []):
                    if neighbor not in visited:
                        visited[neighbor] = dist + 1
                        queue.append(neighbor)
            return min_dist

        # Identify Parallel Streams (Disconnected Subgraphs)
        stream_id = 1
        node_to_stream = {}
        visited_nodes = set()
        for node in all_nodes:
            if node not in visited_nodes:
                stream_queue = collections.deque([node])
                current_stream = set()
                while stream_queue:
                    curr = stream_queue.popleft()
                    if curr not in current_stream:
                        current_stream.add(curr)
                        visited_nodes.add(curr)
                        for neighbor in forward_graph.get(curr, []):
                            if neighbor not in current_stream:
                                stream_queue.append(neighbor)
                        for neighbor in backward_graph.get(curr, []):
                            if neighbor not in current_stream:
                                stream_queue.append(neighbor)
                for stream_node in current_stream:
                    node_to_stream[stream_node] = f"Stream {stream_id}"
                stream_id += 1

        # PASS 3: Calculate PMO Metrics
        for name, cand in name_to_candidate.items():
            immediate = list(forward_graph.get(name, set()))
            longest_path = get_longest_forward_path(name)
            
            cand['parents'] = list(backward_graph.get(name, set()))
            cand['children'] = immediate
            cand['longest_path'] = longest_path
            cand['parallel_stream'] = node_to_stream.get(name, "Stream 1")
            
            cand['downstream_names'] = get_forward_path(name)
            cand['direct_blocking_names'] = immediate
            
            cand['blocked_work_count'] = len(get_forward_path(name))
            cand['cascade_depth'] = len(longest_path)
            cand['distance_to_terminal'] = get_distance_to_terminal(name)
            
            # Criticality Score (0-100)
            # Base logic: cascade_depth * 10, bonus if distance_to_terminal is small
            crit_score = min(cand['cascade_depth'] * 15, 80)
            if cand['distance_to_terminal'] < 999:
                crit_score += max(20 - (cand['distance_to_terminal'] * 5), 0)
            cand['criticality_score'] = min(crit_score, 100.0)
            cand['critical_path'] = cand['criticality_score'] >= 75.0
            cand['critical_chain'] = cand['distance_to_terminal'] < 999

            unresolved_blockers = {
                n: [b for b in backward_graph.get(n, set()) if name_to_candidate.get(b, {}).get('status', 'NOT_STARTED') not in ['RESOLVED', 'COMPLETED']]
                for n in name_to_candidate.keys()
            }
            
            has_unresolved_upstream = len(unresolved_blockers.get(name, [])) > 0
            # Multiple root causes natively supported: any node with blocked_work but 0 upstream unresolved blockers
            if cand['blocked_work_count'] > 0 and not has_unresolved_upstream:
                cand['is_root_cause'] = True
            else:
                cand['is_root_cause'] = False
            cand['earliest_root_cause'] = cand['is_root_cause']

            # Immediate Unlock Count
            unlocked = set()
            queue = collections.deque([name])
            sim_blockers = {k: list(v) for k, v in unresolved_blockers.items()}
            
            while queue:
                curr = queue.popleft()
                for neighbor in forward_graph.get(curr, []):
                    if neighbor in sim_blockers and curr in sim_blockers[neighbor]:
                        sim_blockers[neighbor].remove(curr)
                        if len(sim_blockers[neighbor]) == 0:
                            unlocked.add(neighbor)
                            queue.append(neighbor)
                            
            cand['immediate_unlock_count'] = len(unlocked)
            cand['execution_unlock_count'] = len(unlocked)
            cand['immediate_unlocks'] = list(unlocked)
            cand['future_unlocks'] = [x for x in cand['downstream_names'] if x not in unlocked]
            
            if cand['immediate_unlock_count'] > 0:
                cand['distance_to_next_executable'] = 1
            elif cand['blocked_work_count'] > 0:
                cand['distance_to_next_executable'] = cand['cascade_depth']
            else:
                cand['distance_to_next_executable'] = 999
                
            # PMO Fields
            # 1. Dependency Owner
            ev = (cand.get('evidence') or '').lower()
            if any(k in ev for k in ['customer', 'client', 'sponsor']):
                cand['dependency_owner'] = 'Customer'
            elif any(k in ev for k in ['vendor', '3rd party', 'third-party', 'external provider']):
                cand['dependency_owner'] = 'Vendor'
            else:
                cand['dependency_owner'] = 'Internal'

            # 2. Resolution Effort Proxy
            # Simple heuristic based on title length or keywords
            title_lower = name.lower()
            if any(k in title_lower for k in ['api', 'database', 'migration', 'architecture']):
                cand['resolution_effort'] = 'L'
            elif any(k in title_lower for k in ['credentials', 'access', 'vpn']):
                cand['resolution_effort'] = 'S'
            elif any(k in title_lower for k in ['review', 'validation', 'testing']):
                cand['resolution_effort'] = 'M'
            else:
                cand['resolution_effort'] = 'M'
                
            # 3. Business Criticality
            if any(k in title_lower for k in ['production', 'go live', 'crm', 'security']):
                cand['business_criticality'] = 'Mission Critical'
            elif any(k in title_lower for k in ['uat', 'testing', 'migration']):
                cand['business_criticality'] = 'High'
            else:
                cand['business_criticality'] = 'Medium'
                
            # 4. Business Phase
            term_node = longest_path[-1].lower() if longest_path else title_lower
            if any(k in term_node for k in ['design', 'planning', 'requirements']):
                cand['business_phase'] = 'Planning'
            elif any(k in term_node for k in ['api', 'development', 'backend']):
                cand['business_phase'] = 'Development'
            elif any(k in term_node for k in ['sit', 'integration']):
                cand['business_phase'] = 'Integration'
            elif any(k in term_node for k in ['uat', 'testing', 'qa']):
                cand['business_phase'] = 'Testing'
            elif any(k in term_node for k in ['production', 'deployment', 'release']):
                cand['business_phase'] = 'Deployment'
            else:
                cand['business_phase'] = 'Execution'
            
            cand['blocked'] = has_unresolved_upstream
            cand['waiting'] = cand['blocked'] and not cand['is_root_cause']
            cand['cascade_count'] = cand['blocked_work_count']
            
            # dependency_source
            evidence_lower = (cand.get('evidence', '') + ' ' + cand.get('reasoning', '') + ' ' + str(cand.get('activity', ''))).lower()
            if any(k in evidence_lower for k in ['customer', 'client', 'credentials', 'vpn', 'access', 'external']):
                cand['dependency_source'] = 'CUSTOMER'
                cand['external_dependency'] = True
            elif any(k in evidence_lower for k in ['vendor', 'third party', 'third-party', 'partner']):
                cand['dependency_source'] = 'VENDOR'
                cand['external_dependency'] = True
            elif any(k in evidence_lower for k in ['security', 'audit', 'compliance', 'review']):
                cand['dependency_source'] = 'SECURITY'
                cand['external_dependency'] = False
            elif any(k in evidence_lower for k in ['pmo', 'management', 'approval']):
                cand['dependency_source'] = 'PMO'
                cand['external_dependency'] = False
            else:
                cand['dependency_source'] = 'ENGINEERING'
                cand['external_dependency'] = False

            cand['blocked'] = has_unresolved_upstream
            cand['waiting'] = cand['blocked'] and not cand['is_root_cause']
            
        return list(name_to_candidate.values())
