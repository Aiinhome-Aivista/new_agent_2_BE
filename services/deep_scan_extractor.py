"""
Deep Scan (Map-Reduce + Vector Heatmap) scope candidate extractor.

Features:
1. Vector Heatmap chunk pre-filtering via ChromaDB.
2. Context window assembly respecting token/character budgets.
3. Window-level MAP candidate extraction via LLM.
4. Action/Entity-aware REDUCE deduplication:
   - Explicitly protects distinct verbs (view vs post, download vs delete, approve vs recommend).
   - Explicitly protects distinct entities (job requisition vs applicant, poll vs feedback).
   - Prevents aggressive over-merging based on long common prefixes.
5. Canonical Candidate Rehydration with honest source provenance (chunk, segment, verbatim snippet).
6. MAP-to-REDUCE Coverage Auditor:
   - Full accounting of every raw MAP candidate: RETAINED, MERGED, EXCLUDED, UNRESOLVED.
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

        # Map chunk_index -> full chunk text & page number
        index_to_chunk = {}
        for ch in parsed_chunks:
            idx = ch.get("chunk_index")
            if idx is None:
                continue
            index_to_chunk[idx] = {
                "text": ch.get("text", "") or "",
                "page_number": ch.get("page_number"),
            }

        # Map chunk_index -> detected section
        index_to_section = {}
        for ch in (sectioned_chunks or []):
            idx = ch.get("chunk_index")
            sec = ch.get("section")
            if idx is not None and sec and idx not in index_to_section:
                index_to_section[idx] = sec

        # 1) Build the heatmap of "hot" chunk indices via ChromaDB
        hot_indices = cls._build_heatmap(
            project_id, document_id, document_type, index_to_chunk, logger=logger
        )

        used_fallback = False
        if not hot_indices:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] Heatmap produced 0 hot chunks -> Falling back to all {len(index_to_chunk)} document chunks."
            )
            used_fallback = True
            hot_indices = sorted(index_to_chunk.keys())
        else:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] Selected {len(hot_indices)} hot chunks for windowing: {hot_indices}"
            )

        if not hot_indices:
            cls._log(logger, "[DeepScan] No chunks available at all; returning [].")
            return [], {}

        # 2) Assemble Map windows from hot chunks
        windows = cls._build_windows(hot_indices, index_to_chunk, index_to_section, logger=logger)
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
            f"({'fallback/full-doc' if used_fallback else 'heatmap'}): "
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
    ) -> list[int]:
        cls._log(
            logger,
            f"\n  [DeepScan][1/5: Vector Heatmap] Querying ChromaDB with {len(cls.HEATMAP_KEYWORDS)} scope keywords (top_k={cls.HEATMAP_TOP_K})..."
        )
        collection = ChromaService.get_collection()
        if not collection:
            cls._log(logger, "  [DeepScan][1/5: Vector Heatmap] ChromaDB collection unavailable.")
            return []

        doc_filter = {
            "$and": [
                {"project_id": {"$eq": project_id}},
                {"document_id": {"$eq": document_id}},
            ]
        }

        hot_indices: set[int] = set()
        hot_pages: set[int] = set()

        for kw in cls.HEATMAP_KEYWORDS:
            try:
                emb = EmbeddingService.get_embedding(kw)
                results = collection.query(
                    query_embeddings=[emb],
                    n_results=cls.HEATMAP_TOP_K,
                    where=doc_filter,
                    include=["metadatas"],
                )
            except Exception as e:
                continue

            metas = (results.get("metadatas") or [[]])[0] or []
            for meta in metas:
                if not meta:
                    continue
                c_idx = meta.get("chunk_index")
                if c_idx is not None and c_idx in index_to_chunk:
                    hot_indices.add(int(c_idx))
                page = meta.get("page_number")
                if page is not None and page != -1:
                    hot_pages.add(int(page))

        if hot_pages:
            for idx, ch in index_to_chunk.items():
                if ch.get("page_number") in hot_pages:
                    hot_indices.add(idx)

        cls._log(
            logger,
            f"  [DeepScan][1/5: Vector Heatmap] ChromaDB query finished: Found {len(hot_indices)} hot chunks across page(s) {sorted(hot_pages) if hot_pages else ['N/A']}."
        )
        return sorted(hot_indices)

    # ------------------------------------------------------------------ #
    # 2) Window Assembly
    # ------------------------------------------------------------------ #
    @classmethod
    def _build_windows(
        cls,
        hot_indices: list[int],
        index_to_chunk: dict,
        index_to_section: dict,
        logger=None,
    ) -> list[dict]:
        cls._log(
            logger,
            f"\n  [DeepScan][2/5: Window Assembly] Assembling windows from {len(hot_indices)} hot chunks (Budget: {cls.WINDOW_CHAR_BUDGET} chars/window)..."
        )
        windows = []
        cur_indices: list[int] = []
        cur_text_parts: list[str] = []
        cur_len = 0

        def flush():
            nonlocal cur_indices, cur_text_parts, cur_len
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
            windows.append(
                {
                    "indices": list(cur_indices),
                    "text": "\n\n".join(cur_text_parts).strip(),
                    "chunk_index": rep_idx,
                    "page_number": rep_page,
                    "section": rep_section,
                }
            )
            cur_indices = []
            cur_text_parts = []
            cur_len = 0

        for idx in hot_indices:
            text = index_to_chunk.get(idx, {}).get("text", "") or ""
            if not text.strip():
                continue
            if cur_len and (cur_len + len(text)) > cls.WINDOW_CHAR_BUDGET:
                flush()
                if len(windows) >= cls.MAX_WINDOWS:
                    cls._log(
                        logger,
                        f"  [DeepScan][2/5: Window Assembly] Reached MAX_WINDOWS ({cls.MAX_WINDOWS}); truncating remaining chunks."
                    )
                    return windows
            cur_indices.append(idx)
            cur_text_parts.append(text)
            cur_len += len(text)

        flush()
        final_windows = windows[: cls.MAX_WINDOWS]
        cls._log(
            logger,
            f"  [DeepScan][2/5: Window Assembly] Assembled {len(final_windows)} processing window(s):"
        )
        for w_idx, w in enumerate(final_windows, 1):
            snippet = w["text"][:110].replace("\r", " ").replace("\n", " ").strip()
            page_str = f"page={w['page_number']}" if w["page_number"] is not None else "page=N/A"
            cls._log(
                logger,
                f"    - Window {w_idx:02d}/{len(final_windows):02d}: Chunks={w['indices']} | Section: '{w['section']}' | {page_str} | length={len(w['text'])} chars\n"
                f"        Text Preview: \"{snippet}...\""
            )
        return final_windows

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
        for w_idx, window in enumerate(windows, 1):
            cls._log(
                logger,
                f"  [DeepScan][3/5: MAP Phase] Calling LLM on Window {w_idx:02d}/{len(windows):02d} (Chunks={window['indices']}, Section='{window['section']}')..."
            )
            prompt = get_deep_scan_map_prompt(window["text"], window["section"])
            try:
                result = LLMService.generate_json(prompt)
            except Exception as e:
                cls._log(logger, f"  [DeepScan][3/5: MAP Phase] Window {w_idx} failed: {e}")
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

                item_obj = {
                    "name": name,
                    "description": (it.get("description") or name).strip(),
                    "chunk_index": window["chunk_index"],
                    "window_indices": list(window["indices"]),
                    "page_number": window["page_number"],
                    "section": section,
                    "is_pure_milestone": bool(it.get("is_pure_milestone", False)),
                    "evidence_snippet": window["text"][:180].replace("\r", " ").replace("\n", " ").strip(),
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

        cls._log(
            logger,
            f"  [DeepScan][3/5: MAP Phase] Completed. Extracted total {len(mapped)} raw candidates across all windows."
        )
        return mapped

    # ------------------------------------------------------------------ #
    # 4) REDUCE Phase with Action/Entity-Aware Protection
    # ------------------------------------------------------------------ #
    @classmethod
    def _reduce_phase(cls, mapped_items: list[dict], logger=None) -> Tuple[list[dict], list[dict]]:
        cls._log(
            logger,
            f"\n  [DeepScan][4/5: REDUCE Phase] Deduplicating & consolidating {len(mapped_items)} candidate items across windows (Section-Aware)..."
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
            f"  [DeepScan][4/5: REDUCE Phase] Grouped into {len(section_groups)} contractual section(s) for independent reduction."
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
            f"  [DeepScan][4/5: REDUCE Phase] Consolidation successful: Reduced from {len(mapped_items)} down to {len(all_reduced)} distinct candidates across all sections.\n"
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
        """
        cls._log(
            logger,
            f"\n  [DeepScan][5/5: Candidate Rehydration] Rehydrating source metadata (chunk_index, page, section) for {len(reduced_items)} candidates..."
        )
        default_ref = mapped_items[0] if mapped_items else {}
        candidates = []

        for idx, item in enumerate(reduced_items, 1):
            name = item["name"]
            src = cls._best_source(name, mapped_items) or default_ref
            section = item.get("section") or src.get("section") or "General"
            if section not in cls.VALID_SECTIONS:
                section = "General"

            raw_page = src.get("page_number")
            # Honest page reporting: only real integers from PDF, never fabricated for docx
            has_real_page = raw_page is not None and isinstance(raw_page, int) and raw_page >= 0
            page_val = raw_page if has_real_page else None
            page_locator = f"Page {raw_page}" if has_real_page else "N/A (Flow Document)"
            
            chunk_idx = src.get("chunk_index")
            source_locator = f"chunk_{chunk_idx}" if chunk_idx is not None else "chunk_N/A"

            evidence = src.get("evidence_snippet") or src.get("description") or name
            verbatim_evidence = evidence[:220].strip()

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
                "is_pure_milestone": item.get("is_pure_milestone", False) or src.get("is_pure_milestone", False),
            }
            candidates.append(candidate_dict)

            kind = "Milestone" if candidate_dict["is_pure_milestone"] else "Clause"
            cls._log(
                logger,
                f"    [{idx:02d}] [{kind}] \"{name}\" -> Section: '{section}' | {source_locator} | {page_locator}"
            )

        cls._log(
            logger,
            f"  [DeepScan][5/5: Candidate Rehydration] Successfully rehydrated {len(candidates)} canonical scope candidates.\n"
        )
        return candidates

    @staticmethod
    def _best_source(name: str, mapped_items: list[dict]) -> Optional[dict]:
        """Finds the mapped item whose name best matches `name` while checking action/entity."""
        name_l = name.lower()
        best = None
        best_ratio = 0.0
        for it in mapped_items:
            it_name_l = it["name"].lower()
            can_m, _ = ActionEntityProtector.can_merge({"name": name}, it)
            if not can_m:
                continue
            if name_l == it_name_l:
                return it
            if name_l in it_name_l or it_name_l in name_l:
                ratio = 0.90
            else:
                ratio = difflib.SequenceMatcher(None, name_l, it_name_l).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = it
        return best if best_ratio >= 0.60 else None

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
        """
        cls._log(logger, "  [DeepScan][Coverage Audit] Verifying MAP-to-REDUCE candidate coverage accounting...")

        canonical_lookup = {c["name"].lower().strip(): c for c in canonical_candidates}
        merge_lookup = {m["merged_name"].lower().strip(): m for m in merge_events}

        audit_records = []
        retained_count = 0
        merged_count = 0
        excluded_count = 0
        unresolved_count = 0

        for idx, it in enumerate(mapped_items, 1):
            raw_name = (it.get("name") or "").strip()
            raw_name_l = raw_name.lower()

            if not raw_name or len(raw_name) < 2:
                audit_records.append({
                    "map_index": idx,
                    "raw_name": raw_name,
                    "status": "EXCLUDED",
                    "canonical_name": None,
                    "section": it.get("section", "General"),
                    "rationale": "Empty or malformed candidate string",
                })
                excluded_count += 1
                continue

            # 1. Check RETAINED (exact or near-identical in canonical list)
            if raw_name_l in canonical_lookup:
                audit_records.append({
                    "map_index": idx,
                    "raw_name": raw_name,
                    "status": "RETAINED",
                    "canonical_name": canonical_lookup[raw_name_l]["name"],
                    "section": it.get("section", "General"),
                    "rationale": "Survives directly as distinct canonical candidate",
                })
                retained_count += 1
                continue

            # 2. Check documented MERGED event from deduplication
            if raw_name_l in merge_lookup:
                m_info = merge_lookup[raw_name_l]
                audit_records.append({
                    "map_index": idx,
                    "raw_name": raw_name,
                    "status": "MERGED",
                    "canonical_name": m_info["surviving_name"],
                    "section": it.get("section", "General"),
                    "rationale": m_info["rationale"],
                })
                merged_count += 1
                continue

            # 3. Fuzzy match to canonical candidate with action/entity check
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
                    "map_index": idx,
                    "raw_name": raw_name,
                    "status": "MERGED",
                    "canonical_name": best_c["name"],
                    "section": it.get("section", "General"),
                    "rationale": f"Consolidated into canonical candidate across overlapping windows (similarity: {best_sim:.2f})",
                })
                merged_count += 1
            else:
                audit_records.append({
                    "map_index": idx,
                    "raw_name": raw_name,
                    "status": "UNRESOLVED",
                    "canonical_name": None,
                    "section": it.get("section", "General"),
                    "rationale": "Candidate was not preserved and has no defensible merge mapping",
                })
                unresolved_count += 1

        total = len(mapped_items)
        accounted = retained_count + merged_count + excluded_count
        coverage_pct = round((accounted / total * 100), 2) if total else 100.0

        report = {
            "total_map_candidates": total,
            "total_canonical_candidates": len(canonical_candidates),
            "retained_count": retained_count,
            "merged_count": merged_count,
            "excluded_count": excluded_count,
            "unresolved_count": unresolved_count,
            "coverage_percentage": coverage_pct,
            "audit_records": audit_records,
        }

        cls._log(
            logger,
            f"  [DeepScan][Coverage Audit] Summary: Total MAP={total} | Canonical={len(canonical_candidates)} | "
            f"RETAINED={retained_count} | MERGED={merged_count} | EXCLUDED={excluded_count} | UNRESOLVED={unresolved_count} | "
            f"Coverage={coverage_pct}%"
        )
        if unresolved_count > 0:
            cls._log(logger, f"  [WARNING][Coverage Audit] {unresolved_count} MAP candidates were UNRESOLVED!")

        return report
