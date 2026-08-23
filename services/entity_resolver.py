"""
EntityResolver — Unified Canonical Entity Resolution Service

This service is the single authority for resolving any text reference
(activity name, blocked_by entry, expected_unlock, etc.) into a
canonical entity with a stable canonical_id.

Resolution order (deterministic):
  1. Exact canonical_id
  2. Exact normalized canonical name
  3. Exact alias
  4. Normalized alias
  5. Strong lexical match (token overlap / Jaccard ≥ 0.6)
  6. Substring containment match
  7. Contextual match against EL baseline
  8. UNRESOLVED

Rules:
- Never create a new graph node from an unresolved reference.
- Never compare dependency strings directly against display names without normalization.
- One immutable canonical_id per project entity.
- All entity references in the pipeline must go through this resolver.
"""

import re
import unicodedata
from typing import Optional, Dict, List, Any


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s]")
_MULTI_SPACE = re.compile(r"\s+")

def normalize_entity_name(text: str) -> str:
    """
    Normalise a name for matching purposes.

    Handles:
    - Unicode → ASCII
    - Lowercase
    - Punctuation removal
    - Duplicate whitespace collapse
    - Leading/trailing whitespace

    Does NOT:
    - Remove meaningful words (e.g. 'Production', 'API', 'Credentials')
    - Collapse multi-word entities to single tokens
    """
    if not text:
        return ""
    # Normalise unicode
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = _PUNCT.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text


def _strip_parentheticals(name: str) -> Optional[str]:
    """
    Strips parenthetical suffixes from dependency names.
    Handles common patterns like:
      "User Acceptance Testing (UAT)" → "User Acceptance Testing"
      "System Integration Testing (SIT)" → "System Integration Testing"
      "API Gateway (v2)" → "API Gateway"

    Generic: uses only regex, no hardcoded names.
    Returns the stripped name for secondary resolution attempt.
    """
    if not name:
        return None
    # Remove trailing parenthetical expressions
    stripped = re.sub(r'\s*\([^)]+\)\s*$', '', name).strip()
    # Also handle multiple parens: "Name (abbr) (v2)" → "Name"
    while re.search(r'\([^)]+\)$', stripped):
        stripped = re.sub(r'\s*\([^)]+\)\s*$', '', stripped).strip()
    return stripped if stripped != name else None


def _token_overlap(a: str, b: str) -> float:
    """Jaccard similarity on word tokens."""
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


# ---------------------------------------------------------------------------
# Canonical Entity
# ---------------------------------------------------------------------------

class CanonicalEntity:
    """
    Represents one immutable project entity.

    canonical_id  — stable identifier (e.g. 'si_480')
    display_name  — original full name from EL baseline
    aliases       — additional known names (auto-generated + extracted)
    entity_type   — MILESTONE | ACTION_ITEM | DEPENDENCY | EXTERNAL | etc.
    """

    def __init__(self, canonical_id: str, display_name: str, entity_type: str = "MILESTONE",
                 aliases: List[str] = None, baseline_id: str = None):
        self.canonical_id = canonical_id
        self.baseline_id = baseline_id
        self.display_name = display_name
        self.entity_type = entity_type
        self.aliases: List[str] = list(aliases or [])
        self._norm_name = normalize_entity_name(display_name)
        self._norm_aliases = [normalize_entity_name(a) for a in self.aliases]

    def add_alias(self, alias: str):
        norm = normalize_entity_name(alias)
        if norm and norm not in self._norm_aliases and norm != self._norm_name:
            self.aliases.append(alias)
            self._norm_aliases.append(norm)

    def __repr__(self):
        return f"CanonicalEntity({self.canonical_id!r}, {self.display_name!r})"


# ---------------------------------------------------------------------------
# Unresolved reference DTO
# ---------------------------------------------------------------------------

class UnresolvedReference:
    """
    Represents a dependency reference that could not be resolved to any
    known canonical entity.

    type:
      UNRESOLVED_EXTERNAL_DEPENDENCY — plausibly real but not in EL
      NON_ENTITY_TEXT                — status text, role names, etc.
    """

    EXTERNAL = "UNRESOLVED_EXTERNAL_DEPENDENCY"
    NON_ENTITY = "NON_ENTITY_TEXT"

    def __init__(self, raw: str, ref_type: str, source_id: str = None,
                 evidence: str = None):
        self.raw = raw
        self.ref_type = ref_type
        self.source_id = source_id
        self.evidence = evidence

    def to_dict(self) -> dict:
        return {
            "type": self.ref_type,
            "label": self.raw,
            "source_entity_id": self.source_id,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

class ResolutionResult:
    def __init__(self, resolved: bool, entity: CanonicalEntity = None,
                 raw: str = "", confidence: float = 0.0,
                 match_type: str = "none", reason: str = ""):
        self.resolved = resolved
        self.entity = entity
        self.raw = raw
        self.confidence = confidence
        self.match_type = match_type
        self.reason = reason

    @property
    def canonical_id(self) -> Optional[str]:
        return self.entity.canonical_id if self.entity else None

    def to_log_dict(self) -> dict:
        return {
            "raw": self.raw,
            "resolved": self.resolved,
            "canonical_id": self.canonical_id,
            "display_name": self.entity.display_name if self.entity else None,
            "confidence": round(self.confidence, 3),
            "match_type": self.match_type,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Canonical Entity Registry
# ---------------------------------------------------------------------------

class CanonicalEntityRegistry:
    """
    Holds ALL canonical entities for a single project.

    Built in Pass 1 (before any dependency resolution).
    Immutable after build — no new nodes should be added during
    dependency resolution (Pass 2).
    """

    def __init__(self):
        self._by_id: Dict[str, CanonicalEntity] = {}
        self._by_norm_name: Dict[str, CanonicalEntity] = {}
        self._by_norm_alias: Dict[str, List[CanonicalEntity]] = {}

    def register(self, entity: CanonicalEntity):
        self._by_id[entity.canonical_id] = entity
        self._by_norm_name[entity._norm_name] = entity
        for norm_alias in entity._norm_aliases:
            if norm_alias not in self._by_norm_alias:
                self._by_norm_alias[norm_alias] = []
            self._by_norm_alias[norm_alias].append(entity)

    def get_by_id(self, cid: str) -> Optional[CanonicalEntity]:
        return self._by_id.get(cid)

    def all_entities(self) -> List[CanonicalEntity]:
        return list(self._by_id.values())

    def print_registry(self):
        print("\n=== CANONICAL ENTITY REGISTRY ===")
        print(f"{'ID':<12} | {'Canonical Name':<55} | {'Type':<20} | Aliases")
        print("-" * 120)
        for e in sorted(self._by_id.values(), key=lambda x: x.canonical_id):
            aliases = ", ".join(e.aliases[:4]) if e.aliases else "-"
            print(f"{e.canonical_id:<12} | {e.display_name:<55} | {e.entity_type:<20} | {aliases}")
        print()


# ---------------------------------------------------------------------------
# Non-entity text blocklist (status words, roles, generic nouns)
# ---------------------------------------------------------------------------

_NON_ENTITY_WORDS = {
    # Statuses
    "pending", "pending review", "waiting", "completed", "not started",
    "in progress", "unknown", "delayed", "blocked", "cancelled",
    "done", "resolved", "on hold", "deferred", "planned", "open",
    # Roles / owners / teams
    "customer", "client", "internal", "external", "vendor",
    "third party", "third-party", "development team", "qa team",
    "qa lead", "project manager", "customer team", "sponsor",
    "management", "stakeholder", "pmo", "team", "department", "role",
    "customer department", "vendor team",
    # Generic resources & nouns (not canonical activities by themselves)
    "credentials", "access", "approval", "security approval",
    "internal security approval", "review", "next weekly meeting",
    "meeting", "discussion", "follow up", "follow-up", "tbd", "n/a",
    "na", "none", "null", "undefined", "yes", "no", "percentage",
    "next week", "september 9",
}

_DATE_RE = re.compile(
    r"^\d{1,2}\s+\w+(\s+\d{4})?$"          # "09 Sep 2026"
    r"|^\w+\s+\d{1,2}(,?\s+\d{4})?$"       # "September 9, 2026"
    r"|^\d{4}-\d{2}-\d{2}$"                # ISO date
    r"|^Q[1-4]\s+\d{4}$",                  # "Q3 2026"
    re.IGNORECASE,
)


def _is_non_entity(text: str) -> bool:
    n = normalize_entity_name(text)
    if n in _NON_ENTITY_WORDS:
        return True
    if _DATE_RE.match(n):
        return True
    if len(n) <= 2:
        return True
    # Single generic word not matching anything
    if len(n.split()) == 1 and not text.isupper():
        return True
    return False


# ---------------------------------------------------------------------------
# EntityResolver
# ---------------------------------------------------------------------------

class EntityResolver:
    """
    Single shared resolver for all entity references in the pipeline.

    Usage:
        resolver = EntityResolver(registry)
        result = resolver.resolve("CRM Integration", source_id="si_481")
        if result.resolved:
            edge = DependencyEdge(source_id, result.canonical_id, ...)
        else:
            log_unresolved(result)
    """

    LEXICAL_THRESHOLD = 0.75    # Minimum Jaccard for a "strong" lexical match (raised from 0.55)
    SUBSTRING_THRESHOLD = 0.60   # Minimum length ratio for substring match (raised from 0.40)

    def __init__(self, registry: CanonicalEntityRegistry):
        self._registry = registry
        self._log: List[dict] = []   # Resolution log for diagnostics

    def resolve(self, reference: str, source_id: str = None,
                evidence: str = None) -> ResolutionResult:
        """
        Resolve a raw text reference to a CanonicalEntity.

        Steps (in order):
          1. Exact canonical_id
          2. Exact normalized canonical name
          3. Exact normalized alias
          4. Normalized alias substring / Jaccard
          5. Strong lexical match against all canonical names
          6. Substring containment match
          7. Contextual match (partial token sets)
          8. Unresolved
        """
        if not reference or not reference.strip():
            r = ResolutionResult(False, raw=reference or "",
                                 reason="Empty reference")
            self._log.append(r.to_log_dict())
            return r

        raw = reference.strip()
        norm_ref = normalize_entity_name(raw)

        # ── Step 1: Exact canonical_id ──────────────────────────────────────
        if raw in self._registry._by_id:
            e = self._registry._by_id[raw]
            r = ResolutionResult(True, e, raw, 1.0, "exact_id")
            self._log.append(r.to_log_dict())
            return r

        # ── Step 2: Exact normalized canonical name ─────────────────────────
        if norm_ref in self._registry._by_norm_name:
            e = self._registry._by_norm_name[norm_ref]
            r = ResolutionResult(True, e, raw, 1.0, "exact_norm_name")
            self._log.append(r.to_log_dict())
            return r

        # ── Step 3: Exact normalized alias ──────────────────────────────────
        if norm_ref in self._registry._by_norm_alias:
            candidates = self._registry._by_norm_alias[norm_ref]
            e = candidates[0]
            r = ResolutionResult(True, e, raw, 0.98, "exact_alias")
            self._log.append(r.to_log_dict())
            return r

        # ── Step 3.1 (Tier 0.5): Strip parentheticals and retry exact matches ──
        # PARENTHETICAL FIX: Strip trailing parentheticals and retry exact name / alias matches
        stripped = _strip_parentheticals(raw)
        if stripped:
            norm_stripped = normalize_entity_name(stripped)
            # Retry exact canonical name with stripped name
            if norm_stripped in self._registry._by_norm_name:
                e = self._registry._by_norm_name[norm_stripped]
                print(f"  [GraphBuilder] Parenthetical strip resolved: "
                      f"'{raw}' -> '{stripped}' ({e.canonical_id}, confidence: 0.95)")
                r = ResolutionResult(True, e, raw, 0.95, "parenthetical_strip")
                self._log.append(r.to_log_dict())
                return r
            # Retry exact alias with stripped name
            if norm_stripped in self._registry._by_norm_alias:
                candidates = self._registry._by_norm_alias[norm_stripped]
                e = candidates[0]
                print(f"  [GraphBuilder] Parenthetical strip alias resolved: "
                      f"'{raw}' -> '{stripped}' ({e.canonical_id}, confidence: 0.93)")
                r = ResolutionResult(True, e, raw, 0.93, "parenthetical_strip_alias")
                self._log.append(r.to_log_dict())
                return r

        # ── Step 3.5: Prefix / Head phrase match ────────────────────────────
        # Only resolves if the reference is a significant leading phrase of the
        # entity name (e.g. "CRM Integration" -> "CRM Integration for customer...")
        # GUARD: do NOT resolve if entity name is a superset of the reference
        # (prevents "security review" -> "audit logs security review" superset match)
        prefix_candidates = []
        for entity in self._registry.all_entities():
            entity_norm = entity._norm_name
            # Reference is a prefix of the entity name
            if entity_norm.startswith(norm_ref) and len(norm_ref) >= 4:
                shared = set(norm_ref.split()) & set(entity_norm.split())
                len_ratio = len(norm_ref) / len(entity_norm) if entity_norm else 0
                # Only if reference covers >=50% of entity name (not too short a prefix)
                if len(shared) >= 2 and len_ratio >= 0.40:
                    prefix_candidates.append((entity, len_ratio))

        # Ambiguity check: if multiple prefix matches, do not auto-resolve
        if len(prefix_candidates) == 1:
            entity, conf = prefix_candidates[0]
            r = ResolutionResult(True, entity, raw, round(0.80 + conf * 0.15, 3),
                                 "prefix_match")
            self._log.append(r.to_log_dict())
            return r
        elif len(prefix_candidates) > 1:
            # Ambiguous — return unresolved rather than guessing
            r = ResolutionResult(False, raw=raw,
                                 reason=f"AMBIGUOUS_PREFIX: {len(prefix_candidates)} matches")
            self._log.append(r.to_log_dict())
            return r

        # ── Steps 4–7: Fuzzy / partial matching with ambiguity detection ─────
        # Collect ALL candidates above threshold, check for ambiguity
        match_candidates = []  # List of (entity, score, match_type)

        for entity in self._registry.all_entities():
            # Step 4: Normalized alias fuzzy
            for norm_alias in entity._norm_aliases:
                j = _token_overlap(norm_ref, norm_alias)
                if j >= self.LEXICAL_THRESHOLD:
                    match_candidates.append((entity, j, "alias_lexical"))
                    break  # best alias for this entity

            # Step 5: Strong lexical match against canonical name
            j = _token_overlap(norm_ref, entity._norm_name)
            if j >= self.LEXICAL_THRESHOLD:
                match_candidates.append((entity, j, "lexical"))

            # Step 6: Substring containment — with superset guard
            if len(norm_ref) >= 5:
                # norm_ref is contained IN entity name
                if norm_ref in entity._norm_name:
                    len_ratio = len(norm_ref) / len(entity._norm_name)
                    # Guard: reject if entity is a superset (entity much longer than ref)
                    # e.g. "security review" -> "audit logs security review" rejected
                    if len_ratio >= self.SUBSTRING_THRESHOLD:
                        match_candidates.append((entity, len_ratio, "substring"))
                # entity name is contained IN norm_ref
                elif entity._norm_name in norm_ref:
                    len_ratio = len(entity._norm_name) / len(norm_ref)
                    if len_ratio >= self.SUBSTRING_THRESHOLD:
                        match_candidates.append((entity, len_ratio, "substring"))

            # Step 7: Contextual partial token match (min 2 shared tokens)
            ref_tokens = set(norm_ref.split())
            name_tokens = set(entity._norm_name.split())
            shared = ref_tokens & name_tokens
            if len(shared) >= 2:
                ctx_score = len(shared) / max(len(ref_tokens), len(name_tokens))
                if ctx_score >= self.LEXICAL_THRESHOLD:
                    match_candidates.append((entity, ctx_score, "contextual"))

        # Deduplicate candidates by entity (keep highest score per entity)
        best_per_entity: Dict[str, tuple] = {}
        for (entity, score, mtype) in match_candidates:
            cid = entity.canonical_id
            if cid not in best_per_entity or score > best_per_entity[cid][1]:
                best_per_entity[cid] = (entity, score, mtype)

        unique_candidates = list(best_per_entity.values())

        # Ambiguity check: if >1 entity qualifies, do NOT auto-resolve
        if len(unique_candidates) > 1:
            r = ResolutionResult(False, raw=raw,
                                 reason=f"AMBIGUOUS_MATCH: {len(unique_candidates)} entities qualify")
            self._log.append(r.to_log_dict())
            return r

        if unique_candidates:
            best_entity, best_score, best_type = unique_candidates[0]
            r = ResolutionResult(True, best_entity, raw, best_score, best_type)
            self._log.append(r.to_log_dict())
            return r

        # ── Step 8: Unresolved ───────────────────────────────────────────────
        r = ResolutionResult(False, raw=raw,
                             reason="No canonical entity match found")
        self._log.append(r.to_log_dict())
        return r

    def classify_unresolved(self, raw: str, source_id: str = None,
                             evidence: str = None) -> UnresolvedReference:
        """
        Classify a raw reference that failed resolution into:
          - UNRESOLVED_EXTERNAL_DEPENDENCY (plausibly real project entity)
          - NON_ENTITY_TEXT (status, role, generic noun)
        """
        if _is_non_entity(raw):
            return UnresolvedReference(raw, UnresolvedReference.NON_ENTITY,
                                       source_id, evidence)
        return UnresolvedReference(raw, UnresolvedReference.EXTERNAL,
                                   source_id, evidence)

    def print_resolution_log(self, source_label: str = ""):
        if not self._log:
            return
        label = f" [{source_label}]" if source_label else ""
        print(f"\n=== DEPENDENCY RESOLUTION{label} ===")
        print(f"{'Source (raw)':<45} | {'Resolved Name':<50} | {'ID':<12} | {'Conf':>5} | {'Match':<18} | Result")
        print("-" * 160)
        for entry in self._log:
            status = "RESOLVED" if entry["resolved"] else "UNRESOLVED"
            name = (entry.get("display_name") or "-")[:50]
            cid = (entry.get("canonical_id") or "-")[:12]
            raw = (entry.get("raw") or "-")[:45]
            conf = f"{entry['confidence']:.2f}" if entry["confidence"] else "0.00"
            mt = entry.get("match_type", "-")[:18]
            print(f"{raw:<45} | {name:<50} | {cid:<12} | {conf:>5} | {mt:<18} | {status}")
        print()

    def clear_log(self):
        self._log.clear()


# ---------------------------------------------------------------------------
# Registry builder helpers
# ---------------------------------------------------------------------------

def build_registry_from_baseline(baseline_items: List[Dict[str, Any]],
                                  id_prefix: str = "si") -> CanonicalEntityRegistry:
    """
    Build a CanonicalEntityRegistry from EL baseline items.

    Each baseline item dict is expected to have at minimum:
        { "id": <int>, "name": <str>, ... }

    Auto-generates acronym aliases (e.g. "System Integration Testing" → "sit").
    """
    registry = CanonicalEntityRegistry()
    for item in baseline_items:
        raw_id = item.get("id")
        name = (item.get("name") or "").strip()
        if not name:
            continue

        canonical_id = f"{id_prefix}_{raw_id}" if raw_id else f"{id_prefix}_{normalize_entity_name(name)[:20]}"
        entity_type = str(item.get("category") or item.get("type") or "MILESTONE").upper()

        entity = CanonicalEntity(canonical_id, name, entity_type)

        # Auto-generate acronym alias from words
        words = name.split()
        if len(words) > 1:
            acronym = "".join(w[0] for w in words if w[0].isupper())
            if len(acronym) >= 2:
                entity.add_alias(acronym)
            acronym_lower = "".join(w[0].lower() for w in words)
            if len(acronym_lower) >= 2:
                entity.add_alias(acronym_lower)

        # Auto-generate core prefix alias before " for ", " - ", " – ", " ("
        for separator in [" for ", " - ", " – ", " ("]:
            if separator in name:
                core_part = name.split(separator)[0].strip()
                if len(core_part) >= 3:
                    entity.add_alias(core_part)

        # Parentheses contents (e.g. "(SSO)")
        match_paren = re.search(r'\(([^)]+)\)', name)
        if match_paren:
            inside = match_paren.group(1).strip()
            if len(inside) >= 2:
                entity.add_alias(inside)

        # Normalized scope_item_normalized alias if present
        norm_val = item.get("scope_item_normalized", "")
        if norm_val and norm_val.strip() and norm_val.strip().lower() != name.lower():
            entity.add_alias(norm_val.strip())

        # Compound item support (e.g. "System Integration Testing (SIT), UAT, Production Deployment")
        if "," in name:
            for part in name.split(","):
                clean_part = part.strip()
                if len(clean_part) >= 3:
                    entity.add_alias(clean_part)

        registry.register(entity)

    return registry


def enrich_registry_with_candidates(registry: CanonicalEntityRegistry,
                                     candidates: List[Dict[str, Any]],
                                     id_prefix: str = "cand") -> CanonicalEntityRegistry:
    """
    Pass 1 (second phase): Add extracted LLM activities that are NOT already
    in the EL baseline into the registry.

    Every candidate is registered as a unique execution node (cand_X).
    If it resolves to a baseline item, we store that as its `baseline_id`.
    """
    resolver = EntityResolver(registry)
    for i, cand in enumerate(candidates):
        raw_name = (cand.get("activity") or cand.get("canonical_title") or "").strip()
        if not raw_name:
            continue

        cid = f"{id_prefix}_{i}"
        
        # Check if it maps to an existing baseline entity
        result = resolver.resolve(raw_name)
        baseline_id = result.entity.canonical_id if result.resolved else None
        
        # Create unique execution node for this candidate
        entity = CanonicalEntity(cid, raw_name, "ACTIVITY", baseline_id=baseline_id)
        
        if result.resolved:
            # Let the baseline entity know about this alias as well
            result.entity.add_alias(raw_name)

        registry.register(entity)
        cand["_canonical_id"] = cid
        cand["_baseline_id"] = baseline_id
        cand["_canonical_entity"] = entity

    return registry
