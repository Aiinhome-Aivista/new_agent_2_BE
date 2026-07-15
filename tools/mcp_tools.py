from services.rag_service import RAGService
from memory.memory_manager import MemoryManager

class MCPTools:
    """
    Simulates MCP-style internal tools available to the Reconciliation Agent.
    """
    @staticmethod
    def search_baseline(project_id: int, query: str) -> list:
        """Searches the approved baseline documents specifically."""
        return RAGService.retrieve_evidence(project_id, query, document_types=["EL", "IFA"])
        
    @staticmethod
    def search_documents(project_id: int, query: str, doc_types: list = None) -> list:
        """Searches all uploaded documents for the project."""
        return RAGService.retrieve_evidence(project_id, query, document_types=doc_types)
        
    @staticmethod
    def get_project_context(project_id: int) -> dict:
        """Retrieves the full context for the agent (memory tiers)."""
        return MemoryManager.get_context(project_id)
