import collections


class DependencyGraphBuilder:
    """
    Document-independent dependency graph builder.
    
    NO hardcoded project names or alias registries.
    All resolution is dynamic from the EL baseline and extracted facts.
    """

    # Words that are NEVER valid graph nodes — they are statuses, roles, or generic nouns
    INVALID_DEPENDENCY_TARGETS = {
        # Statuses
        "pending", "pending review", "waiting", "completed", "not started",
        "in progress", "unknown", "delayed", "blocked", "cancelled",
        "done", "resolved", "on hold", "deferred", "planned",
        # Owners / Roles
        "customer", "client", "internal", "external", "vendor",
        "third party", "third-party", "development team", "qa team",
        "qa lead", "project manager", "customer team", "sponsor",
        "management", "stakeholder", "pmo",
        # Generic nouns
        "review", "approval", "access", "credentials", "next weekly meeting",
        "meeting", "discussion", "follow up", "follow-up", "tbd",
        "n/a", "na", "none", "null", "undefined",
    }

    @classmethod
    def is_valid_dependency_entity(cls, name: str, known_entities: set) -> bool:
        """
        Validates whether a dependency target is a legitimate project entity.
        Returns False for statuses, owners, roles, dates, and generic nouns.
        """
        if not name or not name.strip():
            return False

        n_lower = name.lower().strip()

        # Reject if it's in the explicit blocklist
        if n_lower in cls.INVALID_DEPENDENCY_TARGETS:
            return False

        # Reject pure dates (e.g. "September 9", "2026-09-09", "09 Sep")
        import re
        if re.match(r'^\d{1,2}\s+\w+(\s+\d{4})?$', n_lower):  # "09 Sep 2026"
            return False
        if re.match(r'^\w+\s+\d{1,2}(,?\s+\d{4})?$', n_lower):  # "September 9, 2026"
            return False
        if re.match(r'^\d{4}-\d{2}-\d{2}$', n_lower):  # "2026-09-09"
            return False

        # Reject very short strings (1-2 chars) — likely abbreviations without context
        if len(n_lower) <= 2:
            return False

        # If it matches a known project entity, always accept
        if n_lower in known_entities:
            return True

        # Reject single generic words that aren't known entities
        if len(n_lower.split()) == 1 and n_lower not in known_entities:
            # Allow if it's a recognizable acronym (3+ uppercase chars)
            if name.isupper() and len(name) >= 3:
                return True
            # Otherwise reject single-word unknowns
            return False

        return True

    @classmethod
    def build_and_enrich(cls, candidates: list, baseline_items: list = None) -> list:
        """
        Builds the dependency graph from extracted candidates.
        
        All alias resolution is DYNAMIC — driven by the EL baseline items,
        not by a hardcoded registry.
        """
        if baseline_items is None:
            baseline_items = []

        # ── DYNAMIC ALIAS REGISTRY ──
        # Built from EL baseline items, not hardcoded
        alias_registry = {}
        baseline_names_lower = set()

        for item in baseline_items:
            name = item.get("name", "").strip()
            if not name:
                continue
            n_lower = name.lower()
            baseline_names_lower.add(n_lower)

            # Auto-generate aliases from baseline items
            # e.g. "System Integration Testing" -> aliases: "sit", "system integration testing"
            words = name.split()
            if len(words) > 1:
                # Acronym alias (first letter of each word)
                acronym = "".join(w[0] for w in words if w[0].isupper()).lower()
                if len(acronym) >= 2:
                    alias_registry[acronym] = name

            alias_registry[n_lower] = name

        # Also build aliases from candidate activities themselves
        candidate_names = set()
        for cand in candidates:
            raw_name = cand.get('activity', '').strip()
            if raw_name:
                candidate_names.add(raw_name.lower())

        # Build the full known entities set for validation
        known_entities = baseline_names_lower | candidate_names

        def normalize_name(name):
            """Resolve a name against the dynamic alias registry."""
            if not name:
                return name
            n_lower = str(name).lower().strip()

            # Exact alias match
            if n_lower in alias_registry:
                return alias_registry[n_lower]

            # Substring match against aliases (longest match first)
            sorted_aliases = sorted(alias_registry.keys(), key=len, reverse=True)
            for alias in sorted_aliases:
                if len(alias) >= 3 and alias == n_lower:
                    return alias_registry[alias]

            return name

        # ── BUILD CANDIDATE MAP ──
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

            name_to_candidate[canonical_name] = cand

        # ── PASS 1: Normalize blocked_by and blocks arrays ──
        for cand in name_to_candidate.values():
            if 'blocked_by' in cand:
                new_blocked_by = []
                for b in cand['blocked_by']:
                    normalized = normalize_name(b)
                    # VALIDATE: Only accept legitimate project entities
                    if cls.is_valid_dependency_entity(normalized, known_entities):
                        new_blocked_by.append(normalized)
                    else:
                        print(f"  [GraphValidator] REJECTED dependency target '{b}' (normalized: '{normalized}') — not a valid project entity")
                cand['blocked_by'] = list(set(new_blocked_by))

            if 'blocks' in cand:
                new_blocks = []
                for b in cand['blocks']:
                    normalized = normalize_name(b)
                    if cls.is_valid_dependency_entity(normalized, known_entities):
                        new_blocks.append(normalized)
                    else:
                        print(f"  [GraphValidator] REJECTED blocks target '{b}' (normalized: '{normalized}') — not a valid project entity")
                cand['blocks'] = list(set(new_blocks))

        # ── BUILD GRAPH (only from validated entities) ──
        forward_graph = collections.defaultdict(set)
        backward_graph = collections.defaultdict(set)

        for name, cand in name_to_candidate.items():
            conf = cand.get('llm_confidence', 1.0)
            if conf < 0.5:
                continue

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

        # ── VALIDATE GRAPH: Remove self-loops ──
        for node in list(forward_graph.keys()):
            if node in forward_graph[node]:
                forward_graph[node].remove(node)
                print(f"  [GraphValidator] Removed self-dependency: {node}")

        # ── VALIDATE GRAPH: Detect and break cycles ──
        def detect_and_break_cycles():
            visited = set()
            rec_stack = set()
            cycles_broken = []

            def dfs(node, path):
                visited.add(node)
                rec_stack.add(node)
                for neighbor in list(forward_graph.get(node, [])):
                    if neighbor not in visited:
                        dfs(neighbor, path + [neighbor])
                    elif neighbor in rec_stack:
                        # Cycle detected! Break the edge from node -> neighbor
                        forward_graph[node].discard(neighbor)
                        backward_graph[neighbor].discard(node)
                        cycles_broken.append((node, neighbor))
                        print(f"  [GraphValidator] CYCLE DETECTED: {' -> '.join(path + [neighbor])}. Broke edge: {node} -> {neighbor}")
                rec_stack.discard(node)

            for node in list(set(forward_graph.keys()) | set(backward_graph.keys())):
                if node not in visited:
                    dfs(node, [node])

            return cycles_broken

        detect_and_break_cycles()

        # ── DO NOT spawn dummy nodes for unknown blockers ──
        # If a blocked_by target doesn't exist as a candidate, it means:
        # (a) The LLM extracted something that isn't a real activity, OR
        # (b) It's a reference to something not in this document
        # In both cases, we log it but do NOT create a fake graph node.
        all_known_nodes = set(name_to_candidate.keys())
        for name, cand in list(name_to_candidate.items()):
            valid_blockers = []
            for b in cand.get('blocked_by', []):
                if b in all_known_nodes:
                    valid_blockers.append(b)
                else:
                    print(f"  [GraphValidator] Orphan dependency '{b}' for '{name}' — target not in graph. Skipping.")
                    # Remove from graphs too
                    forward_graph[b].discard(name)
                    backward_graph[name].discard(b)
            cand['blocked_by'] = valid_blockers

        # Sync blocked_by arrays to match the validated backward graph
        for name, cand in name_to_candidate.items():
            cand['blocked_by'] = list(backward_graph.get(name, set()))

        # ── GRAPH TRAVERSAL HELPERS ──
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
            """Deterministic DAG longest-path using DFS. Returns ordered list."""
            max_path = []
            def dfs(curr_node, current_path, visited):
                nonlocal max_path
                neighbors = sorted(forward_graph.get(curr_node, []))  # Sort for determinism
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
                # Also check if current path is longest (in case all neighbors were visited)
                if len(current_path) > len(max_path):
                    max_path = list(current_path)
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

        # ── IDENTIFY PARALLEL STREAMS ──
        all_nodes = set(name_to_candidate.keys())
        stream_id = 1
        node_to_stream = {}
        visited_nodes = set()
        for node in sorted(all_nodes):  # Sort for determinism
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

        # ── PASS 3: Calculate PMO Metrics ──
        # Pre-compute unresolved blockers once
        unresolved_blockers = {
            n: [b for b in backward_graph.get(n, set())
                if name_to_candidate.get(b, {}).get('status', 'NOT_STARTED') not in ['RESOLVED', 'COMPLETED']]
            for n in name_to_candidate.keys()
        }

        for name, cand in name_to_candidate.items():
            immediate = sorted(list(forward_graph.get(name, set())))
            longest_path = get_longest_forward_path(name)

            cand['parents'] = sorted(list(backward_graph.get(name, set())))
            cand['children'] = immediate
            cand['longest_path'] = longest_path
            cand['parallel_stream'] = node_to_stream.get(name, "Stream 1")

            cand['downstream_names'] = get_forward_path(name)
            cand['direct_blocking_names'] = immediate

            cand['blocked_work_count'] = len(get_forward_path(name))
            cand['cascade_depth'] = len(longest_path)
            cand['distance_to_terminal'] = get_distance_to_terminal(name)

            # Criticality Score (0-100)
            crit_score = min(cand['cascade_depth'] * 15, 80)
            if cand['distance_to_terminal'] < 999:
                crit_score += max(20 - (cand['distance_to_terminal'] * 5), 0)
            cand['criticality_score'] = min(crit_score, 100.0)
            cand['critical_path'] = cand['criticality_score'] >= 75.0
            cand['critical_chain'] = cand['distance_to_terminal'] < 999

            has_unresolved_upstream = len(unresolved_blockers.get(name, [])) > 0
            # Root cause: blocks downstream AND has no unresolved upstream
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
            cand['immediate_unlocks'] = sorted(list(unlocked))
            cand['future_unlocks'] = [x for x in cand['downstream_names'] if x not in unlocked]

            if cand['immediate_unlock_count'] > 0:
                cand['distance_to_next_executable'] = 1
            elif cand['blocked_work_count'] > 0:
                cand['distance_to_next_executable'] = cand['cascade_depth']
            else:
                cand['distance_to_next_executable'] = 999

            # PMO Fields — Dependency Owner (from evidence, not hardcoded names)
            ev = (cand.get('evidence') or '').lower()
            if any(k in ev for k in ['customer', 'client', 'sponsor']):
                cand['dependency_owner'] = 'Customer'
            elif any(k in ev for k in ['vendor', '3rd party', 'third-party', 'external provider']):
                cand['dependency_owner'] = 'Vendor'
            else:
                cand['dependency_owner'] = 'Internal'

            # Resolution Effort Proxy — generic heuristic, not name-specific
            cand['resolution_effort'] = 'M'  # Default medium
            if cand['blocked_work_count'] == 0:
                cand['resolution_effort'] = 'S'
            elif cand['cascade_depth'] >= 3:
                cand['resolution_effort'] = 'L'

            # Business Criticality — based on graph position, not name keywords
            if cand['critical_path'] or cand['cascade_depth'] >= 3:
                cand['business_criticality'] = 'Mission Critical'
            elif cand['blocked_work_count'] >= 2:
                cand['business_criticality'] = 'High'
            else:
                cand['business_criticality'] = 'Medium'

            # Business Phase — based on graph position
            if cand['distance_to_terminal'] == 0:
                cand['business_phase'] = 'Deployment'
            elif cand['distance_to_terminal'] <= 2:
                cand['business_phase'] = 'Testing'
            elif cand['is_root_cause']:
                cand['business_phase'] = 'Execution'
            else:
                cand['business_phase'] = 'Development'

            cand['blocked'] = has_unresolved_upstream
            cand['waiting'] = cand['blocked'] and not cand['is_root_cause']
            cand['cascade_count'] = cand['blocked_work_count']

            # Dependency source — from evidence keywords
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

        # ── LOG FINAL GRAPH STRUCTURE ──
        print(f"\n  [DependencyGraph] Final graph: {len(name_to_candidate)} nodes, {sum(len(v) for v in forward_graph.values())} edges")
        for name, cand in sorted(name_to_candidate.items()):
            root_marker = " [ROOT CAUSE]" if cand.get('is_root_cause') else ""
            crit_marker = " [CRITICAL PATH]" if cand.get('critical_path') else ""
            print(f"    {name}: cascade={cand.get('cascade_count', 0)}, unlocks={cand.get('immediate_unlock_count', 0)}, "
                  f"blocked_by={cand.get('blocked_by', [])}{root_marker}{crit_marker}")

        return list(name_to_candidate.values())
