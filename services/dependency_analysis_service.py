class DependencyAnalysisService:
    @classmethod
    def analyze_dependencies(cls, milestone_status_map: dict, dependency_graph: dict):
        """
        Analyzes the baseline dependency graph against current milestone statuses.
        Does not infer or regenerate dependencies. Uses the existing graph.
        
        Args:
            milestone_status_map: dict mapping milestone name -> status (IN_PROGRESS, BLOCKED, COMPLETED, etc)
            dependency_graph: dict mapping milestone name -> list of blocking milestones (what it depends on)
            
        Returns: dict of analysis results per milestone
        """
        results = {}
        
        # Build reverse graph: milestone -> what it blocks
        reverse_graph = {}
        for m, deps in dependency_graph.items():
            if m not in reverse_graph:
                reverse_graph[m] = []
            for dep in deps:
                if dep not in reverse_graph:
                    reverse_graph[dep] = []
                if m not in reverse_graph[dep]:
                    reverse_graph[dep].append(m)
                    
        def get_all_downstream(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return set()
            visited.add(node)
            downstream = set()
            for child in reverse_graph.get(node, []):
                downstream.add(child)
                downstream.update(get_all_downstream(child, visited))
            return downstream

        for milestone, status in milestone_status_map.items():
            # If no mapping exists in the baseline, safely skip cascade calculations.
            if milestone not in reverse_graph and milestone not in dependency_graph:
                results[milestone] = {
                    "cascade_count": 0,
                    "downstream_milestones": [],
                    "direct_downstream_milestones": [],
                    "is_root_cause": False,
                    "dependency_depth": 0
                }
                continue

            downstream_set = get_all_downstream(milestone)
            cascade_count = len(downstream_set)
            
            # Root cause heuristic: Is it blocking things, but isn't blocked itself?
            is_root_cause = False
            if status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED"]:
                is_root_cause = True
                for dep in dependency_graph.get(milestone, []):
                    dep_status = milestone_status_map.get(dep, "COMPLETED")
                    if dep_status in ["BLOCKED", "DELAYED", "IN_PROGRESS", "NOT_STARTED"]:
                        is_root_cause = False
                        break
            
            results[milestone] = {
                "cascade_count": cascade_count,
                "downstream_milestones": list(downstream_set),
                "direct_downstream_milestones": reverse_graph.get(milestone, []),
                "is_root_cause": is_root_cause and cascade_count > 0,
                "dependency_depth": len(dependency_graph.get(milestone, []))
            }
            
        return results
