from services.document_service import DocumentService
from services.scope_section_detector import ScopeSectionDetector
from services.scope_candidate_extractor import ScopeCandidateExtractor
from services.scope_deduplicator import ScopeDeduplicator

chunks = DocumentService.parse_document('c:/Users/ADMIN/Desktop/Agent-2/new-demo-docx/EL_CloudMigration_Project.docx', '.docx')
chunks_with_sections = ScopeSectionDetector.detect_sections(chunks)
candidates = ScopeCandidateExtractor.extract_candidates(chunks_with_sections, 164)
print(f"Total Extracted Candidates: {len(candidates)}")
for idx, c in enumerate(candidates, 1):
    print(f"{idx}. {c['name']} (sec: {c.get('section')}, pure_milestone: {c.get('is_pure_milestone')})")
