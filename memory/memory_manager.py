from memory.episodic_memory import EpisodicMemory
from memory.semantic_memory import SemanticMemory
from memory.procedural_memory import ProceduralMemory
from memory.context_compaction import ContextCompaction

class MemoryManager:
    @staticmethod
    def get_context(project_id: int, query: str = None) -> dict:
        # 1. Procedural Memory
        rules = ProceduralMemory.get_rules()
        
        # 2. Semantic Memory
        semantic_data = {
            "baseline": SemanticMemory.get_approved_baseline(project_id)
        }
        if query:
            semantic_data["evidence"] = SemanticMemory.get_evidence(project_id, query)
            
        # 3. Episodic Memory + Compaction
        recent_events = EpisodicMemory.get_recent_events(project_id)
        compacted_history = ContextCompaction.compact_events(recent_events)
        
        return {
            "procedural_rules": rules,
            "semantic_data": semantic_data,
            "compacted_history": compacted_history
        }

    @staticmethod
    def log_event(project_id: int, run_id: str, event_type: str, summary: str):
        EpisodicMemory.add_event(project_id, run_id, event_type, summary)
