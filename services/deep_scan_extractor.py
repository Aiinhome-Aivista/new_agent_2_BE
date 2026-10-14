"""
Deep Scan (Map-Reduce + Vector Heatmap) scope candidate extractor.

This service is an ALTERNATIVE to the deterministic `ScopeCandidateExtractor`.
It is selected when the user picks the "Deep Scan" extraction mode in the UI.

Strategy
--------
1. HEATMAP: Instead of regex, query the Vector DB (ChromaDB) with a set of
   scope-related keywords ("Scope of Work", "Deliverables", "Assumptions", ...)
   to locate the "hot" chunks/pages of the current EL/IFA document.
2. MAP: Group the hot chunks into windows and ask the LLM to extract every
   distinct scope-related item from each window.
3. REDUCE: Ask the LLM to merge/deduplicate the mapped items into a single,
   consolidated list.
4. FALLBACK: If the heatmap finds nothing (e.g. Vector DB empty), fall back to
   chunking + mapping the ENTIRE document.

CONTRACT (very important)
-------------------------
`extract_candidates()` returns a list of candidate dicts in the *exact* same
shape produced by `ScopeCandidateExtractor.extract_candidates()`, so the rest
of the baseline pipeline (ScopeClassifier -> ScopeDeduplicator ->
MilestoneDeadlineExtractor -> ...) keeps working unchanged:

    {
        "name": str,
        "description": str,
        "page_number": int | None,
        "section": str,
        "chunk_index": int | None,
        "raw_text": str,
        "document_id": int,
    }
"""

import difflib

from services.embedding_service import EmbeddingService
from services.chroma_service import ChromaService
from services.llm_service import LLMService
from core.prompts import get_deep_scan_map_prompt, get_deep_scan_reduce_prompt


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
        # New keywords for narrative ELs (demo_EL type)
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

    # Sections the LLM is allowed to attribute an item to (matches
    # ScopeSectionDetector's vocabulary so downstream filtering behaves).
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
        # New sections for narrative ELs
        "Functional Scope",
        "Methodology",
        "Governance",
        "Support",
        "Acceptance",
        "Resources",
    }

    # How many hits to pull per keyword from Chroma.
    HEATMAP_TOP_K = 8
    # Approx. character budget per Map window (keeps LLM context small).
    WINDOW_CHAR_BUDGET = 3500
    # Safety cap on the number of Map windows we will process.
    MAX_WINDOWS = 60
    # Batch size for the Reduce phase.
    REDUCE_BATCH_SIZE = 50

    @staticmethod
    def _log(logger, message: str):
        if logger and hasattr(logger, "raw_log"):
            logger.raw_log(message)
        else:
            print(message)

    # ------------------------------------------------------------------ #
    # Public API
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
        Main entry point. Returns candidates in ScopeCandidateExtractor format.
        """
        if not parsed_chunks:
            cls._log(logger, "[DeepScan] No parsed chunks provided; returning [].")
            return []

        # Map chunk_index -> full chunk text & page number (source of truth for text).
        index_to_chunk = {}
        for ch in parsed_chunks:
            idx = ch.get("chunk_index")
            if idx is None:
                continue
            index_to_chunk[idx] = {
                "text": ch.get("text", "") or "",
                "page_number": ch.get("page_number"),
            }

        # Map chunk_index -> detected section (best-effort; used to tag items).
        index_to_section = {}
        for ch in (sectioned_chunks or []):
            idx = ch.get("chunk_index")
            sec = ch.get("section")
            if idx is not None and sec and idx not in index_to_section:
                index_to_section[idx] = sec

        # 1) Build the heatmap of "hot" chunk indices via the Vector DB.
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
            return []

        # 2) Assemble Map windows from the hot chunks.
        windows = cls._build_windows(hot_indices, index_to_chunk, index_to_section, logger=logger)
        if not windows:
            return []

        # 3) MAP: extract raw items from each window.
        mapped_items = cls._map_phase(windows, logger=logger)
        if not mapped_items:
            cls._log(logger, "[DeepScan] Map phase yielded 0 items; returning [].")
            return []

        # 4) REDUCE: consolidate/deduplicate mapped items via the LLM.
        reduced_items = cls._reduce_phase(mapped_items, logger=logger)

        # 5) Convert to the canonical candidate dict format, restoring source
        #    references (chunk_index/page/section) from the mapped items.
        candidates = cls._to_candidates(reduced_items, mapped_items, document_id, logger=logger)

        cls._log(
            logger,
            f"\n  [DeepScan] Completed "
            f"({'fallback/full-doc' if used_fallback else 'heatmap'}): "
            f"{len(candidates)} candidates from {len(windows)} windows."
        )
        return candidates

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
        """
        Query ChromaDB with each keyword, collect the chunk_indices belonging to
        THIS document, then expand to any sibling chunks on the same page.
        """
        cls._log(
            logger,
            f"\n  [DeepScan][1/5: Vector Heatmap] Querying ChromaDB with {len(cls.HEATMAP_KEYWORDS)} scope keywords (top_k={cls.HEATMAP_TOP_K})..."
        )
        doc_types = ["EL", "IFA"]

        hot = set()
        hot_pages = set()

        for keyword in cls.HEATMAP_KEYWORDS:
            try:
                embedding = EmbeddingService.encode(keyword)
                results = ChromaService.search(
                    project_id=project_id,
                    query_embedding=embedding,
                    document_types=doc_types,
                    top_k=cls.HEATMAP_TOP_K,
                )
            except Exception as e:
                cls._log(logger, f"  [DeepScan][1/5: Vector Heatmap] Query failed for '{keyword}': {e}")
                continue

            metadatas = (results or {}).get("metadatas") or [[]]
            metadatas = metadatas[0] if metadatas else []
            for meta in metadatas:
                meta = meta or {}
                if meta.get("document_id") != document_id:
                    continue
                c_idx = meta.get("chunk_index")
                if c_idx in index_to_chunk:
                    hot.add(c_idx)
                page = meta.get("page_number")
                if isinstance(page, int) and page > 0:
                    hot_pages.add(page)

        # Expand hot pages -> include every sibling chunk on those pages so the
        # LLM gets the *full page* context (as opposed to a single retrieved snippet).
        if hot_pages:
            for c_idx, ch in index_to_chunk.items():
                if ch.get("page_number") in hot_pages:
                    hot.add(c_idx)

        cls._log(
            logger,
            f"  [DeepScan][1/5: Vector Heatmap] ChromaDB query finished: Found {len(hot)} hot chunks across page(s) {sorted(list(hot_pages)) or ['N/A']}."
        )
        return sorted(hot)

    # ------------------------------------------------------------------ #
    # 2) Windowing
    # ------------------------------------------------------------------ #
    @classmethod
    def _build_windows(
        cls,
        hot_indices: list[int],
        index_to_chunk: dict,
        index_to_section: dict,
        logger=None,
    ) -> list[dict]:
        """
        Group consecutive hot chunks into windows under WINDOW_CHAR_BUDGET.
        Each window carries a representative chunk_index/page/section used later
        to re-attach source references to extracted items.
        """
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
            # Representative page: first non-null page among the window's chunks.
            rep_page = None
            for i in cur_indices:
                p = index_to_chunk.get(i, {}).get("page_number")
                if p is not None:
                    rep_page = p
                    break
            # Representative section: first detected non-"General" section, else General.
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
            # If adding this chunk would exceed the budget, close the window first.
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
            page_str = f"page={w['page_number']}" if w['page_number'] else "page=N/A"
            cls._log(
                logger,
                f"    - Window {w_idx:02d}/{len(final_windows):02d}: Chunks={w['indices']} | Section: '{w['section']}' | {page_str} | length={len(w['text'])} chars\n"
                f"        Text Preview: \"{snippet}...\""
            )
        return final_windows

    # ------------------------------------------------------------------ #
    # 3) Map
    # ------------------------------------------------------------------ #
    @classmethod
    def _map_phase(cls, windows: list[dict], logger=None) -> list[dict]:
        """
        Run the LLM Map prompt over each window. Each returned item is annotated
        with the window's source references so we can restore them after Reduce.
        """
        cls._log(
            logger,
            f"\n  [DeepScan][3/5: MAP Phase] Executing LLM Map Extraction across {len(windows)} window(s)..."
        )
        mapped = []
        for w_num, window in enumerate(windows, 1):
            if not window["text"].strip():
                continue
            cls._log(
                logger,
                f"  [DeepScan][3/5: MAP Phase] Calling LLM on Window {w_num:02d}/{len(windows):02d} (Chunks={window['indices']}, Section='{window['section']}')..."
            )
            prompt = get_deep_scan_map_prompt(window["text"])
            try:
                result = LLMService.generate_json(prompt)
            except Exception as e:
                cls._log(logger, f"  [DeepScan][3/5: MAP Phase] LLM failed for window {w_num}: {e}")
                continue

            items = result if isinstance(result, list) else [result]
            window_extracted_items = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                description = (it.get("description") or name).strip()
                section = it.get("source_section") or window["section"]
                if section not in cls.VALID_SECTIONS:
                    section = window["section"] if window["section"] in cls.VALID_SECTIONS else "General"
                item_dict = {
                    "name": name,
                    "description": description,
                    "section": section,
                    "chunk_index": window["chunk_index"],
                    "page_number": window["page_number"],
                    "is_pure_milestone": it.get("is_pure_milestone", False),
                }
                mapped.append(item_dict)
                window_extracted_items.append(item_dict)

            cls._log(
                logger,
                f"  [DeepScan][3/5: MAP Phase] Window {w_num:02d} extracted {len(window_extracted_items)} candidate(s):"
            )
            for it in window_extracted_items:
                kind = "Milestone" if it["is_pure_milestone"] else "Clause"
                cls._log(logger, f"      * [{kind}] \"{it['name']}\" (Section: '{it['section']}')")

        cls._log(
            logger,
            f"  [DeepScan][3/5: MAP Phase] Completed. Extracted total {len(mapped)} raw candidates across all windows."
        )
        return mapped

    # ------------------------------------------------------------------ #
    # 4) Reduce
    # ------------------------------------------------------------------ #
    @classmethod
    def _reduce_phase(cls, mapped_items: list[dict], logger=None) -> list[dict]:
        """
        Deduplicate/merge mapped items via the LLM. Batches large inputs and runs
        a final consolidation pass. Falls back to deterministic dedup on failure.
        """
        cls._log(
            logger,
            f"\n  [DeepScan][4/5: REDUCE Phase] Deduplicating & consolidating {len(mapped_items)} candidate items across windows..."
        )
        # Deterministic fallback list (used if the LLM reduce fails entirely).
        det_dedup = cls._deterministic_dedup(mapped_items)

        # Build the minimal payload the reduce prompt needs.
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

        try:
            working = mapped_items
            # If very large, reduce in batches first, then a final pass.
            if len(working) > cls.REDUCE_BATCH_SIZE:
                cls._log(
                    logger,
                    f"  [DeepScan][4/5: REDUCE Phase] Input exceeds batch size ({cls.REDUCE_BATCH_SIZE}); reducing in multiple batches..."
                )
                intermediate = []
                for i in range(0, len(working), cls.REDUCE_BATCH_SIZE):
                    batch = working[i : i + cls.REDUCE_BATCH_SIZE]
                    intermediate.extend(cls._reduce_once(to_payload(batch), logger=logger))
                working_payload = to_payload(intermediate) if intermediate else to_payload(working)
            else:
                working_payload = to_payload(working)

            cls._log(
                logger,
                f"  [DeepScan][4/5: REDUCE Phase] Sending {len(working_payload)} candidate items to LLM Reduce prompt..."
            )
            reduced = cls._reduce_once(working_payload, logger=logger)
            if reduced:
                cls._log(
                    logger,
                    f"  [DeepScan][4/5: REDUCE Phase] Consolidation successful: Reduced from {len(mapped_items)} down to {len(reduced)} distinct candidates."
                )
                return reduced
        except Exception as e:
            cls._log(logger, f"  [DeepScan][4/5: REDUCE Phase] LLM reduce failed, using deterministic dedup: {e}")

        cls._log(
            logger,
            f"  [DeepScan][4/5: REDUCE Phase] Applied deterministic deduplication: {len(det_dedup)} distinct candidates."
        )
        return det_dedup

    @classmethod
    def _reduce_once(cls, items_payload: list[dict], logger=None) -> list[dict]:
        """Single LLM reduce call. Returns list of {name, description, source_section}."""
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
                    "is_pure_milestone": it.get("is_pure_milestone", False),
                }
            )
        return cleaned

    @classmethod
    def _deterministic_dedup(cls, mapped_items: list[dict]) -> list[dict]:
        """Simple fuzzy dedup fallback (mirrors ScopeDeduplicator's intent)."""
        result = []
        for it in mapped_items:
            match = None
            for existing in result:
                ratio = difflib.SequenceMatcher(
                    None, it["name"].lower(), existing["name"].lower()
                ).ratio()
                if (
                    ratio > 0.80
                    or it["name"].lower() in existing["name"].lower()
                    or existing["name"].lower() in it["name"].lower()
                ):
                    match = existing
                    break
            if match:
                if it["description"] and it["description"] not in match["description"]:
                    match["description"] += f" | {it['description']}"
            else:
                result.append(
                    {
                        "name": it["name"],
                        "description": it["description"],
                        "section": it["section"],
                        "is_pure_milestone": it.get("is_pure_milestone", False),
                    }
                )
        return result

    # ------------------------------------------------------------------ #
    # 5) Convert to canonical candidate format
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
        Rehydrate source references (chunk_index/page_number) onto the reduced
        items by matching them back to the best mapped item, then emit dicts in
        the exact ScopeCandidateExtractor shape.
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

            candidate_dict = {
                "name": name,
                "description": item.get("description", name),
                "page_number": src.get("page_number"),
                "section": section,
                "chunk_index": src.get("chunk_index"),
                "raw_text": item.get("description", name),
                "document_id": document_id,
                "is_pure_milestone": item.get("is_pure_milestone", False) or src.get("is_pure_milestone", False),
            }
            candidates.append(candidate_dict)

            kind = "Milestone" if candidate_dict["is_pure_milestone"] else "Clause"
            page_val = src.get('page_number') if src.get('page_number') is not None else "N/A"
            cls._log(
                logger,
                f"    [{idx:02d}] [{kind}] \"{name}\" -> Section: '{section}' | chunk_index={src.get('chunk_index')} | page={page_val}"
            )

        cls._log(
            logger,
            f"  [DeepScan][5/5: Candidate Rehydration] Successfully rehydrated {len(candidates)} canonical scope candidates for Step 4 classification.\n"
        )
        return candidates

    @staticmethod
    def _best_source(name: str, mapped_items: list[dict]) -> dict | None:
        """Find the mapped item whose name best matches `name`."""
        name_l = name.lower()
        best = None
        best_ratio = 0.0
        for it in mapped_items:
            it_name_l = it["name"].lower()
            if name_l in it_name_l or it_name_l in name_l:
                return it
            ratio = difflib.SequenceMatcher(None, name_l, it_name_l).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = it
        if best_ratio >= 0.6:
            return best
        return None
