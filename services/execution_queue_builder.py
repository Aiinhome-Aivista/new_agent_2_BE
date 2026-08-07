from agents.execution_pipeline import ProjectStateSnapshot
import collections

class ExecutionQueueBuilder:
    @classmethod
    def build_queue(cls, snapshot: ProjectStateSnapshot, backward_graph: dict, forward_graph: dict) -> list:
        """
        Builds the Execution Queue using Topological Traversal.
        Prioritizes by Immediate Unlock Count -> Critical Path -> Cascade Depth.
        Returns a tuple: (queue, node_metrics)
        queue: ordered list of milestone IDs
        node_metrics: dictionary of computed metrics for each node
        """
        queue = []
        
        # 1. Identify all nodes and compute properties
        all_nodes = set(backward_graph.keys()).union(set(forward_graph.keys())).union(set(snapshot.milestone_statuses.keys()))
        
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

        # Find downstream chain length for sorting
        memo_dist = {}
        def get_downstream_chain_length(node):
            if node in memo_dist:
                return memo_dist[node]
            children = forward_graph.get(node, [])
            if not children:
                memo_dist[node] = 0
                return 0
            max_dist = max(get_downstream_chain_length(c) for c in children) + 1
            memo_dist[node] = max_dist
            return max_dist

        for node in all_nodes:
            get_downstream_chain_length(node)

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

        node_metrics = {}
        for node in all_nodes:
            downstream_set = get_all_downstream(node)
            cascade_count = len(downstream_set)
            status = snapshot.get_status(node)
            
            # Find earliest root cause
            earliest_root_cause = False
            if cascade_count > 0 and status not in ["COMPLETED", "RESOLVED"]:
                earliest_root_cause = True
                for dep in backward_graph.get(node, []):
                    dep_status = snapshot.get_status(dep)
                    if dep_status not in ["COMPLETED", "RESOLVED"]:
                        earliest_root_cause = False
                        break
                        
            # Calculate Immediate Unlock Count (Actual executable work)
            immediate_unlocks = 0
            for child in forward_graph.get(node, []):
                child_ready_after_this = True
                for dep in backward_graph.get(child, []):
                    if dep != node and snapshot.get_status(dep) not in ["COMPLETED", "RESOLVED"]:
                        child_ready_after_this = False
                        break
                if child_ready_after_this:
                    immediate_unlocks += 1
                    
            node_metrics[node] = {
                "cascade_count": cascade_count,
                "earliest_root_cause": earliest_root_cause,
                "immediate_unlocks": immediate_unlocks,
                "chain_length": memo_dist.get(node, 0),
                "is_critical_path": node in critical_nodes
            }

        # 2. Build Queue via Topological Sort traversing from Root Causes
        visited = set()
        
        # Sort root causes
        root_causes = [n for n, metrics in node_metrics.items() if metrics["earliest_root_cause"]]
        
        def root_sort_key(node):
            metrics = node_metrics[node]
            # Primary: Immediate Unlock Count
            # Secondary: Is Critical Path
            # Tertiary: Chain Length
            return (
                -metrics["immediate_unlocks"], 
                -1 if metrics["is_critical_path"] else 0,
                -metrics["chain_length"]
            )
            
        root_causes.sort(key=root_sort_key)
        
        for root in root_causes:
            # BFS or DFS from root preserving chain order
            q = collections.deque([root])
            while q:
                curr = q.popleft()
                if curr not in visited:
                    visited.add(curr)
                    queue.append(curr)
                    
                    # Add children, sorted by their chain length
                    children = forward_graph.get(curr, [])
                    sorted_children = sorted(children, key=lambda c: -node_metrics[c]["chain_length"])
                    for child in sorted_children:
                        if child not in visited:
                            # Only add child to traverse if its other dependencies are met
                            all_deps_met = True
                            for d in backward_graph.get(child, []):
                                if d not in visited and snapshot.get_status(d) not in ["COMPLETED", "RESOLVED"]:
                                    all_deps_met = False
                                    break
                            if all_deps_met:
                                q.append(child)
                                
        # Add remaining nodes that might be isolated or just blocked
        remaining = [n for n in all_nodes if n not in visited and snapshot.get_status(n) not in ["COMPLETED", "RESOLVED"]]
        # We also need a way to topological sort the remaining items based on waiting dependencies
        # Simple strategy: just sort by chain length and immediate unlocks
        remaining.sort(key=lambda n: (-node_metrics[n]["immediate_unlocks"], -node_metrics[n]["chain_length"]))
        for r in remaining:
            queue.append(r)
            visited.add(r)
            
        return queue, node_metrics
