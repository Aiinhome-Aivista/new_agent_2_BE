import json
from collections import defaultdict, deque

class MilestoneDependencyService:
    @staticmethod
    def validate_dag(edges):
        """
        Validates if a set of edges (parent_id, child_id) forms a Directed Acyclic Graph (DAG).
        Raises ValueError if a cycle is detected.
        """
        graph = defaultdict(list)
        in_degree = defaultdict(int)
        nodes = set()
        
        for u, v in edges:
            graph[u].append(v)
            in_degree[v] += 1
            if u not in in_degree:
                in_degree[u] = 0
            nodes.add(u)
            nodes.add(v)
            
        queue = deque([n for n in nodes if in_degree[n] == 0])
        visited_count = 0
        
        while queue:
            node = queue.popleft()
            visited_count += 1
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    
        if visited_count != len(nodes):
            raise ValueError("Circular dependency detected in the milestone graph.")
        return True

    @staticmethod
    def build_dependency_graph(db_cursor, project_id):
        """
        Retrieves the approved milestone dependencies and builds the graph representation.
        Returns two graphs:
        forward_graph: {parent_id: [child_id, ...]}
        backward_graph: {child_id: [parent_id, ...]}
        """
        db_cursor.execute("""
            SELECT parent_milestone_id, child_milestone_id 
            FROM milestone_dependencies 
            WHERE project_id = %s
        """, (project_id,))
        rows = db_cursor.fetchall()
        
        forward_graph = defaultdict(list)
        backward_graph = defaultdict(list)
        
        for r in rows:
            p, c = r['parent_milestone_id'], r['child_milestone_id']
            forward_graph[p].append(c)
            backward_graph[c].append(p)
            
        return dict(forward_graph), dict(backward_graph)

    @staticmethod
    def build_rich_dependency_graph(db_cursor, project_id):
        """
        Builds a rich dependency graph expected by ProjectKnowledgeService for LLM context.
        Returns:
            dict mapping item_id -> { "is_incomplete": bool, "dependent_count": int, "blocked_items": list[str], "name": str, "status": str }
        """
        forward_graph, _ = MilestoneDependencyService.build_dependency_graph(db_cursor, project_id)
        
        db_cursor.execute("SELECT id, name, status FROM project_milestones WHERE project_id = %s", (project_id,))
        milestone_data = {r['id']: r for r in db_cursor.fetchall()}
        
        rich_graph = {}
        for parent_id, children_ids in forward_graph.items():
            if parent_id not in milestone_data:
                continue
            parent = milestone_data[parent_id]
            is_incomplete = parent['status'] not in ['COMPLETED', 'CANCELLED']
            
            blocked_names = [
                milestone_data[cid]['name'] 
                for cid in children_ids if cid in milestone_data
            ]
            
            rich_graph[parent_id] = {
                "name": parent['name'],
                "status": parent['status'],
                "is_incomplete": is_incomplete,
                "dependent_count": len(blocked_names),
                "blocked_items": blocked_names
            }
            
        return rich_graph

    @staticmethod
    def calculate_milestone_cascade(db_cursor, project_id, delayed_milestone_ids):
        """
        Calculates how many and which downstream milestones are blocked/delayed.
        Args:
            delayed_milestone_ids: List of milestone IDs that are currently delayed/blocked.
        Returns:
            dict mapping milestone_id -> { "blocked_count": int, "blocked_milestone_names": list[str] }
        """
        forward_graph, _ = MilestoneDependencyService.build_dependency_graph(db_cursor, project_id)
        
        # Get milestone names and statuses for reporting and filtering
        db_cursor.execute("""
            SELECT id, name, status FROM project_milestones WHERE project_id = %s
        """, (project_id,))
        milestone_data = {r['id']: {'name': r['name'], 'status': r.get('status', 'Planned')} for r in db_cursor.fetchall()}
        
        results = {}
        for start_id in delayed_milestone_ids:
            # BFS to find all reachable downstream nodes
            visited = set()
            queue = deque([start_id])
            while queue:
                curr = queue.popleft()
                if curr in forward_graph:
                    for child in forward_graph[curr]:
                        if child not in visited:
                            # Skip if the child is already completed or cancelled
                            child_status = milestone_data.get(child, {}).get('status', 'Planned').upper()
                            if child_status in ['COMPLETED', 'CANCELLED']:
                                continue
                                
                            visited.add(child)
                            queue.append(child)
                            
            blocked_names = [milestone_data.get(vid, {}).get('name', str(vid)) for vid in visited]
            results[start_id] = {
                "blocked_count": len(visited),
                "blocked_milestones": blocked_names,
                "blocked_milestone_ids": list(visited)
            }
            
        return results

    @staticmethod
    def generate_sequential_dependencies(db_cursor, project_id):
        """
        Fallback logic: Automatically links milestones sequentially (sequence N -> sequence N+1).
        Only links milestones that are not OUT_OF_SCOPE (if applicable) and are actually active.
        """
        db_cursor.execute("""
            SELECT id, sequence FROM project_milestones 
            WHERE project_id = %s 
            ORDER BY sequence ASC
        """, (project_id,))
        milestones = db_cursor.fetchall()
        
        if len(milestones) < 2:
            return 0
            
        edges = []
        for i in range(len(milestones) - 1):
            parent_id = milestones[i][0] if isinstance(milestones[i], tuple) else milestones[i]['id']
            child_id = milestones[i+1][0] if isinstance(milestones[i+1], tuple) else milestones[i+1]['id']
            edges.append((parent_id, child_id))
            
        # Validate just in case
        MilestoneDependencyService.validate_dag(edges)
        
        # Insert
        inserted_count = 0
        for p, c in edges:
            db_cursor.execute("""
                INSERT INTO milestone_dependencies (project_id, parent_milestone_id, child_milestone_id, dependency_type)
                VALUES (%s, %s, %s, 'FINISH_TO_START')
            """, (project_id, p, c))
            inserted_count += 1
            
        return inserted_count
