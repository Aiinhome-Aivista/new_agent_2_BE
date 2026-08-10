import collections
from datetime import datetime, timezone
from agents.execution_pipeline import ProjectStateSnapshot

class GraphValidationEngine:
    @classmethod
    def validate_and_clean(cls, backward_graph: dict, forward_graph: dict, all_nodes: set):
        # 1. Self-Dependency Removal
        for n in list(forward_graph.keys()):
            if n in forward_graph[n]:
                forward_graph[n].remove(n)
        for n in list(backward_graph.keys()):
            if n in backward_graph[n]:
                backward_graph[n].remove(n)

        # 2. Reference Integrity (Remove edges to non-existent nodes)
        for n in list(forward_graph.keys()):
            forward_graph[n] = [c for c in forward_graph[n] if c in all_nodes]
        for n in list(backward_graph.keys()):
            backward_graph[n] = [p for p in backward_graph[n] if p in all_nodes]

        # 3. Cycle Detection (DFS)
        visited = set()
        rec_stack = set()
        def is_cyclic(node):
            visited.add(node)
            rec_stack.add(node)
            for neighbor in forward_graph.get(node, []):
                if neighbor not in visited:
                    if is_cyclic(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.remove(node)
            return False

        for node in all_nodes:
            if node not in visited:
                if is_cyclic(node):
                    # If cycle detected, break it conservatively by clearing backward edges of the offending node
                    print(f"  [GraphValidation] Warning: Cycle detected at node {node}. Breaking cycle.")
                    forward_graph[node] = []
                    
        return backward_graph, forward_graph


class ExecutionQueueBuilder:
    @classmethod
    def build_queue(cls, snapshot: ProjectStateSnapshot, backward_graph: dict, forward_graph: dict) -> tuple:
        """
        Builds the Execution Queue using Topological Traversal and the Execution Index Formula.
        Returns a tuple: (queue, node_metrics)
        """
        all_nodes = set(backward_graph.keys()).union(set(forward_graph.keys())).union(set(snapshot.milestone_statuses.keys()))
        
        # Phase 1: Validation
        backward_graph, forward_graph = GraphValidationEngine.validate_and_clean(backward_graph, forward_graph, all_nodes)
        
        node_metrics = {}
        
        # Helper: Get all downstream nodes
        def get_all_downstream(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return set()
            visited.add(node)
            downstream = set()
            for child in forward_graph.get(node, []):
                downstream.add(child)
                downstream.update(get_all_downstream(child, visited))
            return downstream

        # Helper: Get downstream chain length and longest path
        memo_dist = {}
        memo_path = {}
        def get_downstream_chain(node):
            if node in memo_dist:
                return memo_dist[node], memo_path[node]
            children = forward_graph.get(node, [])
            if not children:
                memo_dist[node] = 0
                memo_path[node] = [node]
                return 0, [node]
            
            max_dist = -1
            best_path = []
            for c in children:
                c_dist, c_path = get_downstream_chain(c)
                if c_dist > max_dist:
                    max_dist = c_dist
                    best_path = c_path
                    
            memo_dist[node] = max_dist + 1
            memo_path[node] = [node] + best_path
            return memo_dist[node], memo_path[node]

        for node in all_nodes:
            get_downstream_chain(node)

        # Critical path heuristics (nodes on the absolute longest path)
        max_graph_dist = max(memo_dist.values()) if memo_dist else 0
        critical_nodes = set()
        if max_graph_dist > 0:
            current_nodes = [n for n, d in memo_dist.items() if d == max_graph_dist]
            while current_nodes:
                next_nodes = []
                for n in current_nodes:
                    critical_nodes.add(n)
                    children = forward_graph.get(n, [])
                    if children:
                        max_c_dist = max(memo_dist.get(c, 0) for c in children)
                        for c in children:
                            if memo_dist.get(c, 0) == max_c_dist:
                                next_nodes.append(c)
                current_nodes = next_nodes

        # Helper: Find earliest effective due date
        memo_date = {}
        def get_effective_date(node):
            if node in memo_date:
                return memo_date[node]
            
            node_date = snapshot.get_date(node)
            children = forward_graph.get(node, [])
            
            valid_dates = []
            if node_date: valid_dates.append(node_date)
            
            for c in children:
                c_date = get_effective_date(c)
                if c_date: valid_dates.append(c_date)
                
            eff_date = min(valid_dates) if valid_dates else None
            memo_date[node] = eff_date
            return eff_date

        for node in all_nodes:
            get_effective_date(node)

        # Phase 2: Compute Rich Graph Metrics
        today = datetime.now(timezone.utc).date()
        
        for node in all_nodes:
            downstream_set = get_all_downstream(node)
            cascade_count = len(downstream_set)
            status = snapshot.get_status(node)
            
            # Root cause heuristic: Is it the EARLIEST actionable blocker?
            is_root = False
            if status not in ["COMPLETED", "RESOLVED"]:
                is_root = True
                for dep in backward_graph.get(node, []):
                    dep_status = snapshot.get_status(dep)
                    if dep_status not in ["COMPLETED", "RESOLVED"]:
                        is_root = False
                        break
            
            is_leaf = len(forward_graph.get(node, [])) == 0
            
            # Immediate Unlock Count
            immediate_unlocks = 0
            for child in forward_graph.get(node, []):
                child_ready = True
                for dep in backward_graph.get(child, []):
                    if dep != node and snapshot.get_status(dep) not in ["COMPLETED", "RESOLVED"]:
                        child_ready = False
                        break
                if child_ready:
                    immediate_unlocks += 1
                    
            # Due Date Urgency calculation
            due_date = memo_date.get(node)
            days_remaining = 999
            if due_date:
                # Assuming due_date is datetime.date or datetime
                try:
                    due_date_obj = due_date.date() if isinstance(due_date, datetime) else due_date
                    days_remaining = (due_date_obj - today).days
                except:
                    pass
                    
            due_date_weight = 1.0
            if days_remaining <= 0:
                due_date_weight = 3.0
            elif days_remaining <= 7:
                due_date_weight = 2.0
            elif days_remaining <= 14:
                due_date_weight = 1.5
            elif days_remaining <= 30:
                due_date_weight = 1.2
            
            critical_path_len = memo_dist.get(node, 0)
            
            # Execution Index Formula
            root_cause_score = 100 if is_root else 10
            exec_index = root_cause_score + (critical_path_len * immediate_unlocks * due_date_weight)
            if node in critical_nodes:
                exec_index *= 1.5
                
            node_metrics[node] = {
                "parents": backward_graph.get(node, []),
                "children": forward_graph.get(node, []),
                "is_root": is_root,
                "is_leaf": is_leaf,
                "critical_path": node in critical_nodes,
                "critical_path_length": critical_path_len,
                "immediate_unlocks": immediate_unlocks,
                "cascade_nodes": cascade_count,
                "execution_level": 0, # Will be filled by topological sort
                "longest_path": memo_path.get(node, []),
                "execution_index": exec_index,
                "days_remaining": days_remaining
            }

        # Phase 3: Build Queue
        # We sort eligible nodes strictly by Execution Index (highest first)
        eligible_nodes = [n for n in all_nodes if snapshot.get_status(n) not in ["COMPLETED", "RESOLVED"]]
        eligible_nodes.sort(key=lambda n: (-node_metrics[n]["execution_index"], n))
        
        queue = []
        visited = set()
        
        # Traverse strictly by descending execution index
        for node in eligible_nodes:
            if node not in visited:
                visited.add(node)
                queue.append(node)
                
        # Fill execution levels based on BFS depth from roots
        roots = [n for n in all_nodes if not backward_graph.get(n)]
        q = collections.deque([(r, 1) for r in roots])
        level_visited = set()
        while q:
            curr, lvl = q.popleft()
            if curr not in level_visited:
                level_visited.add(curr)
                node_metrics[curr]["execution_level"] = lvl
                for c in forward_graph.get(curr, []):
                    q.append((c, lvl + 1))
                    
        return queue, node_metrics
