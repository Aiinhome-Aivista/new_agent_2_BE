class PMOClassifier:
    """
    Lightweight Deterministic Classifier.
    Runs immediately after Fact Extraction to firmly classify entities based on PMO keywords.
    Ensures the Risk Engine doesn't have to rediscover classifications from raw text.
    """
    @staticmethod
    def classify(item: dict, baseline_canonical_names: set) -> str:
        statement = str(item.get("activity") or item.get("statement") or "").strip()
        verb = str(item.get("verb") or "").strip()
        
        statement_lower = statement.lower()
        verb_lower = verb.lower()
        
        # 1. Milestone: Exact or substring match in baseline
        for b_name in baseline_canonical_names:
            if statement_lower in b_name.lower() or b_name.lower() in statement_lower:
                return "MILESTONE"
                
        # 2. Issue: Has the problem already occurred?
        if any(kw in statement_lower for kw in ['outage', 'failed', 'defect', 'incident', 'error']):
            if "test" not in statement_lower: 
                return "ISSUE"

        # 3. Dependency: External blocker, credential, or access requirement
        if any(kw in statement_lower for kw in ['credentials', 'access', 'vpn', 'api key', 'blocked by', 'waiting on', 'dependent on', 'missing', 'firewall']):
            if "provide" not in verb_lower and "update" not in verb_lower:
                return "DEPENDENCY"
                
        # 4. Action Item: Someone must perform work
        if verb_lower in ['provide', 'update', 'create', 'schedule', 'review', 'approve', 'complete', 'start', 'submit']:
            return "ACTION_ITEM"
            
        if item.get("owner") and item.get("due_date"):
            return "ACTION_ITEM"

        # Default to Change Request if feature-like, otherwise fallback
        return "CHANGE_REQUEST"
