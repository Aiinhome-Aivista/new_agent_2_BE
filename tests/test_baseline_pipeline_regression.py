import pytest
import json
from unittest.mock import patch
from services.document_tree_builder import DocumentTreeBuilder
from services.scope_section_detector import ScopeSectionDetector
from services.section_dispatcher import SectionDispatcher

# Mock the LLM to return simple validations
def mock_generate_json(prompt):
    return {
        "is_valid": True,
        "cleaned_name": "Mocked Name",
        "confidence": 0.8,
        "evidence_text": "Mocked evidence from text."
    }

@patch('services.llm_service.LLMService.generate_json', side_effect=mock_generate_json)
def test_pipeline_routing_and_extraction(mock_llm):
    # Construct a mock document with various sections
    blocks = [
        {"block_type": "heading", "text": "1.0 Project Scope"},
        {"block_type": "bullet", "text": "Develop the main portal module with authentication."},
        {"block_type": "paragraph", "text": "The Employer Portal will allow users to manage their profiles. Candidate Shortlisting will enable quick hiring. The system shall send notifications."},
        {"block_type": "heading", "text": "2.0 Deliverables"},
        {"block_type": "table", "headers": ["Deliverable / Output", "Owner", "Deadline"], "rows": [["System Design Doc", "Alice", "Oct 1"]]},
        {"block_type": "heading", "text": "3.0 Technology Stack"},
        {"block_type": "table", "headers": ["Component", "Technology"], "rows": [["Frontend", "React"]]},
        {"block_type": "heading", "text": "4.0 Commercial Terms"},
        {"block_type": "paragraph", "text": "Total cost is $100,000."},
        {"block_type": "heading", "text": "5.0 Project Timeline"},
        {"block_type": "table", "headers": ["Phase", "Task", "Deadline", "Deliverable"], "rows": [["P3", "Backend REST API Development", "2026-09-25", "Swagger API Docs"]]},
        {"block_type": "heading", "text": "6.0 Client Dependencies"},
        {"block_type": "bullet", "text": "AWS Account"},
        {"block_type": "bullet", "text": "Brand Assets"},
        {"block_type": "heading", "text": "7.0 Legal and Warranties"},
        {"block_type": "paragraph", "text": "The vendor holds no liability."},
        {"block_type": "heading", "text": "CONFIDENTIAL - DO NOT DISTRIBUTE"},
        {"block_type": "paragraph", "text": "This page is confidential."},
    ]
    
    # Phase 1: Build Tree
    doc_tree = DocumentTreeBuilder.build_tree(blocks)
    
    # Phase 2: Detect Sections
    doc_tree = ScopeSectionDetector.classify_tree(doc_tree)
    
    # Verify section classification
    sections = {s["section_name"]: s["semantic_type"] for s in doc_tree["sections"]}
    assert sections.get("1.0 Project Scope") == "FUNCTIONAL_SCOPE"
    assert sections.get("2.0 Deliverables") == "DELIVERABLES"
    assert sections.get("3.0 Technology Stack") == "TECH_STACK"
    assert sections.get("4.0 Commercial Terms") == "COMMERCIAL"
    assert sections.get("5.0 Project Timeline") == "MILESTONES"
    assert sections.get("6.0 Client Dependencies") == "CLIENT_DEPENDENCY"
    assert sections.get("7.0 Legal and Warranties") == "LEGAL"
    assert sections.get("CONFIDENTIAL - DO NOT DISTRIBUTE") == "UNKNOWN"
    
    # Phase 3: Dispatcher
    extracted = SectionDispatcher.dispatch(doc_tree)
    
    # Assertions on extracted data
    types = [e["type"] for e in extracted]
    
    # Should extract functional, deliverable, tech stack, milestone, client dependency
    assert "FUNCTIONAL_SCOPE" in types
    assert "DELIVERABLE" in types
    assert "TECH_STACK" in types
    assert "MILESTONE" in types
    assert "CLIENT_DEPENDENCY" in types
    
    # Should NOT extract commercial, legal or UNKNOWN
    assert "COMMERCIAL" not in types
    assert "LEGAL" not in types
    assert "UNKNOWN" not in types
    
    # Verify Deliverable structured metadata
    delivs = [e for e in extracted if e["type"] == "DELIVERABLE"]
    assert len(delivs) == 1
    assert delivs[0]["name"] == "System Design Doc"
    assert delivs[0]["owner"] == "Alice"
    assert delivs[0]["deadline"] == "Oct 1"
    meta_json = json.loads(delivs[0]["metadata_json"])
    assert "System Design Doc" in meta_json.values()
    
    # Verify Functional Scope atomic extraction
    funcs = [e for e in extracted if e["type"] == "FUNCTIONAL_SCOPE"]
    assert len(funcs) == 4 # 1 bullet + 3 sentences
    func_texts = [f["raw_text"] for f in funcs]
    assert any("Develop the main portal module" in t for t in func_texts)
    assert any("The Employer Portal will allow users to manage their profiles." in t for t in func_texts)
    assert any("Candidate Shortlisting will enable quick hiring." in t for t in func_texts)
    assert any("The system shall send notifications." in t for t in func_texts)
    
    # Verify Timeline Processing (Milestone)
    milestones = [e for e in extracted if e["type"] == "MILESTONE"]
    assert len(milestones) == 1
    assert milestones[0]["deadline"] == "2026-09-25"
    ms_meta = json.loads(milestones[0]["metadata_json"])
    assert ms_meta.get("phase") == "P3"
    assert ms_meta.get("deliverable") == "Swagger API Docs"
    
    # Verify Client Dependency
    deps = [e for e in extracted if e["type"] == "CLIENT_DEPENDENCY"]
    assert len(deps) == 2
    assert deps[0]["raw_text"] == "AWS Account"
    assert deps[1]["raw_text"] == "Brand Assets"
    
    # Verify Tech Stack
    techs = [e for e in extracted if e["type"] == "TECH_STACK"]
    assert len(techs) == 1
    tech_meta = json.loads(techs[0]["metadata_json"])
    assert tech_meta.get("component") == "Frontend"

if __name__ == "__main__":
    pytest.main(["-v", __file__])
