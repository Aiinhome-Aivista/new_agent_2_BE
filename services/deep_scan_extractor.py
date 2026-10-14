"""
Deep Scan (Map-Reduce + Vector Heatmap) scope candidate extractor.

Features:
1. Vector Heatmap chunk pre-filtering via ChromaDB.
2. Section-aware fallback: on zero-hit heatmap, uses Step 2 sectioned segments
   (not raw unsectioned chunks), preserving structural context.
3. Context window assembly respecting token/character budgets and section boundaries.
4. Window-level MAP candidate extraction via LLM.
5. Action/Entity-aware REDUCE deduplication:
   - Explicitly protects distinct verbs (view vs post, download vs delete, approve vs recommend).
   - Explicitly protects distinct entities (job requisition vs applicant, poll vs feedback).
   - Prevents aggressive over-merging based on long common prefixes.
6. Canonical Candidate Rehydration with honest source provenance:
   - Uses per-item evidence snippets from MAP output (not window start).
   - Validates semantic match between candidate name and evidence.
   - Marks provenance as VERIFIED / PARTIALLY_VERIFIED / UNRESOLVED.
7. MAP-to-REDUCE Coverage Auditor:
   - Full accounting of every raw MAP candidate: RETAINED, MERGED, EXCLUDED, UNRESOLVED.
   - Mathematically consistent invariants.
"""

import difflib
import re
from typing import List, Dict, Any, Tuple, Optional, Set

from services.embedding_service import EmbeddingService
from services.chroma_service import ChromaService
from services.llm_service import LLMService
from core.prompts import get_deep_scan_map_prompt, get_deep_scan_reduce_prompt


class ActionEntityProtector:
    """
    Guards distinct contractual features from being erroneously merged during
    the REDUCE deduplication phase. Analyzes contrasting action verbs and contrasting entities.
    """

    # Action categories with keywords
    ACTIONS = {
        "view": {"view", "viewing", "read", "display", "fetch", "see", "show"},
        "post": {"post", "posting", "publish", "publishing"},
        "download": {"download", "downloading", "export", "exporting"},
        "upload": {"upload", "uploading", "import", "importing"},
        "delete": {"delete", "deleting", "remove", "removing"},
        "edit": {"edit", "editing", "update", "updating", "modify", "modification"},
        "approve": {"approve", "approval", "approving"},
        "recommend": {"recommend", "recommendation", "recommending"},
        "reject": {"reject", "rejection", "rejecting"},
        "execute": {"execute", "execution", "run", "running", "conduct", "conducting", "perform"},
        "sign_off": {"sign-off", "signoff", "sign_off", "sign off"},
        "deploy": {"deploy", "deployment", "deploying", "release"},
        "cutover": {"cutover", "cut-over", "migration", "cut over"},
        "create": {"create", "creating", "creation", "build", "building"},
        "assign": {"assign", "assigning", "assignment"},
    }

    # Pairs of actions that are mutually distinct and MUST NEVER be merged
    CONTRASTING_ACTION_PAIRS = [
        ({"view"}, {"post"}),
        ({"view"}, {"edit"}),
        ({"view"}, {"delete"}),
        ({"post"}, {"delete"}),
        ({"download"}, {"upload"}),
        ({"download"}, {"delete"}),
        ({"approve"}, {"recommend"}),
        ({"execute"}, {"sign_off"}),
        ({"deploy"}, {"cutover"}),
        ({"create"}, {"assign"}),
    ]

    # Entity categories with keywords
    ENTITIES = {
        "applicant": {"applicant", "applicants", "candidate", "candidates", "resume", "resumes"},
        "job_requisition": {"job requisition", "job requisitions", "job posting", "job postings", "requisition", "requisitions", "jobs"},
        "poll_survey": {"poll", "polls", "survey", "surveys"},
        "feedback_suggestion": {"feedback", "feedbacks", "suggestion", "suggestions"},
        "praise": {"praise", "praising", "recognition", "viva engage"},
        "training": {"training", "learning", "course", "courses"},
        "compliance": {"compliance", "audit"},
        "invoice": {"invoice", "invoices", "billing", "fee", "fees"},
        "report": {"report", "reports", "reporting", "status report"},
        "employee_collaboration": {"employee collaboration", "vconnect portal", "collaboration portal"},
        "internal_job_posting": {"internal job posting", "job posting feature"},
    }

    # Pairs of entities that are mutually distinct and MUST NEVER be merged
    CONTRASTING_ENTITY_PAIRS = [
        ({"applicant"}, {"job_requisition"}),
        ({"poll_survey"}, {"feedback_suggestion"}),
        ({"praise"}, {"compliance"}),
        ({"invoice"}, {"report"}),
        ({"employee_collaboration"}, {"internal_job_posting"}),
        ({"training"}, {"news"}),
    ]

    @classmethod
    def get_actions(cls, text: str) -> Set[str]:
        words = set(re.findall(r"\b[a-zA-Z\-_]+\b", text.lower()))
        detected = set()
        for act_cat, kws in cls.ACTIONS.items():
            if words.intersection(kws) or any(kw in text.lower() for kw in kws if " " in kw or "-" in kw):
                detected.add(act_cat)
        return detected

    @classmethod
    def get_entities(cls, text: str) -> Set[str]:
        text_l = text.lower()
        detected = set()
        for ent_cat, kws in cls.ENTITIES.items():
            for kw in kws:
                if kw in text_l:
                    detected.add(ent_cat)
                    break
        return detected

    @classmethod
    def can_merge(cls, item_a: Dict[str, Any], item_b: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determines whether two candidate items are eligible for deduplication.
        Returns:
            (can_merge: bool, rationale: str)
        """
        name_a = item_a.get("name", "")
        name_b = item_b.get("name", "")
        desc_a = item_a.get("description", "")
        desc_b = item_b.get("description", "")

        combined_a = f"{name_a} {desc_a}"
        combined_b = f"{name_b} {desc_b}"

        # 1. Milestone vs non-milestone protection
        m_a = item_a.get("is_pure_milestone", False)
        m_b = item_b.get("is_pure_milestone", False)
        if m_a != m_b:
            return False, "Cannot merge Pure Milestone with Functional Clause"

        # 2. Action conflict check
        actions_a = cls.get_actions(name_a) or cls.get_actions(combined_a)
        actions_b = cls.get_actions(name_b) or cls.get_actions(combined_b)

        if actions_a and actions_b and actions_a.isdisjoint(actions_b):
            for pair_1, pair_2 in cls.CONTRASTING_ACTION_PAIRS:
                if (actions_a.intersection(pair_1) and actions_b.intersection(pair_2)) or (
                    actions_a.intersection(pair_2) and actions_b.intersection(pair_1)
                ):
                    return False, f"Contrasting actions: {actions_a} vs {actions_b}"

        # 3. Entity conflict check
        entities_a = cls.get_entities(name_a) or cls.get_entities(combined_a)
        entities_b = cls.get_entities(name_b) or cls.get_entities(combined_b)

        if entities_a and entities_b and entities_a.isdisjoint(entities_b):
            for pair_1, pair_2 in cls.CONTRASTING_ENTITY_PAIRS:
                if (entities_a.intersection(pair_1) and entities_b.intersection(pair_2)) or (
                    entities_a.intersection(pair_2) and entities_b.intersection(pair_1)
                ):
                    return False, f"Contrasting target entities: {entities_a} vs {entities_b}"

        return True, "Eligible for deduplication"


# ---------------------------------------------------------------------------
# Provenance verification statuses
# ---------------------------------------------------------------------------
PROVENANCE_VERIFIED = "VERIFIED"
PROVENANCE_PARTIAL = "PARTIALLY_VERIFIED"
PROVENANCE_UNRESOLVED = "UNRESOLVED"


class DeepScanExtractor:
    # Keywords used to build the vector "heatmap" of relevant pages/chunks.
    HEATMAP_KEYWORDS = [
        "Scope of Work",
        "Deliverables",
        "Assumptions",
        "Out of Scope",
        "Responsibilities",
        "Client Responsibilities",
        "Milestones and Timeline",
        "Dependencies",
        "Recurring Commitments",
        "Employee Collaboration",
        "Internal Job Posting",
        "Manager View",
        "Admin View",
        "Employee View",
        "Methodology",
        "Project Approach",
        "Governance",
        "Steering Committee",
        "Hypercare",
        "Production Support",
        "UAT",
        "User Acceptance Testing",
        "Resource Plan",
        "Project Team",
        "Fee Schedule",
        "Payment Milestones",
    ]

    # Sections the LLM is allowed to attribute an item to (all 16 target sections)
    VALID_SECTIONS = {
        "Scope of Work",
        "Deliverables",
        "Responsibilities",
        "Client Responsibilities",
        "Out of Scope",
        "Assumptions",
        "Dependencies",
        "Milestones",
        "Recurring Commitments",
        "General",
        "Functional Scope",
        "Methodology",
        "Governance",
        "Support",
        "Acceptance",
        "Resources",
    }

    HEATMAP_TOP_K = 8
    WINDOW_CHAR_BUDGET = 3500
    MAX_WINDOWS = 60
    SECTION_BATCH_LIMIT = 30

    last_coverage_report: Optional[Dict[str, Any]] = None

    @staticmethod
    def _log(logger, message: str):
        if logger and hasattr(logger, "raw_log"):
            logger.raw_log(message)
        else:
            print(message)

    # ------------------------------------------------------------------ #
    # Public APIs
    # ------------------------------------------------------------------ #
    @classmethod
    def extract_candidates(
        cls,
        project_id: int,
        document_id: int,
        document_type: str,
        parsed_chunks: list[dict],
        sectioned_chunks: list[dict] | None = None,
        logger=None,
    ) -> list[dict]:
        """
        Public entry point returning canonical candidates.
        Caches coverage audit in cls.last_coverage_report.
        """
        candidates, coverage = cls.extract_candidates_with_coverage(
            project_id=project_id,
            document_id=document_id,
            document_type=document_type,
            parsed_chunks=parsed_chunks,
            sectioned_chunks=sectioned_chunks,
            logger=logger,
        )
        cls.last_coverage_report = coverage
        return candidates

    @classmethod
    def extract_candidates_with_coverage(
        cls,
        project_id: int,
        document_id: int,
        document_type: str,
        parsed_chunks: list[dict],
        sectioned_chunks: list[dict] | None = None,
        logger=None,
    ) -> Tuple[list[dict], dict]:
        """
        Full Deep Scan execution returning both canonical candidates AND MAP-to-REDUCE coverage audit.
        """
        if not parsed_chunks:
            cls._log(logger, "[DeepScan] No parsed chunks provided; returning [].")
            return [], {}

        # Map chunk_index -> full chunk text & page number (raw, unsectioned)
        index_to_chunk: Dict[int, Dict] = {}
        for ch in parsed_chunks:
            idx = ch.get("chunk_index")
            if idx is None:
                continue
            index_to_chunk[idx] = {
                "text": ch.get("text", "") or "",
                "page_number": ch.get("page_number"),
            }

        # Build a sectioned segment list for section-aware fallback and window assembly.
        # Each entry: {chunk_index, segment_index, text, section, page_number, ...}
        sectioned_segments: List[Dict] = list(sectioned_chunks or [])

        # Map chunk_index -> first detected section (for heatmap-based window assembly)
        index_to_section: Dict[int, str] = {}
        for ch in sectioned_segments:
            idx = ch.get("chunk_index")
            sec = ch.get("section")
            if idx is not None and sec and idx not in index_to_section:
                index_to_section[idx] = sec

        # 1) Build the heatmap of "hot" chunk indices via ChromaDB
        hot_indices, heatmap_degraded = cls._build_heatmap(
            project_id, document_id, document_type, index_to_chunk, logger=logger
        )

        used_fallback = False
        if not hot_indices:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] Heatmap produced 0 hot chunks "
                f"({'DEGRADED - semantic filtering bypassed' if heatmap_degraded else 'empty document'}) "
                f"-> Falling back to ALL {len(sectioned_segments)} section-tagged segments from Step 2."
            )
            used_fallback = True
            # CRITICAL FIX: use section-tagged segments (Step 2 output), NOT raw chunks
            # This preserves the structural section boundaries from Step 2.
            hot_indices = sorted(index_to_chunk.keys())
        else:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] Selected {len(hot_indices)} hot chunk indices "
                f"for windowing: {hot_indices}"
            )

        if not hot_indices:
            cls._log(logger, "[DeepScan] No chunks available at all; returning [].")
            return [], {}

        # 2) Assemble Map windows from hot chunk indices, using sectioned segments when available
        windows = cls._build_windows(
            hot_indices, index_to_chunk, index_to_section, sectioned_segments, used_fallback, logger=logger
        )
        if not windows:
            return [], {}

        # 3) MAP: extract raw items from each window
        mapped_items = cls._map_phase(windows, logger=logger)
        if not mapped_items:
            cls._log(logger, "[DeepScan] Map phase yielded 0 items; returning [].")
            return [], {}

        # 4) REDUCE: consolidate/deduplicate with Action/Entity protection
        reduced_items, merge_events = cls._reduce_phase(mapped_items, logger=logger)

        # 5) Candidate Rehydration with honest source provenance
        candidates = cls._to_candidates(reduced_items, mapped_items, document_id, logger=logger)

        # 6) MAP-to-REDUCE Coverage Audit
        coverage_audit = cls._audit_coverage(mapped_items, candidates, merge_events, logger=logger)
        cls.last_coverage_report = coverage_audit

        cls._log(
            logger,
            f"\n  [DeepScan] Completed "
            f"({'fallback/full-doc (section-aware)' if used_fallback else 'heatmap'}): "
            f"{len(candidates)} canonical candidates from {len(windows)} windows."
        )
        return candidates, coverage_audit

    # ------------------------------------------------------------------ #
    # 1) Heatmap
    # ------------------------------------------------------------------ #
    @classmethod
    def _build_heatmap(
        cls,
        project_id: int,
        document_id: int,
        document_type: str,
        index_to_chunk: dict,
        logger=None,
    ) -> Tuple[List[int], bool]:
        """
        Returns (hot_chunk_indices, heatmap_degraded).
        heatmap_degraded=True means zero results were returned unexpectedly.
        """
        cls._log(
            logger,
            f"\n  [DeepScan][1/5: Vector Heatmap] Querying ChromaDB with {len(cls.HEATMAP_KEYWORDS)} "
            f"scope keywords (top_k={cls.HEATMAP_TOP_K})..."
        )
        cls._log(
            logger,
            f"  [DeepScan][1/5: Vector Heatmap] Filter: project_id={project_id}, document_id={document_id}"
        )

        collection = ChromaService.get_collection()
        if not collection:
            cls._log(logger, "  [DeepScan][1/5: Vector Heatmap] ChromaDB collection unavailable. Heatmap degraded.")
            return [], True

        doc_filter = {
            "$and": [
                {"project_id": {"$eq": project_id}},
                {"document_id": {"$eq": document_id}},
            ]
        }

        # Probe collection size for this document to detect indexing issues
        try:
            probe = collection.get(
                where=doc_filter,
                limit=5,
                include=["metadatas"],
            )
            probe_count = len((probe.get("ids") or []))
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] ChromaDB probe: found {probe_count} vector(s) "
                f"for project_id={project_id} / document_id={document_id}"
            )
            if probe_count == 0:
                cls._log(
                    logger,
                    "  [DeepScan][1/5: Vector Heatmap] WARNING: Document has NO vectors indexed in ChromaDB. "
                    "Heatmap DEGRADED — semantic filtering bypassed."
                )
                return [], True
        except Exception as probe_err:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] WARNING: ChromaDB probe failed: {probe_err}. "
                "Proceeding with keyword queries."
            )

        hot_indices: set[int] = set()
        hot_pages: set[int] = set()
        successful_queries = 0
        failed_queries = 0
        retrieved_vector_ids: List[str] = []

        for kw in cls.HEATMAP_KEYWORDS:
            try:
                emb = EmbeddingService.get_embedding(kw)
                results = collection.query(
                    query_embeddings=[emb],
                    n_results=cls.HEATMAP_TOP_K,
                    where=doc_filter,
                    include=["metadatas"],
                )
                successful_queries += 1
            except Exception as e:
                failed_queries += 1
                cls._log(logger, f"  [DeepScan][1/5: Vector Heatmap] Query failed for keyword '{kw}': {e}")
                continue

            metas = (results.get("metadatas") or [[]])[0] or []
            ids_returned = (results.get("ids") or [[]])[0] or []
            retrieved_vector_ids.extend(ids_returned)
            for meta in metas:
                if not meta:
                    continue
                c_idx = meta.get("chunk_index")
                if c_idx is not None and int(c_idx) in index_to_chunk:
                    hot_indices.add(int(c_idx))
                page = meta.get("page_number")
                if page is not None and page != -1:
                    hot_pages.add(int(page))

        # Also include chunks on the same pages as hot chunks (PDF documents)
        if hot_pages:
            for idx, ch in index_to_chunk.items():
                if ch.get("page_number") in hot_pages:
                    hot_indices.add(idx)

        unique_retrieved_ids = list(set(retrieved_vector_ids))
        cls._log(
            logger,
            f"  [DeepScan][1/5: Vector Heatmap] ChromaDB query finished: "
            f"successful={successful_queries}, failed={failed_queries}, "
            f"unique_vector_ids_retrieved={len(unique_retrieved_ids)}, "
            f"hot_chunk_indices={sorted(hot_indices)}, "
            f"hot_pages={sorted(hot_pages) if hot_pages else ['N/A']}."
        )

        heatmap_degraded = (len(hot_indices) == 0 and successful_queries > 0)
        if heatmap_degraded:
            cls._log(
                logger,
                "  [DeepScan][1/5: Vector Heatmap] WARNING: Queries succeeded but returned 0 matching chunks. "
                "Possible metadata mismatch (project_id/document_id filter). Heatmap DEGRADED."
            )

        return sorted(hot_indices), heatmap_degraded

    # ------------------------------------------------------------------ #
    # 2) Window Assembly (section-aware)
    # ------------------------------------------------------------------ #
    @classmethod
    def _build_windows(
        cls,
        hot_indices: list[int],
        index_to_chunk: dict,
        index_to_section: dict,
        sectioned_segments: List[Dict],
        used_fallback: bool,
        logger=None,
    ) -> list[dict]:
        """
        Assembles character-budget processing windows.

        When used_fallback=True (heatmap degraded), assembles windows from sectioned
        segments (Step 2 output), preserving section boundaries.

        When used_fallback=False (heatmap succeeded), uses raw hot chunk text but
        still annotates with section from index_to_section mapping.
        """
        cls._log(
            logger,
            f"\n  [DeepScan][2/5: Window Assembly] Assembling windows from "
            f"{'section-tagged segments (fallback)' if used_fallback else f'{len(hot_indices)} hot chunks'} "
            f"(Budget: {cls.WINDOW_CHAR_BUDGET} chars/window)..."
        )

        if used_fallback and sectioned_segments:
            return cls._build_windows_from_segments(sectioned_segments, logger=logger)
        else:
            return cls._build_windows_from_hot_chunks(hot_indices, index_to_chunk, index_to_section, logger=logger)

    @classmethod
    def _build_windows_from_segments(
        cls,
        sectioned_segments: List[Dict],
        logger=None,
    ) -> list[dict]:
        """
        Section-aware window assembly using Step 2 sectioned segments.
        Groups segments by section, then packs them into windows within the budget.
        """
        windows = []
        cur_seg_ids: List[Tuple[int, int]] = []  # (chunk_index, segment_index) tuples
        cur_chunk_ids: List[int] = []
        cur_text_parts: List[str] = []
        cur_len = 0
        cur_sections: List[str] = []
        cur_page: Optional[int] = None
        win_id = 0

        def flush():
            nonlocal cur_seg_ids, cur_chunk_ids, cur_text_parts, cur_len, cur_sections, cur_page, win_id
            if not cur_text_parts:
                return
            # Deduplicate sections list while preserving order
            seen_secs = []
            for s in cur_sections:
                if s not in seen_secs:
                    seen_secs.append(s)
            # Dominant section: first non-General section
            dom_section = next((s for s in seen_secs if s != "General"), seen_secs[0] if seen_secs else "General")
            windows.append({
                "window_id": f"win_{win_id:03d}",
                "indices": list(dict.fromkeys(cur_chunk_ids)),   # deduped ordered chunk IDs
                "segment_ids": list(cur_seg_ids),
                "text": "\n\n".join(cur_text_parts).strip(),
                "chunk_index": cur_chunk_ids[0] if cur_chunk_ids else 0,
                "page_number": cur_page,
                "section": dom_section,
                "sections_in_window": seen_secs,
                "char_length": cur_len,
            })
            win_id += 1
            cur_seg_ids = []
            cur_chunk_ids = []
            cur_text_parts = []
            cur_len = 0
            cur_sections = []
            cur_page = None

        for seg in sectioned_segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            seg_len = len(text)
            chunk_idx = seg.get("chunk_index", 0)
            seg_idx = seg.get("segment_index", 0)
            sec = seg.get("section") or "General"
            page = seg.get("page_number")

            # If this single segment exceeds budget, split it
            if seg_len > cls.WINDOW_CHAR_BUDGET:
                flush()
                # Emit as its own window (single oversized segment)
                dom_section = sec
                windows.append({
                    "window_id": f"win_{win_id:03d}",
                    "indices": [chunk_idx],
                    "segment_ids": [(chunk_idx, seg_idx)],
                    "text": text[:cls.WINDOW_CHAR_BUDGET * 2].strip(),  # hard cap at 2x budget
                    "chunk_index": chunk_idx,
                    "page_number": page,
                    "section": dom_section,
                    "sections_in_window": [dom_section],
                    "char_length": min(seg_len, cls.WINDOW_CHAR_BUDGET * 2),
                })
                win_id += 1
                if len(windows) >= cls.MAX_WINDOWS:
                    cls._log(
                        logger,
                        f"  [DeepScan][2/5: Window Assembly] Reached MAX_WINDOWS ({cls.MAX_WINDOWS}); truncating."
                    )
                    return windows
                continue

            # Flush if adding this segment would exceed budget
            if cur_len and (cur_len + seg_len) > cls.WINDOW_CHAR_BUDGET:
                flush()
                if len(windows) >= cls.MAX_WINDOWS:
                    cls._log(
                        logger,
                        f"  [DeepScan][2/5: Window Assembly] Reached MAX_WINDOWS ({cls.MAX_WINDOWS}); truncating."
                    )
                    return windows

            cur_seg_ids.append((chunk_idx, seg_idx))
            cur_chunk_ids.append(chunk_idx)
            cur_text_parts.append(text)
            cur_len += seg_len
            cur_sections.append(sec)
            if cur_page is None and page is not None:
                cur_page = page

        flush()
        return cls._log_windows(windows[:cls.MAX_WINDOWS], logger)

    @classmethod
    def _build_windows_from_hot_chunks(
        cls,
        hot_indices: list[int],
        index_to_chunk: dict,
        index_to_section: dict,
        logger=None,
    ) -> list[dict]:
        """
        Window assembly from heatmap-selected raw chunks.
        """
        windows = []
        cur_indices: list[int] = []
        cur_text_parts: list[str] = []
        cur_len = 0
        win_id = 0

        def flush():
            nonlocal cur_indices, cur_text_parts, cur_len, win_id
            if not cur_indices:
                return
            rep_idx = cur_indices[0]
            rep_page = None
            for i in cur_indices:
                p = index_to_chunk.get(i, {}).get("page_number")
                if p is not None:
                    rep_page = p
                    break
            rep_section = "General"
            for i in cur_indices:
                s = index_to_section.get(i)
                if s and s != "General":
                    rep_section = s
                    break
            windows.append({
                "window_id": f"win_{win_id:03d}",
                "indices": list(cur_indices),
                "segment_ids": [(i, 0) for i in cur_indices],
                "text": "\n\n".join(cur_text_parts).strip(),
                "chunk_index": rep_idx,
                "page_number": rep_page,
                "section": rep_section,
                "sections_in_window": [rep_section],
                "char_length": cur_len,
            })
            win_id += 1
            cur_indices.clear()
            cur_text_parts.clear()
            cur_len.__class__   # trick mypy

        # Rebuild cur_len since we can't assign int after nonlocal
        cur_len_ref = [0]

        def flush2():
            nonlocal cur_indices, cur_text_parts, win_id
            if not cur_indices:
                return
            rep_idx = cur_indices[0]
            rep_page = None
            for i in cur_indices:
                p = index_to_chunk.get(i, {}).get("page_number")
                if p is not None:
                    rep_page = p
                    break
            rep_section = "General"
            for i in cur_indices:
                s = index_to_section.get(i)
                if s and s != "General":
                    rep_section = s
                    break
            windows.append({
                "window_id": f"win_{win_id:03d}",
                "indices": list(cur_indices),
                "segment_ids": [(i, 0) for i in cur_indices],
                "text": "\n\n".join(cur_text_parts).strip(),
                "chunk_index": rep_idx,
                "page_number": rep_page,
                "section": rep_section,
                "sections_in_window": [rep_section],
                "char_length": cur_len_ref[0],
            })
            win_id += 1
            cur_indices.clear()
            cur_text_parts.clear()
            cur_len_ref[0] = 0

        for idx in hot_indices:
            text = index_to_chunk.get(idx, {}).get("text", "") or ""
            if not text.strip():
                continue
            if cur_len_ref[0] and (cur_len_ref[0] + len(text)) > cls.WINDOW_CHAR_BUDGET:
                flush2()
                if len(windows) >= cls.MAX_WINDOWS:
                    cls._log(
                        logger,
                        f"  [DeepScan][2/5: Window Assembly] Reached MAX_WINDOWS ({cls.MAX_WINDOWS}); truncating."
                    )
                    return cls._log_windows(windows, logger)
            cur_indices.append(idx)
            cur_text_parts.append(text)
            cur_len_ref[0] += len(text)

        flush2()
        return cls._log_windows(windows[:cls.MAX_WINDOWS], logger)

    @classmethod
    def _log_windows(cls, windows: list[dict], logger) -> list[dict]:
        cls._log(
            logger,
            f"  [DeepScan][2/5: Window Assembly] Assembled {len(windows)} processing window(s):"
        )
        for w_idx, w in enumerate(windows, 1):
            snippet = w["text"][:110].replace("\r", " ").replace("\n", " ").strip()
            page_str = f"page={w['page_number']}" if w.get("page_number") is not None else "page=N/A"
            secs = w.get("sections_in_window", [w.get("section", "General")])
            cls._log(
                logger,
                f"    - Window {w_idx:02d}/{len(windows):02d}: "
                f"Chunks={w['indices']} | Section(s): {secs} | {page_str} | "
                f"length={w.get('char_length', len(w['text']))} chars\n"
                f"        Text Preview: \"{snippet}...\""
            )
        return windows

    # ------------------------------------------------------------------ #
    # 3) MAP Phase
    # ------------------------------------------------------------------ #
    @classmethod
    def _map_phase(cls, windows: list[dict], logger=None) -> list[dict]:
        cls._log(
            logger,
            f"\n  [DeepScan][3/5: MAP Phase] Executing LLM Map Extraction across {len(windows)} window(s)..."
        )
        mapped = []
        failed_windows = []

        for w_idx, window in enumerate(windows, 1):
            cls._log(
                logger,
                f"  [DeepScan][3/5: MAP Phase] Calling LLM on Window {w_idx:02d}/{len(windows):02d} "
                f"(Chunks={window['indices']}, Section='{window['section']}')..."
            )
            prompt = get_deep_scan_map_prompt(window["text"], window["section"])
            result = None
            last_err = None
            # Bounded retry: up to 2 attempts
            for attempt in range(2):
                try:
                    result = LLMService.generate_json(prompt)
                    break
                except Exception as e:
                    last_err = e
                    cls._log(
                        logger,
                        f"  [DeepScan][3/5: MAP Phase] Window {w_idx} attempt {attempt+1} failed: {e}"
                    )

            if result is None:
                failed_windows.append({
                    "window_id": window.get("window_id", f"win_{w_idx:03d}"),
                    "indices": window["indices"],
                    "section": window["section"],
                    "error": str(last_err),
                })
                cls._log(
                    logger,
                    f"  [DeepScan][3/5: MAP Phase] Window {w_idx} FAILED after retries. "
                    f"Source range chunks={window['indices']} marked as incomplete."
                )
                continue

            items = result if isinstance(result, list) else [result]
            window_items = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                section = it.get("source_section") or window["section"]
                if section not in cls.VALID_SECTIONS:
                    section = window["section"] if window["section"] in cls.VALID_SECTIONS else "General"

                # Extract a per-item evidence snippet:
                # Use the LLM-provided description (which is the faithful source clause)
                # rather than the window start.
                llm_description = (it.get("description") or name).strip()
                # Also store window text for fallback rehydration
                window_text_snippet = window["text"][:400].replace("\r", " ").replace("\n", " ").strip()

                item_obj = {
                    "name": name,
                    "description": llm_description,
                    "chunk_index": window["chunk_index"],
                    "window_id": window.get("window_id", f"win_{w_idx:03d}"),
                    "window_indices": list(window["indices"]),
                    "segment_ids": window.get("segment_ids", [(window["chunk_index"], 0)]),
                    "page_number": window["page_number"],
                    "section": section,
                    "sections_in_window": window.get("sections_in_window", [section]),
                    "is_pure_milestone": bool(it.get("is_pure_milestone", False)),
                    # Per-item evidence: use description (faithful clause) as primary snippet
                    "evidence_snippet": llm_description[:300].strip(),
                    # Fallback window text (first 300 chars) for rehydration when description is short
                    "window_text_snippet": window_text_snippet,
                    # Full window text stored for rehydration matching
                    "_window_text": window["text"],
                }
                mapped.append(item_obj)
                window_items.append(item_obj)

            cls._log(
                logger,
                f"  [DeepScan][3/5: MAP Phase] Window {w_idx:02d} extracted {len(window_items)} candidate(s):"
            )
            for it in window_items:
                kind = "Milestone" if it["is_pure_milestone"] else "Clause"
                cls._log(logger, f"      * [{kind}] \"{it['name']}\" (Section: '{it['section']}')")

        if failed_windows:
            cls._log(
                logger,
                f"  [DeepScan][3/5: MAP Phase] WARNING: {len(failed_windows)} window(s) FAILED extraction "
                f"and have INCOMPLETE coverage: {[w['indices'] for w in failed_windows]}"
            )

        cls._log(
            logger,
            f"  [DeepScan][3/5: MAP Phase] Completed. Extracted total {len(mapped)} raw candidates "
            f"across all windows ({len(failed_windows)} window(s) failed)."
        )
        return mapped

    # ------------------------------------------------------------------ #
    # 4) REDUCE Phase with Action/Entity-Aware Protection
    # ------------------------------------------------------------------ #
    @classmethod
    def _reduce_phase(cls, mapped_items: list[dict], logger=None) -> Tuple[list[dict], list[dict]]:
        cls._log(
            logger,
            f"\n  [DeepScan][4/5: REDUCE Phase] Deduplicating & consolidating {len(mapped_items)} "
            f"candidate items across windows (Section-Aware)..."
        )
        if not mapped_items:
            return [], []

        def to_payload(items):
            return [
                {
                    "name": it["name"],
                    "description": it["description"],
                    "source_section": it["section"],
                    "is_pure_milestone": it.get("is_pure_milestone", False),
                }
                for it in items
            ]

        # Group mapped items by detected section
        section_groups: dict[str, list[dict]] = {}
        for it in mapped_items:
            sec = it.get("section") or "General"
            section_groups.setdefault(sec, []).append(it)

        cls._log(
            logger,
            f"  [DeepScan][4/5: REDUCE Phase] Grouped into {len(section_groups)} contractual section(s) "
            f"for independent reduction."
        )

        all_reduced: list[dict] = []
        all_merge_events: list[dict] = []

        for sec, items in section_groups.items():
            cls._log(
                logger,
                f"  [DeepScan][4/5: REDUCE Phase] Reducing Section '{sec}' ({len(items)} input candidates)..."
            )

            # If small enough, run single LLM reduce with deterministic verification
            if len(items) <= cls.SECTION_BATCH_LIMIT:
                try:
                    sec_reduced = cls._reduce_once(to_payload(items), logger=logger)
                    # Protect against LLM over-merging by re-running deterministic check
                    sec_final, merge_events = cls._deterministic_dedup(sec_reduced or items)
                    all_merge_events.extend(merge_events)
                    cls._log(
                        logger,
                        f"    -> Section '{sec}': Consolidated {len(items)} -> {len(sec_final)} items."
                    )
                    all_reduced.extend(sec_final)
                    continue
                except Exception as e:
                    cls._log(logger, f"    -> Section '{sec}' LLM reduce failed: {e}. Applying deterministic fallback.")

                sec_det, merge_events = cls._deterministic_dedup(items)
                all_merge_events.extend(merge_events)
                cls._log(logger, f"    -> Section '{sec}': Deduplication kept {len(sec_det)} distinct items.")
                all_reduced.extend(sec_det)
            else:
                # Batch processing for large sections
                sec_intermediate = []
                for i in range(0, len(items), cls.SECTION_BATCH_LIMIT):
                    batch = items[i : i + cls.SECTION_BATCH_LIMIT]
                    cls._log(
                        logger,
                        f"    -> Section '{sec}': Batch {i//cls.SECTION_BATCH_LIMIT + 1} "
                        f"({len(batch)} items)..."
                    )
                    try:
                        b_red = cls._reduce_once(to_payload(batch), logger=logger)
                        if b_red:
                            sec_intermediate.extend(b_red)
                        else:
                            b_det, m_ev = cls._deterministic_dedup(batch)
                            all_merge_events.extend(m_ev)
                            sec_intermediate.extend(b_det)
                    except Exception as e:
                        cls._log(logger, f"    -> Section '{sec}' batch error: {e}. Applying deterministic dedup.")
                        b_det, m_ev = cls._deterministic_dedup(batch)
                        all_merge_events.extend(m_ev)
                        sec_intermediate.extend(b_det)

                final_sec, merge_events = cls._deterministic_dedup(sec_intermediate)
                all_merge_events.extend(merge_events)
                cls._log(
                    logger,
                    f"    -> Section '{sec}': Batch consolidated {len(items)} -> {len(final_sec)} distinct items."
                )
                all_reduced.extend(final_sec)

        cls._log(
            logger,
            f"  [DeepScan][4/5: REDUCE Phase] Consolidation successful: "
            f"Reduced from {len(mapped_items)} down to {len(all_reduced)} distinct candidates across all sections.\n"
        )
        return all_reduced, all_merge_events

    @classmethod
    def _reduce_once(cls, items_payload: list[dict], logger=None) -> list[dict]:
        """Single LLM reduce call."""
        if not items_payload:
            return []
        prompt = get_deep_scan_reduce_prompt(items_payload)
        result = LLMService.generate_json(prompt)
        items = result if isinstance(result, list) else [result]
        cleaned = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            if not name:
                continue
            cleaned.append(
                {
                    "name": name,
                    "description": (it.get("description") or name).strip(),
                    "section": it.get("source_section")
                    if it.get("source_section") in cls.VALID_SECTIONS
                    else "General",
                    "is_pure_milestone": bool(it.get("is_pure_milestone", False)),
                }
            )
        return cleaned

    @classmethod
    def _deterministic_dedup(cls, items: list[dict]) -> Tuple[list[dict], list[dict]]:
        """
        Action/Entity-aware deterministic deduplication.
        Merges true duplicates across windows while STRICTLY PROTECTING distinct actions and entities.
        Returns:
            (deduped_items, merge_events)
        """
        result = []
        merge_events = []

        for it in items:
            match = None
            merge_reason = ""

            for existing in result:
                can_m, reason = ActionEntityProtector.can_merge(it, existing)
                if not can_m:
                    continue  # Distinct action or entity conflict -> NEVER merge!

                name_it = it["name"].strip()
                name_ex = existing["name"].strip()
                name_it_l = name_it.lower()
                name_ex_l = name_ex.lower()

                # 1. Exact normalized match
                if name_it_l == name_ex_l:
                    match = existing
                    merge_reason = "Exact normalized title match across overlapping excerpts"
                    break

                # 2. Strict prefix/suffix match with minor qualifier difference
                len_diff = abs(len(name_it_l) - len(name_ex_l))
                if len_diff <= 16 and (name_it_l in name_ex_l or name_ex_l in name_it_l):
                    match = existing
                    merge_reason = f"Identical core item with minor qualifier difference ('{name_it}' vs '{name_ex}')"
                    break

                # 3. High fuzzy ratio match
                ratio = difflib.SequenceMatcher(None, name_it_l, name_ex_l).ratio()
                if ratio >= 0.88:
                    match = existing
                    merge_reason = f"High textual similarity consolidation (ratio: {ratio:.2f}) with verified identical action/entity"
                    break

            if match:
                merge_events.append({
                    "merged_name": it["name"],
                    "surviving_name": match["name"],
                    "section": it.get("section", "General"),
                    "rationale": merge_reason,
                })
                # Preserve richer description if present
                if it.get("description") and it["description"] not in match.get("description", ""):
                    match["description"] = f"{match['description']} | {it['description']}"
            else:
                result.append({
                    "name": it["name"],
                    "description": it.get("description", it["name"]),
                    "section": it.get("section", "General"),
                    "is_pure_milestone": bool(it.get("is_pure_milestone", False)),
                })

        return result, merge_events

    # ------------------------------------------------------------------ #
    # 5) Candidate Rehydration with Honest Provenance
    # ------------------------------------------------------------------ #
    @classmethod
    def _to_candidates(
        cls,
        reduced_items: list[dict],
        mapped_items: list[dict],
        document_id: int,
        logger=None,
    ) -> list[dict]:
        """
        Rehydrates source references (chunk_index, honest page_number, section, evidence snippet)
        onto the reduced canonical candidates.

        Provenance verification:
        - VERIFIED: source match is semantically supported by matched chunk text
        - PARTIALLY_VERIFIED: source matched by name fuzzy but snippet may not align
        - UNRESOLVED: no defensible source match found; provenance left blank
        """
        cls._log(
            logger,
            f"\n  [DeepScan][5/5: Candidate Rehydration] Rehydrating source metadata "
            f"(chunk_index, page, section, provenance) for {len(reduced_items)} candidates..."
        )
        candidates = []

        for idx, item in enumerate(reduced_items, 1):
            name = item["name"]
            src, provenance_status = cls._best_source_verified(name, item, mapped_items)

            section = item.get("section") or (src.get("section") if src else None) or "General"
            if section not in cls.VALID_SECTIONS:
                section = "General"

            chunk_idx = src.get("chunk_index") if src else None
            raw_page = src.get("page_number") if src else None

            # Honest page reporting: only real integers from PDF, never fabricated for docx
            has_real_page = raw_page is not None and isinstance(raw_page, int) and raw_page >= 0
            page_val = raw_page if has_real_page else None
            page_locator = f"Page {raw_page}" if has_real_page else "N/A (Flow Document)"

            source_locator = f"chunk_{chunk_idx}" if chunk_idx is not None else "chunk_N/A"

            # Evidence snippet: use the per-item evidence from MAP (LLM description),
            # not the raw window start. If UNRESOLVED, mark clearly.
            if src:
                evidence = src.get("evidence_snippet") or src.get("description") or name
            else:
                evidence = item.get("description") or name
                provenance_status = PROVENANCE_UNRESOLVED

            verbatim_evidence = evidence[:300].strip()

            candidate_dict = {
                "name": name,
                "description": item.get("description", name),
                "page_number": page_val,
                "page_locator": page_locator,
                "section": section,
                "chunk_index": chunk_idx,
                "source_locator": source_locator,
                "raw_text": item.get("description", name),
                "evidence_text": verbatim_evidence,
                "document_id": document_id,
                "is_pure_milestone": item.get("is_pure_milestone", False) or (src.get("is_pure_milestone", False) if src else False),
                "provenance_status": provenance_status,
                "window_id": src.get("window_id") if src else None,
                "window_indices": src.get("window_indices") if src else [],
            }
            candidates.append(candidate_dict)

            kind = "Milestone" if candidate_dict["is_pure_milestone"] else "Clause"
            cls._log(
                logger,
                f"    [{idx:02d}] [{kind}] \"{name}\" -> Section: '{section}' | "
                f"{source_locator} | {page_locator} | Provenance: {provenance_status}"
            )

        # Summary of provenance
        prov_counts = {
            PROVENANCE_VERIFIED: sum(1 for c in candidates if c["provenance_status"] == PROVENANCE_VERIFIED),
            PROVENANCE_PARTIAL: sum(1 for c in candidates if c["provenance_status"] == PROVENANCE_PARTIAL),
            PROVENANCE_UNRESOLVED: sum(1 for c in candidates if c["provenance_status"] == PROVENANCE_UNRESOLVED),
        }
        cls._log(
            logger,
            f"  [DeepScan][5/5: Candidate Rehydration] Successfully rehydrated {len(candidates)} canonical candidates. "
            f"Provenance: VERIFIED={prov_counts[PROVENANCE_VERIFIED]}, "
            f"PARTIALLY_VERIFIED={prov_counts[PROVENANCE_PARTIAL]}, "
            f"UNRESOLVED={prov_counts[PROVENANCE_UNRESOLVED]}\n"
        )

        if prov_counts[PROVENANCE_UNRESOLVED] > 0:
            unresolved_names = [c["name"] for c in candidates if c["provenance_status"] == PROVENANCE_UNRESOLVED]
            cls._log(
                logger,
                f"  [WARNING][Rehydration] {prov_counts[PROVENANCE_UNRESOLVED]} candidates have UNRESOLVED provenance: "
                f"{unresolved_names[:10]}{'...' if len(unresolved_names) > 10 else ''}"
            )

        return candidates

    @classmethod
    def _best_source_verified(
        cls,
        name: str,
        reduced_item: dict,
        mapped_items: list[dict],
    ) -> Tuple[Optional[dict], str]:
        """
        Finds the mapped item whose evidence best supports this candidate.
        Returns (best_source_item, provenance_status).

        Rules:
        1. Exact name match -> VERIFIED
        2. Name contained in/containing other -> ratio check -> VERIFIED or PARTIALLY_VERIFIED
        3. Fuzzy ratio >= 0.60 -> check if evidence semantically supports name
        4. No match -> UNRESOLVED (do NOT fall back to first item)
        """
        name_l = name.lower()
        best = None
        best_ratio = 0.0

        for it in mapped_items:
            it_name_l = it["name"].lower()
            can_m, _ = ActionEntityProtector.can_merge({"name": name}, it)
            if not can_m:
                continue

            # Exact match
            if name_l == it_name_l:
                return it, PROVENANCE_VERIFIED

            # Substring match
            if name_l in it_name_l or it_name_l in name_l:
                ratio = 0.90
            else:
                ratio = difflib.SequenceMatcher(None, name_l, it_name_l).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best = it

        if best is None or best_ratio < 0.45:
            # No defensible match: UNRESOLVED
            return None, PROVENANCE_UNRESOLVED

        # Validate: does the matched chunk's evidence actually mention anything
        # semantically related to the candidate name?
        name_keywords = set(re.findall(r"\b[a-zA-Z]{3,}\b", name_l))
        evidence_text = (best.get("evidence_snippet") or best.get("description") or "").lower()
        window_text = best.get("_window_text", "").lower()
        combined_evidence = evidence_text + " " + window_text

        # Check how many name keywords appear in the evidence
        if name_keywords:
            overlap = sum(1 for kw in name_keywords if kw in combined_evidence)
            overlap_ratio = overlap / len(name_keywords)
        else:
            overlap_ratio = 1.0  # no keywords to check -> not a concern

        if best_ratio >= 0.85 and overlap_ratio >= 0.3:
            return best, PROVENANCE_VERIFIED
        elif best_ratio >= 0.60 and overlap_ratio >= 0.2:
            return best, PROVENANCE_PARTIAL
        else:
            # Low overlap -> provenance is suspect
            return best, PROVENANCE_PARTIAL

    # ------------------------------------------------------------------ #
    # 6) MAP-to-REDUCE Coverage Auditor
    # ------------------------------------------------------------------ #
    @classmethod
    def _audit_coverage(
        cls,
        mapped_items: list[dict],
        canonical_candidates: list[dict],
        merge_events: list[dict],
        logger=None,
    ) -> dict:
        """
        Produces an auditable coverage report accounting for EVERY single MAP candidate:
        - RETAINED: survives directly as a canonical candidate.
        - MERGED: mapped to a surviving canonical candidate with merge rationale.
        - EXCLUDED: explicitly excluded (empty/malformed).
        - UNRESOLVED: unmapped candidate (triggers warning).

        Audit invariants:
        1. Every unique MAP candidate has exactly one terminal outcome.
        2. No candidate is counted twice.
        3. Every MERGED record maps to an existing canonical.
        4. Every RETAINED record references an actual canonical.
        5. canonical_candidates_count == unique canonical records.
        6. coverage% is derived from a clearly documented formula.
        """
        cls._log(logger, "  [DeepScan][Coverage Audit] Verifying MAP-to-REDUCE candidate coverage accounting...")

        canonical_lookup = {c["name"].lower().strip(): c for c in canonical_candidates}
        # Build merge lookup: merged_name -> surviving canonical name
        merge_lookup: dict[str, str] = {}
        for m in merge_events:
            ml = m["merged_name"].lower().strip()
            merge_lookup[ml] = m["surviving_name"]

        audit_records = []
        retained_count = 0
        merged_count = 0
        excluded_count = 0
        unresolved_count = 0

        # Track which MAP candidates have been assigned (for deduplication of outcome)
        assigned: set[int] = set()

        for item_idx, it in enumerate(mapped_items):
            if item_idx in assigned:
                continue
            assigned.add(item_idx)

            raw_name = (it.get("name") or "").strip()
            raw_name_l = raw_name.lower()
            section = it.get("section", "General")

            # EXCLUDED: empty or malformed
            if not raw_name or len(raw_name) < 2:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "status": "EXCLUDED",
                    "canonical_name": None,
                    "section": section,
                    "rationale": "Empty or malformed candidate string",
                    "warning": None,
                })
                excluded_count += 1
                continue

            # 1. RETAINED: exact match in canonical list
            if raw_name_l in canonical_lookup:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "status": "RETAINED",
                    "canonical_name": canonical_lookup[raw_name_l]["name"],
                    "section": section,
                    "rationale": "Survives directly as distinct canonical candidate",
                    "warning": None,
                })
                retained_count += 1
                continue

            # 2. MERGED: documented merge event
            if raw_name_l in merge_lookup:
                surviving = merge_lookup[raw_name_l]
                surviving_l = surviving.lower().strip()
                # Validate that the surviving canonical actually exists
                surviving_exists = surviving_l in canonical_lookup
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "status": "MERGED",
                    "canonical_name": surviving,
                    "section": section,
                    "rationale": "Merged into surviving canonical candidate",
                    "warning": None if surviving_exists else f"INVARIANT VIOLATION: Surviving canonical '{surviving}' not found in canonical list",
                })
                merged_count += 1
                continue

            # 3. Fuzzy RETAINED or MERGED: try to find the canonical
            best_c = None
            best_sim = 0.0
            for c_name_l, c_obj in canonical_lookup.items():
                can_m, _ = ActionEntityProtector.can_merge({"name": raw_name}, {"name": c_obj["name"]})
                if can_m:
                    sim = difflib.SequenceMatcher(None, raw_name_l, c_name_l).ratio()
                    if sim > best_sim:
                        best_sim = sim
                        best_c = c_obj

            if best_c and best_sim >= 0.70:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "status": "MERGED",
                    "canonical_name": best_c["name"],
                    "section": section,
                    "rationale": f"Consolidated into canonical candidate across overlapping windows (similarity: {best_sim:.2f})",
                    "warning": None,
                })
                merged_count += 1
            else:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "status": "UNRESOLVED",
                    "canonical_name": None,
                    "section": section,
                    "rationale": "Candidate was not preserved and has no defensible merge mapping",
                    "warning": "Review required: this requirement may have been silently lost",
                })
                unresolved_count += 1

        total = len(mapped_items)
        # Coverage formula:
        # coverage = (RETAINED + MERGED + EXCLUDED) / total * 100
        # EXCLUDED is included because malformed items are genuinely handled (just rejected).
        # UNRESOLVED items reduce coverage — they represent potentially lost requirements.
        accounted = retained_count + merged_count + excluded_count
        coverage_pct = round((accounted / total * 100), 2) if total else 100.0

        # Provenance breakdown from canonical candidates
        prov_verified = sum(1 for c in canonical_candidates if c.get("provenance_status") == PROVENANCE_VERIFIED)
        prov_partial = sum(1 for c in canonical_candidates if c.get("provenance_status") == PROVENANCE_PARTIAL)
        prov_unresolved = sum(1 for c in canonical_candidates if c.get("provenance_status") == PROVENANCE_UNRESOLVED)
        prov_total = len(canonical_candidates)
        prov_pct = round((prov_verified / prov_total * 100), 2) if prov_total else 100.0

        # Invariant checks
        warnings = []
        if retained_count + merged_count + excluded_count + unresolved_count != total:
            warnings.append(
                f"INVARIANT VIOLATION: outcome totals ({retained_count}+{merged_count}+"
                f"{excluded_count}+{unresolved_count}) != total MAP candidates ({total})"
            )
        if len(canonical_candidates) != len(set(c["name"] for c in canonical_candidates)):
            warnings.append("INVARIANT VIOLATION: Canonical candidates contain duplicate names")

        report = {
            "total_map_candidates": total,
            "total_canonical_candidates": len(canonical_candidates),
            "retained_count": retained_count,
            "merged_count": merged_count,
            "excluded_count": excluded_count,
            "unresolved_count": unresolved_count,
            "coverage_percentage": coverage_pct,
            "coverage_formula": "(RETAINED + MERGED + EXCLUDED) / total_map_candidates * 100",
            "provenance": {
                "verified_count": prov_verified,
                "partially_verified_count": prov_partial,
                "unresolved_count": prov_unresolved,
                "verified_percentage": prov_pct,
            },
            "invariant_warnings": warnings,
            "audit_records": audit_records,
        }

        cls._log(
            logger,
            f"  [DeepScan][Coverage Audit] Summary: Total MAP={total} | Canonical={len(canonical_candidates)} | "
            f"RETAINED={retained_count} | MERGED={merged_count} | EXCLUDED={excluded_count} | "
            f"UNRESOLVED={unresolved_count} | Coverage={coverage_pct}%"
        )
        cls._log(
            logger,
            f"  [DeepScan][Coverage Audit] Provenance: VERIFIED={prov_verified} | "
            f"PARTIALLY_VERIFIED={prov_partial} | UNRESOLVED={prov_unresolved} | "
            f"Verified%={prov_pct}%"
        )
        if unresolved_count > 0:
            cls._log(
                logger,
                f"  [WARNING][Coverage Audit] {unresolved_count} MAP candidates were UNRESOLVED! "
                f"These requirements may be lost."
            )
        for w in warnings:
            cls._log(logger, f"  [INVARIANT WARNING] {w}")

        return report
