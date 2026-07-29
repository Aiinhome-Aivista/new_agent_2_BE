import concurrent.futures
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)
from services.extractors.functional_scope_extractor import FunctionalScopeExtractor
from services.extractors.deliverable_extractor import DeliverableExtractor
from services.extractors.milestone_extractor import MilestoneExtractor
from services.extractors.tech_stack_extractor import TechStackExtractor
from services.extractors.client_dependency_extractor import ClientDependencyExtractor

class SectionDispatcher:
    """
    Phase 3: Dispatcher that routes Document Tree sections to specialized extractors
    using parallel execution (ThreadPoolExecutor).
    """
    
    @classmethod
    def dispatch(cls, doc_tree: Dict[str, Any]) -> List[Dict[str, Any]]:
        extracted_entities = []
        sections = doc_tree.get("sections", [])
        
        # We will dispatch sections based on their semantic_type
        # To simulate parallel extraction, we map the extractor functions
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            
            for section in sections:
                semantic_type = section.get("semantic_type")
                
                if semantic_type == "FUNCTIONAL_SCOPE":
                    futures.append(executor.submit(FunctionalScopeExtractor.extract, section))
                elif semantic_type == "DELIVERABLES":
                    futures.append(executor.submit(DeliverableExtractor.extract, section))
                elif semantic_type == "MILESTONES":
                    futures.append(executor.submit(MilestoneExtractor.extract, section))
                    futures.append(executor.submit(DeliverableExtractor.extract, section))
                elif semantic_type == "TECH_STACK":
                    futures.append(executor.submit(TechStackExtractor.extract, section))
                elif semantic_type == "CLIENT_DEPENDENCY":
                    futures.append(executor.submit(ClientDependencyExtractor.extract, section))
                elif semantic_type in ["STAKEHOLDERS", "ACTORS"]:
                    from services.extractors.stakeholder_extractor import StakeholderExtractor
                    futures.append(executor.submit(StakeholderExtractor.extract, section))
                elif semantic_type == "INTRODUCTION":
                    pass
                elif semantic_type in ["LEGAL", "COMMERCIAL", "OUT_OF_SCOPE", "ASSUMPTIONS"]:
                    pass
                else:
                    # Log UNKNOWN sections for later analysis
                    logger.warning(f"Ignoring UNKNOWN or unhandled section: '{section.get('section_name', '')}'")

            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        extracted_entities.extend(result)
                except Exception as e:
                    logger.error(f"Extractor failed: {e}", exc_info=True)
                    
        return extracted_entities
