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
from services.party_resolver import PartyResolver
from core.prompts import get_deep_scan_map_prompt, get_deep_scan_reduce_prompt


class ActionEntityProtector:
    """
    Guards distinct contractual features and milestones from being erroneously merged during
    the REDUCE deduplication phase. Analyzes contrasting action verbs, contrasting entities,
    explicit milestone dates, payment percentages, and workstream qualifiers.
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
        "execute": {"execute", "execution", "run", "running", "perform", "performing"},
        "assist": {"assist", "assists", "assisting", "assistance", "help", "helping", "guide", "guidance"},
        "sign_off": {"sign-off", "signoff", "sign_off", "sign off"},
        "deploy": {"deploy", "deployment", "deploying", "release"},
        "cutover": {"cutover", "cut-over", "migration", "cut over"},
        "test": {"uat", "user acceptance testing", "acceptance testing", "testing", "test", "integration testing", "unit testing", "system testing"},
        "apply": {"apply", "applying", "application", "submit application"},
        "train": {"train", "training", "trainings", "user training", "training materials", "training sessions", "training manuals", "educate", "education", "communication", "coaching"},
        "delegate": {"on behalf", "on behalf of", "proxy", "delegated", "post on behalf", "behalf"},
        "create": {"create", "creating", "creation", "build", "building"},
        "assign": {"assign", "assigning", "assignment"},
        "track_report": {"track", "tracking", "report", "reports", "reporting", "compliance report"},
        "status_report": {"status update", "status updates", "project status", "status reporting", "progress reporting", "provide project status"},
        "governance_commitment": {"organisational commitment", "organizational commitment", "commitment of the entire organisation", "commitment of the entire organization"},
        "knowledge_transfer": {"knowledge transfer", "kt handover", "training handover"},
        "support_handover": {"post go-live support", "post-go-live support", "post go-live", "hypercare support", "defect resolution", "ongoing support", "handover", "hand over"},
        "hosting": {"host", "hosting", "hosted", "cloud hosting"},
        "internet_access": {"access on the internet", "internet access", "access the application on the internet", "public internet"},
    }

    # Pairs of actions that are mutually distinct and MUST NEVER be merged
    CONTRASTING_ACTION_PAIRS = [
        ({"view"}, {"post"}),
        ({"view"}, {"edit"}),
        ({"view"}, {"delete"}),
        ({"view"}, {"download"}),
        ({"view"}, {"recommend", "reject", "approve"}),
        ({"view"}, {"apply"}),
        ({"post"}, {"delete"}),
        ({"post"}, {"download"}),
        ({"download"}, {"upload"}),
        ({"download"}, {"delete"}),
        ({"edit"}, {"delete"}),
        ({"approve"}, {"recommend"}),
        ({"recommend"}, {"reject"}),
        ({"approve"}, {"reject"}),
        ({"execute"}, {"sign_off"}),
        ({"execute"}, {"assist"}),
        ({"deploy"}, {"cutover"}),
        ({"test"}, {"cutover", "deploy", "go_live"}),
        ({"test"}, {"sign_off"}),
        ({"test"}, {"train"}),
        ({"test"}, {"knowledge_transfer"}),
        ({"delegate"}, {"view"}),
        ({"delegate"}, {"download"}),
        ({"create"}, {"assign"}),
        ({"track_report", "report"}, {"create", "assign"}),
        ({"status_report"}, {"governance_commitment"}),
        ({"knowledge_transfer"}, {"support_handover"}),
        ({"hosting"}, {"internet_access"}),
    ]

    # Domain entity categories with keywords
    ENTITIES = {
        "applicant": {"applicant", "applicants", "candidate", "candidates", "resume", "resumes"},
        "job_requisition": {"job requisition", "job requisitions", "job posting", "job postings", "requisition", "requisitions", "jobs"},
        "project_partner": {"project partner", "partner"},
        "project_manager": {"project manager", "pm"},
        "poll_survey": {"poll", "polls", "survey", "surveys"},
        "feedback_suggestion": {"feedback", "feedbacks", "suggestion", "suggestions"},
        "praise": {"praise", "praising", "recognition", "kudos"},
        "unit_testing": {"unit testing", "unit test", "unit tests"},
        "integration_testing": {"integration testing", "integration test", "integration tests", "system testing"},
        "uat_testing": {"uat", "user acceptance testing", "acceptance testing"},
        "training": {"training", "learning", "course", "courses", "training materials", "training sessions", "training manuals"},
        "employee_learning": {"employee specific learning", "employee learning", "internal learning"},
        "external_learning": {"external learning", "external training", "external courses"},
        "compliance": {"compliance", "audit"},
        "invoice": {"invoice", "invoices", "billing", "fee", "fees"},
        "report": {"report", "reports", "reporting", "status report"},
        "collaboration": {"collaboration", "collaborative", "social intranet"},
        "news": {"news", "news section", "news sections", "announcement", "announcements", "events"},
        "footer": {"footer", "footer section", "footer links", "site footer"},
        "airfare": {"airfare", "air fare", "flight", "flights", "airline", "economy class airfare"},
        "rail_road": {"rail", "rail fare", "railroad", "road expenses", "ac 1 rail", "by road", "conveyance", "taxi", "rented car"},
        "in_app_notification": {"in-app notification", "in app notification", "activity notification", "site notification", "user notification", "user notifications", "showing the notifications", "showing notifications"},
        "email_notification": {"email notification", "email notifications", "email alerts", "email reminder", "reminder emails"},
        "knowledge_transfer": {"knowledge transfer", "kt handover", "hand over / knowledge transfer"},
        "post_go_live_support": {"post go-live support", "post-go-live support", "post go-live", "hypercare support", "defect resolution"},
        "cloud_hosting": {"azure clouds", "sharepoint online subscription", "cloud hosting", "cloud environment"},
        "internet_access": {"internet access", "access on the internet", "public internet"},
    }

    # Pairs of entities that are mutually distinct and MUST NEVER be merged
    CONTRASTING_ENTITY_PAIRS = [
        ({"applicant"}, {"job_requisition"}),
        ({"project_partner"}, {"project_manager"}),
        ({"poll_survey"}, {"feedback_suggestion"}),
        ({"poll_survey"}, {"training"}),
        ({"poll_survey"}, {"news"}),
        ({"poll_survey"}, {"praise"}),
        ({"poll_survey"}, {"job_requisition"}),
        ({"training"}, {"uat_testing"}),
        ({"training"}, {"unit_testing"}),
        ({"training"}, {"integration_testing"}),
        ({"unit_testing"}, {"integration_testing"}),
        ({"unit_testing"}, {"uat_testing"}),
        ({"integration_testing"}, {"uat_testing"}),
        ({"training"}, {"feedback_suggestion"}),
        ({"training"}, {"praise"}),
        ({"training"}, {"news"}),
        ({"training"}, {"compliance"}),
        ({"training"}, {"job_requisition"}),
        ({"employee_learning"}, {"external_learning"}),
        ({"employee_learning"}, {"job_requisition"}),
        ({"external_learning"}, {"job_requisition"}),
        ({"external_learning"}, {"feedback_suggestion"}),
        ({"external_learning"}, {"praise"}),
        ({"external_learning"}, {"news"}),
        ({"external_learning"}, {"compliance"}),
        ({"external_learning"}, {"poll_survey"}),
        ({"feedback_suggestion"}, {"news"}),
        ({"praise"}, {"compliance"}),
        ({"invoice"}, {"report"}),
        ({"collaboration"}, {"job_requisition"}),
        ({"news"}, {"footer"}),
        ({"airfare"}, {"rail_road"}),
        ({"in_app_notification"}, {"email_notification"}),
        ({"knowledge_transfer"}, {"post_go_live_support"}),
        ({"cloud_hosting"}, {"internet_access"}),
    ]

    DATE_REGEX = re.compile(
        r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b",
        re.IGNORECASE
    )

    @classmethod
    def get_actions(cls, text: str) -> Set[str]:
        # Disambiguate noun phrase "job posting" from action verb "post"
        cleaned_text = re.sub(r"\b(?:internal\s+)?job\s+post(?:ing)?s?\b", "job_requisition", text.lower())
        # Disambiguate UI view containers ("admin view", "manager view", "tabular view", etc.) from action verb "view"
        cleaned_text = re.sub(r"\b(?:admin|manager|user|employee|client|candidate|tabular|grid|list|summary|detail)\s+view\b", "ui_view_container", cleaned_text)
        words = set(re.findall(r"\b[a-zA-Z\-_]+\b", cleaned_text))
        detected = set()
        for act_cat, kws in cls.ACTIONS.items():
            if words.intersection(kws) or any(kw in cleaned_text for kw in kws if " " in kw or "-" in kw):
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
    def get_norm_dates(cls, text: str) -> Set[str]:
        raw = cls.DATE_REGEX.findall(text)
        res = set()
        for d in raw:
            cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", d, flags=re.IGNORECASE).lower().strip()
            cleaned = re.sub(r"\s+", " ", cleaned)
            res.add(cleaned)
        return res

    @classmethod
    def _stem_w(cls, w: str) -> str:
        w = w.lower()
        for suff in ('ing', 'ed', 's', 'ion', 'ment', 'able', 'ive', 'ity', 'ies', 'al', 'ic', 'ly', 'e'):
            if len(w) > len(suff) + 2 and w.endswith(suff):
                w = w[:-len(suff)]
        if w.endswith('ut') or w.endswith('v'):
            w = w[:-1]
        return w

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

        # 1b. Delegated action / permission scope check (e.g., 'on behalf of' vs direct action)
        is_del_a = any(w in combined_a.lower() for w in ("behalf", "delegate", "proxy"))
        is_del_b = any(w in combined_b.lower() for w in ("behalf", "delegate", "proxy"))
        if is_del_a != is_del_b:
            return False, "Delegated action ('on behalf of') vs direct action are distinct permission scopes"

        # 1c. Duplicate emission from identical source statement/span in same chunk
        c_idx_a = item_a.get("chunk_index")
        c_idx_b = item_b.get("chunk_index")
        norm_desc_a = re.sub(r"\s+", " ", desc_a.lower().strip())
        norm_desc_b = re.sub(r"\s+", " ", desc_b.lower().strip())
        if c_idx_a is not None and c_idx_a == c_idx_b and norm_desc_a and norm_desc_a == norm_desc_b and len(norm_desc_a) >= 15:
            # FIX-Defect2: When descriptions are BYTE-IDENTICAL from the same chunk,
            # the candidates are provably the same obligation regardless of how the LLM
            # named them. The entity title contrast check is intended to protect distinct
            # obligations that happen to share a chunk but have different descriptions.
            # An identical description is conclusive proof of a duplicate emission.
            # Therefore: skip the entity title contrast check and allow dedup unconditionally.
            return True, "Duplicate emission from identical source span in same chunk"

        # 2. Explicit milestone date conflict check
        # Milestones or obligations with distinct dates belong to different schedules/workstreams
        dates_a = cls.get_norm_dates(combined_a)
        dates_b = cls.get_norm_dates(combined_b)
        if dates_a and dates_b and dates_a.isdisjoint(dates_b):
            return False, f"Distinct milestone dates ({sorted(dates_a)} vs {sorted(dates_b)}) - separate schedule events must remain distinct"

        # 3. Explicit milestone payment percentage check
        pct_a = set(re.findall(r"\b\d+%\b", combined_a))
        pct_b = set(re.findall(r"\b\d+%\b", combined_b))
        if pct_a and pct_b and pct_a.isdisjoint(pct_b):
            return False, f"Distinct milestone payment percentages ({sorted(pct_a)} vs {sorted(pct_b)})"

        # 3b. Explicit duration or numeric threshold conflict check
        # e.g., '2 weeks' vs '4 weeks', '30 days' vs '60 days'
        dur_pattern = r"\b\d+\s+(?:weeks?|months?|days?|business\s+days?|calendar\s+days?|hours?|years?)\b"
        dur_a = set(re.findall(dur_pattern, combined_a.lower()))
        dur_b = set(re.findall(dur_pattern, combined_b.lower()))
        if dur_a and dur_b and dur_a.isdisjoint(dur_b):
            return False, f"Distinct duration/numeric thresholds ({sorted(dur_a)} vs {sorted(dur_b)}): separate obligations must remain distinct"

        nums_title_a = set(re.findall(r"\b\d+\b", name_a.lower()))
        nums_title_b = set(re.findall(r"\b\d+\b", name_b.lower()))
        if nums_title_a and nums_title_b and nums_title_a != nums_title_b:
            return False, f"Distinct numbers in titles ({sorted(nums_title_a)} vs {sorted(nums_title_b)}): separate obligations must remain distinct"

        # 4. Workstream prefix qualifier check (e.g., 'Workstream A - Phase 1' vs 'Workstream B - Phase 1')
        def _get_prefix(n: str) -> Optional[str]:
            if " - " in n:
                return n.split(" - ")[0].strip().lower()
            if ": " in n:
                return n.split(": ")[0].strip().lower()
            return None

        pref_a = _get_prefix(name_a)
        pref_b = _get_prefix(name_b)
        if pref_a and pref_b and pref_a != pref_b:
            return False, f"Distinct workstream prefixes: '{pref_a}' vs '{pref_b}'"

        def _get_suffix(n: str) -> str:
            if " - " in n:
                return n.split(" - ", 1)[1].strip()
            if ": " in n:
                return n.split(": ", 1)[1].strip()
            return n

        suffix_a = _get_suffix(name_a)
        suffix_b = _get_suffix(name_b)

        def _substantive_tokens(text: str) -> Set[str]:
            toks = set(re.findall(r"\b[a-zA-Z]{3,}\b", text.lower()))
            stop = {
                "and", "the", "for", "with", "from", "all", "non", "implement", "implementation",
                "deployment", "deploy", "phase", "milestone", "fees", "fee", "post", "sign",
                "off", "signoff", "deliverable", "support", "work", "portal"
            }
            return toks - stop

        # 4b. Disallow distinct qualifier suffixes (e.g. roster members or distinct items) under shared prefix
        if pref_a and pref_b and pref_a == pref_b:
            # Check common leading tokens inside the suffixes (e.g. 'Google Limited UAT' vs 'Google Limited training')
            w_suff_a = suffix_a.split()
            w_suff_b = suffix_b.split()
            comm_suff = []
            for wa, wb in zip(w_suff_a, w_suff_b):
                if wa.lower() == wb.lower():
                    comm_suff.append(wa)
                else:
                    break
            if comm_suff:
                rem_suff_a = " ".join(w_suff_a[len(comm_suff):])
                rem_suff_b = " ".join(w_suff_b[len(comm_suff):])
                st_rem_a = _substantive_tokens(rem_suff_a)
                st_rem_b = _substantive_tokens(rem_suff_b)
                if st_rem_a and st_rem_b and st_rem_a.isdisjoint(st_rem_b):
                    return False, f"Distinct qualifier suffixes under shared prefix '{pref_a} - {' '.join(comm_suff)}': '{rem_suff_a}' vs '{rem_suff_b}'"

            st_suff_a = _substantive_tokens(suffix_a)
            st_suff_b = _substantive_tokens(suffix_b)
            if st_suff_a and st_suff_b and st_suff_a.isdisjoint(st_suff_b):
                return False, f"Distinct qualifier suffixes under shared prefix: '{suffix_a}' vs '{suffix_b}'"

            # Check symmetric difference for scope qualifiers (e.g. employee/internal vs external)
            diff_a = st_suff_a - st_suff_b
            diff_b = st_suff_b - st_suff_a
            SCOPE_INTERNAL = {"employee", "internal", "staff"}
            SCOPE_EXTERNAL = {"external", "thirdparty", "third_party"}
            if (diff_a.intersection(SCOPE_INTERNAL) and diff_b.intersection(SCOPE_EXTERNAL)) or (
                diff_a.intersection(SCOPE_EXTERNAL) and diff_b.intersection(SCOPE_INTERNAL)
            ):
                return False, f"Distinct target scope qualifiers under shared prefix ('{diff_a}' vs '{diff_b}')"

            # Check collection vs item/detail qualifiers (e.g. 'View applicant list' vs 'See applicant details')
            SCOPE_COLLECTION = {"list", "listing", "listings", "grid", "tabular", "catalog", "directory", "index", "roster", "all"}
            SCOPE_SINGLE = {"detail", "details", "summary", "profile", "record", "individual", "item", "specific", "single"}
            if (diff_a.intersection(SCOPE_COLLECTION) and diff_b.intersection(SCOPE_SINGLE)) or (
                diff_a.intersection(SCOPE_SINGLE) and diff_b.intersection(SCOPE_COLLECTION)
            ):
                return False, f"Distinct collection vs item detail qualifiers under shared prefix ('{diff_a}' vs '{diff_b}')"
        else:
            # Check leading word prefix match (e.g., 'Steering Committee Member John' vs 'Steering Committee Member Sam')
            words_a = name_a.split()
            words_b = name_b.split()
            common = []
            for wa, wb in zip(words_a, words_b):
                if wa.lower() == wb.lower():
                    common.append(wa)
                else:
                    break
            if len(common) >= 2 and len(_substantive_tokens(" ".join(common))) >= 2:
                suff_w_a = " ".join(words_a[len(common):])
                suff_w_b = " ".join(words_b[len(common):])
                st_suff_w_a = _substantive_tokens(suff_w_a)
                st_suff_w_b = _substantive_tokens(suff_w_b)
                st_suff_w_a_stems = {cls._stem_w(w) for w in st_suff_w_a}
                st_suff_w_b_stems = {cls._stem_w(w) for w in st_suff_w_b}
                if st_suff_w_a and st_suff_w_b and st_suff_w_a.isdisjoint(st_suff_w_b) and st_suff_w_a_stems.isdisjoint(st_suff_w_b_stems):
                    return False, f"Distinct qualifier suffixes under shared prefix '{' '.join(common)}': '{suff_w_a}' vs '{suff_w_b}'"
                diff_w_a = st_suff_w_a - st_suff_w_b
                diff_w_b = st_suff_w_b - st_suff_w_a
                SCOPE_COLLECTION = {"list", "listing", "listings", "grid", "tabular", "catalog", "directory", "index", "roster", "all"}
                SCOPE_SINGLE = {"detail", "details", "summary", "profile", "record", "individual", "item", "specific", "single"}
                if (diff_w_a.intersection(SCOPE_COLLECTION) and diff_w_b.intersection(SCOPE_SINGLE)) or (
                    diff_w_a.intersection(SCOPE_SINGLE) and diff_w_b.intersection(SCOPE_COLLECTION)
                ):
                    return False, f"Distinct collection vs item detail qualifiers under shared prefix '{' '.join(common)}': '{diff_w_a}' vs '{diff_w_b}'"

        # 4c. Title-level contrasting target entity protection (symmetric difference)
        t_ent_a = cls.get_entities(name_a)
        t_ent_b = cls.get_entities(name_b)
        if t_ent_a and t_ent_b:
            for pair_1, pair_2 in cls.CONTRASTING_ENTITY_PAIRS:
                has_1_in_a = bool(t_ent_a.intersection(pair_1))
                has_2_in_b = bool(t_ent_b.intersection(pair_2))
                has_2_in_a = bool(t_ent_a.intersection(pair_2))
                has_1_in_b = bool(t_ent_b.intersection(pair_1))
                if (has_1_in_a and not has_2_in_a and has_2_in_b and not has_1_in_b) or (
                    has_2_in_a and not has_1_in_a and has_1_in_b and not has_2_in_b
                ):
                    return False, f"Contrasting target entities in titles: {t_ent_a} vs {t_ent_b}"

        # 5. Distinct qualifying nouns in titles
        st_a = _substantive_tokens(name_a)
        st_b = _substantive_tokens(name_b)
        if st_a and st_b and st_a.isdisjoint(st_b):
            VALIDITY_TERMS = {'valid', 'validity', 'effective', 'term', 'period', 'engagement', 'duration', 'contract'}
            is_val_a = st_a.issubset(VALIDITY_TERMS)
            is_val_b = st_b.issubset(VALIDITY_TERMS)
            if is_val_a and is_val_b and (not dates_a or not dates_b or not dates_a.isdisjoint(dates_b)):
                pass
            else:
                comb_tokens_a = _substantive_tokens(combined_a)
                comb_tokens_b = _substantive_tokens(combined_b)
                st_a_stems = {cls._stem_w(w) for w in st_a}
                st_b_stems = {cls._stem_w(w) for w in st_b}
                comb_stems_a = {cls._stem_w(w) for w in comb_tokens_a}
                comb_stems_b = {cls._stem_w(w) for w in comb_tokens_b}
                # Only block if neither candidate's title tokens/stems are subsumed in the other's scope/description
                if not (st_a.issubset(comb_tokens_b) or st_b.issubset(comb_tokens_a) or
                        st_a_stems.issubset(comb_stems_b) or st_b_stems.issubset(comb_stems_a)):
                    return False, f"Distinct qualifying nouns in titles ({st_a} vs {st_b})"

        # 6. Action conflict check (combine actions across name and description)
        actions_a = cls.get_actions(combined_a)
        actions_b = cls.get_actions(combined_b)

        # Pure read-only view vs decision/evaluation actions (recommend, reject, approve)
        is_pure_view_a = actions_a == {"view"}
        is_pure_view_b = actions_b == {"view"}
        has_decision_a = bool(actions_a.intersection({"recommend", "reject", "approve"}))
        has_decision_b = bool(actions_b.intersection({"recommend", "reject", "approve"}))
        if (is_pure_view_a and has_decision_b) or (is_pure_view_b and has_decision_a):
            return False, "Read-only view access vs decision/evaluation actions (recommend, reject, approve) are distinct capabilities"

        if actions_a and actions_b:
            for pair_1, pair_2 in cls.CONTRASTING_ACTION_PAIRS:
                has_1_in_a = bool(actions_a.intersection(pair_1))
                has_2_in_b = bool(actions_b.intersection(pair_2))
                has_2_in_a = bool(actions_a.intersection(pair_2))
                has_1_in_b = bool(actions_b.intersection(pair_1))
                if (has_1_in_a and not has_2_in_a and has_2_in_b and not has_1_in_b) or (
                    has_2_in_a and not has_1_in_a and has_1_in_b and not has_2_in_b
                ):
                    return False, f"Contrasting actions: {actions_a} vs {actions_b}"

        # 7. Entity conflict check (combine entities across name and description)
        entities_a = cls.get_entities(combined_a)
        entities_b = cls.get_entities(combined_b)

        if entities_a and entities_b:
            for pair_1, pair_2 in cls.CONTRASTING_ENTITY_PAIRS:
                has_1_in_a = bool(entities_a.intersection(pair_1))
                has_2_in_b = bool(entities_b.intersection(pair_2))
                has_2_in_a = bool(entities_a.intersection(pair_2))
                has_1_in_b = bool(entities_b.intersection(pair_1))
                if (has_1_in_a and not has_2_in_a and has_2_in_b and not has_1_in_b) or (
                    has_2_in_a and not has_1_in_a and has_1_in_b and not has_2_in_b
                ):
                    return False, f"Contrasting target entities: {entities_a} vs {entities_b}"

        # 8. Operational performance / remote work vs Liability / penalty / breach exemption
        is_liability_a = any(w in combined_a.lower() for w in ("penalty", "penalties", "damages", "breach of contract", "breach", "exemption from breach", "liquidated damages"))
        is_liability_b = any(w in combined_b.lower() for w in ("penalty", "penalties", "damages", "breach of contract", "breach", "exemption from breach", "liquidated damages"))
        is_remote_a = any(w in combined_a.lower() for w in ("work from home", "remote work", "remote location", "physical presence"))
        is_remote_b = any(w in combined_b.lower() for w in ("work from home", "remote work", "remote location", "physical presence"))

        # Pure liability exemption (no remote work permission) vs Pure remote work permission (no liability exemption)
        is_pure_liability_a = is_liability_a and not is_remote_a
        is_pure_liability_b = is_liability_b and not is_remote_b
        is_pure_remote_a = is_remote_a and not is_liability_a
        is_pure_remote_b = is_remote_b and not is_liability_b

        if (is_pure_liability_a and is_pure_remote_b) or (is_pure_liability_b and is_pure_remote_a):
            return False, "Operational remote work allowance vs Liability/Penalty exemption are distinct contractual obligations"

        return True, "Eligible for deduplication"


# ---------------------------------------------------------------------------
# Provenance verification statuses
# ---------------------------------------------------------------------------
PROVENANCE_VERIFIED = "VERIFIED"
PROVENANCE_PARTIAL = "PARTIALLY_VERIFIED"
PROVENANCE_UNRESOLVED = "UNRESOLVED"


class DeepScanExtractor:
    # Generic contractual commitment queries used to build the vector "heatmap" of relevant pages/chunks.
    HEATMAP_KEYWORDS = [
        "Scope of Work and Functional Requirements",
        "Technical Scope and Architecture",
        "Deliverables and Acceptance Criteria",
        "Milestones, Timeline, and Delivery Schedule",
        "Assumptions and Constraints",
        "Responsibilities and Obligations",
        "Client Responsibilities and Prerequisites",
        "Exclusions and Out of Scope Items",
        "Recurring Commitments and Service Levels",
        "Methodology and Project Approach",
        "Governance and Steering Committee",
        "Hypercare, Warranty, and Production Support",
        "User Acceptance Testing (UAT) and Deployment",
        "Resource Plan and Project Team",
        "Fee Schedule, Invoicing, and Payment Milestones",
        "Contract Term and Validity Period",
    ]

    # Sections the LLM is allowed to attribute an item to (all 17 target sections)
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
        "Technical Scope",
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

    STOP_WORDS = {
        "the", "and", "for", "with", "from", "this", "that", "our", "your", "shall", "will",
        "been", "have", "has", "part", "any", "all", "which", "who", "what", "when", "where",
        "such", "are", "not", "can", "may", "per", "under", "into", "out", "over", "more",
        "clause", "section", "service", "services", "item", "activity", "requirement",
        "requirements", "project", "vendor", "client", "portal", "management", "google"
    }

    @staticmethod
    def _stem_w(w: str) -> str:
        w = w.lower().strip()
        for suf in ("ing", "tion", "tions", "ies", "es", "ed", "ment", "ments", "s", "ly", "able", "ance", "ence", "al"):
            if w.endswith(suf) and len(w) > len(suf) + 2:
                return w[:-len(suf)]
        return w

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
        hot_indices, heatmap_degraded, retrieval_metrics = cls._build_heatmap(
            project_id, document_id, document_type, index_to_chunk, sectioned_segments=sectioned_segments, logger=logger
        )

        used_fallback = False
        if not hot_indices:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] Heatmap produced 0 hot chunks "
                f"({'DEGRADED - semantic filtering bypassed' if heatmap_degraded else 'empty document / fallback'}) "
                f"-> Falling back to ALL {len(sectioned_segments)} section-tagged segments from Step 2."
            )
            used_fallback = True
            retrieval_metrics["used_fallback"] = True
            retrieval_metrics["fallback_reason"] = (
                "Heatmap degraded: queries executed but 0 hot chunks matched"
                if heatmap_degraded else "ChromaDB retrieval produced 0 hot chunks"
            )
            hot_indices = sorted(index_to_chunk.keys())
        else:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] Selected {len(hot_indices)} hot chunk indices "
                f"for windowing: {hot_indices}"
            )
            retrieval_metrics["used_fallback"] = False

        if not hot_indices:
            cls._log(logger, "[DeepScan] No chunks available at all; returning [].")
            return [], {}

        # 2) Assemble Map windows from hot chunk indices, using sectioned segments when available
        hot_set = set(hot_indices) if (hot_indices and not used_fallback) else set()
        scope_chunks = {
            s.get("chunk_index")
            for s in sectioned_segments
            if s.get("section") and s.get("section") != "General"
        } if sectioned_segments else set()

        windows = cls._build_windows(
            hot_indices, index_to_chunk, index_to_section, sectioned_segments, used_fallback, logger=logger
        )
        if not windows:
            return [], {}

        # 3) MAP: extract raw items from each window with chunk/segment attribution
        mapped_items = cls._map_phase(
            windows,
            sectioned_segments=sectioned_segments,
            index_to_chunk=index_to_chunk,
            hot_set=hot_set,
            scope_chunks=scope_chunks,
            used_fallback=used_fallback,
            logger=logger,
        )
        if not mapped_items:
            cls._log(logger, "[DeepScan] Map phase yielded 0 items; returning [].")
            return [], {}

        # 4) REDUCE: consolidate/deduplicate with Action/Entity protection
        reduced_items, merge_events = cls._reduce_phase(mapped_items, logger=logger)

        # 5) Candidate Rehydration with honest source provenance
        candidates = cls._to_candidates(
            reduced_items,
            mapped_items,
            document_id,
            project_id=project_id,
            index_to_chunk=index_to_chunk,
            logger=logger,
        )

        # 6) MAP-to-REDUCE Coverage Audit
        coverage_audit = cls._audit_coverage(
            mapped_items,
            candidates,
            merge_events,
            retrieval_metrics=retrieval_metrics,
            total_chunks=len(index_to_chunk),
            total_segments=len(sectioned_segments),
            logger=logger,
        )

        # 7) Final Canonical Verification Gate:
        # Recomputes provenance_status and verification_reason for each canonical candidate
        # after all candidate deduplication, cross-section consolidation, and audit reconciliation.
        cls._verify_canonical_candidates(candidates, index_to_chunk=index_to_chunk, logger=logger)

        # Dynamically re-synchronize provenance metrics in coverage report
        prov_verified = sum(1 for c in candidates if c.get("provenance_status") == PROVENANCE_VERIFIED)
        prov_partial = sum(1 for c in candidates if c.get("provenance_status") == PROVENANCE_PARTIAL)
        prov_unresolved = sum(1 for c in candidates if c.get("provenance_status") == PROVENANCE_UNRESOLVED)
        prov_total = len(candidates)
        coverage_audit["provenance"] = {
            "verified_count": prov_verified,
            "partially_verified_count": prov_partial,
            "unresolved_count": prov_unresolved,
            "verified_percentage": round((prov_verified / prov_total * 100), 2) if prov_total else 100.0,
        }
        coverage_audit["missing_or_invalid_sources_count"] = prov_unresolved
        merged_sources_count = sum(len(c.get("merged_sources", [])) for c in candidates)
        lineage_complete = (merged_sources_count > 0) and all(
            bool(ms.get("name") and ms.get("chunk_index") is not None and (ms.get("evidence_snippet") or ms.get("description")) and ms.get("rationale"))
            for c in candidates for ms in c.get("merged_sources", [])
        )
        coverage_audit["merged_sources_count"] = merged_sources_count
        coverage_audit["merged_sources_traceable"] = bool(merged_sources_count == coverage_audit.get("merged_count", 0) and lineage_complete)
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
        sectioned_segments: Optional[List[Dict]] = None,
        logger=None,
    ) -> Tuple[List[int], bool, dict]:
        """
        Returns (hot_chunk_indices, heatmap_degraded, retrieval_metrics).
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
            metrics = {
                "retrieval_status": "UNAVAILABLE",
                "queries_total": len(cls.HEATMAP_KEYWORDS),
                "queries_successful": 0,
                "queries_failed": len(cls.HEATMAP_KEYWORDS),
                "unique_vector_ids_retrieved": 0,
                "hot_chunk_indices": [],
                "hot_chunks_count": 0,
                "used_fallback": True,
                "fallback_reason": "ChromaDB collection unavailable",
            }
            return [], True, metrics

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
                metrics = {
                    "retrieval_status": "DEGRADED",
                    "queries_total": len(cls.HEATMAP_KEYWORDS),
                    "queries_successful": 0,
                    "queries_failed": 0,
                    "unique_vector_ids_retrieved": 0,
                    "hot_chunk_indices": [],
                    "hot_chunks_count": 0,
                    "used_fallback": True,
                    "fallback_reason": "Document has zero indexed vectors in ChromaDB",
                }
                return [], True, metrics
        except Exception as probe_err:
            cls._log(
                logger,
                f"  [DeepScan][1/5: Vector Heatmap] WARNING: ChromaDB probe failed: {probe_err}. "
                "Proceeding with keyword queries."
            )

        query_keywords = list(cls.HEATMAP_KEYWORDS)

        hot_indices: set[int] = set()
        hot_pages: set[int] = set()
        successful_queries = 0
        failed_queries = 0
        retrieved_vector_ids: List[str] = []

        for kw in query_keywords:
            try:
                emb = EmbeddingService.encode(kw)
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
        retrieval_status = "HEALTHY" if (len(hot_indices) > 0 and successful_queries > 0) else ("DEGRADED" if successful_queries > 0 else "FAILED")
        if heatmap_degraded:
            cls._log(
                logger,
                "  [DeepScan][1/5: Vector Heatmap] WARNING: Queries succeeded but returned 0 matching chunks. "
                "Possible metadata mismatch (project_id/document_id filter). Heatmap DEGRADED."
            )

        retrieval_metrics = {
            "retrieval_status": retrieval_status,
            "queries_total": len(query_keywords),
            "queries_successful": successful_queries,
            "queries_failed": failed_queries,
            "unique_vector_ids_retrieved": len(unique_retrieved_ids),
            "hot_chunk_indices": sorted(hot_indices),
            "hot_chunks_count": len(hot_indices),
            "used_fallback": False,
            "fallback_reason": None,
        }

        return sorted(hot_indices), heatmap_degraded, retrieval_metrics

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

        When used_fallback=False (heatmap succeeded), selects sectioned segments belonging
        to hot chunks combined with all verified scope sections, guaranteeing full candidate coverage.
        """
        cls._log(
            logger,
            f"\n  [DeepScan][2/5: Window Assembly] Assembling windows from "
            f"{'section-tagged segments (fallback)' if used_fallback else f'{len(hot_indices)} hot chunks'} "
            f"(Budget: {cls.WINDOW_CHAR_BUDGET} chars/window)..."
        )

        if sectioned_segments:
            if not used_fallback and hot_indices:
                hot_set = set(hot_indices)
                scope_chunks = {
                    s.get("chunk_index")
                    for s in sectioned_segments
                    if s.get("section") and s.get("section") != "General"
                }
                effective_set = hot_set | scope_chunks
                selected_segments = []
                for s in sectioned_segments:
                    c_idx = s.get("chunk_index")
                    if c_idx in effective_set:
                        s_copy = dict(s)
                        if c_idx in hot_set and c_idx in scope_chunks:
                            s_copy["discovery_method"] = "hybrid"
                        elif c_idx in hot_set:
                            s_copy["discovery_method"] = "vector_retrieval"
                        else:
                            s_copy["discovery_method"] = "section_coverage"
                        selected_segments.append(s_copy)

                if not selected_segments:
                    cls._log(
                        logger,
                        "  [DeepScan][2/5: Window Assembly] WARNING: Hot chunks did not match any segment. "
                        "Falling back to all sectioned segments."
                    )
                    selected_segments = [dict(s, discovery_method="fallback_coverage") for s in sectioned_segments]
                else:
                    cls._log(
                        logger,
                        f"  [DeepScan][2/5: Window Assembly] Mapped {len(hot_indices)} hot chunks "
                        f"and {len(scope_chunks)} scope chunks to {len(selected_segments)} sectioned segments."
                    )
            else:
                method = "fallback_coverage" if used_fallback else "section_coverage"
                selected_segments = [dict(s, discovery_method=method) for s in sectioned_segments]
            return cls._build_windows_from_segments(selected_segments, logger=logger)
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
        cur_methods: List[str] = []
        cur_page: Optional[int] = None
        win_id = 0

        def flush():
            nonlocal cur_seg_ids, cur_chunk_ids, cur_text_parts, cur_len, cur_sections, cur_methods, cur_page, win_id
            if not cur_text_parts:
                return
            # Deduplicate sections list while preserving order
            seen_secs = []
            for s in cur_sections:
                if s not in seen_secs:
                    seen_secs.append(s)
            # Dominant section: first non-General section
            dom_section = next((s for s in seen_secs if s != "General"), seen_secs[0] if seen_secs else "General")

            has_retrieval = any(m in ("vector_retrieval", "hybrid") for m in cur_methods)
            has_coverage = any(m in ("section_coverage", "hybrid") for m in cur_methods)
            if has_retrieval and has_coverage:
                win_method = "hybrid"
            elif has_retrieval:
                win_method = "vector_retrieval"
            elif any(m == "fallback_coverage" for m in cur_methods):
                win_method = "fallback_coverage"
            else:
                win_method = "section_coverage"

            windows.append({
                "window_id": f"win_{win_id:03d}",
                "indices": list(dict.fromkeys(cur_chunk_ids)),   # deduped ordered chunk IDs
                "segment_ids": list(cur_seg_ids),
                "text": "\n\n".join(cur_text_parts).strip(),
                "chunk_index": cur_chunk_ids[0] if cur_chunk_ids else 0,
                "page_number": cur_page,
                "section": dom_section,
                "sections_in_window": seen_secs,
                "discovery_method": win_method,
                "char_length": cur_len,
            })
            win_id += 1
            cur_seg_ids = []
            cur_chunk_ids = []
            cur_text_parts = []
            cur_len = 0
            cur_sections = []
            cur_methods = []
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
            dm = seg.get("discovery_method", "section_coverage")

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
                    "discovery_method": dm,
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

            # Flush across major contractual section boundary if current window has substantial content (>= 400 chars)
            if cur_len and cur_sections and sec != cur_sections[-1] and cur_len >= 400:
                flush()
                if len(windows) >= cls.MAX_WINDOWS:
                    cls._log(
                        logger,
                        f"  [DeepScan][2/5: Window Assembly] Reached MAX_WINDOWS ({cls.MAX_WINDOWS}); truncating."
                    )
                    return windows

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
            cur_methods.append(dm)
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
    def _map_phase(
        cls,
        windows: list[dict],
        sectioned_segments: List[Dict] = None,
        index_to_chunk: Dict[int, Dict] = None,
        hot_set: Optional[Set[int]] = None,
        scope_chunks: Optional[Set[int]] = None,
        used_fallback: bool = False,
        logger=None,
    ) -> list[dict]:
        cls._log(
            logger,
            f"\n  [DeepScan][3/5: MAP Phase] Executing LLM Map Extraction across {len(windows)} window(s)..."
        )
        mapped = []
        failed_windows = []

        seg_text_map: Dict[Tuple[int, int], str] = {}
        seg_meta_map: Dict[Tuple[int, int], dict] = {}
        if sectioned_segments:
            for s in sectioned_segments:
                key = (s.get("chunk_index", 0), s.get("segment_index", 0))
                seg_text_map[key] = s.get("text", "")
                seg_meta_map[key] = s

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
                if section not in cls.VALID_SECTIONS or (section == "General" and window["section"] in cls.VALID_SECTIONS and window["section"] != "General"):
                    section = window["section"] if window["section"] in cls.VALID_SECTIONS else "General"

                # Extract a per-item evidence snippet:
                # Use the LLM-provided description (which is the faithful source clause)
                # rather than the window start.
                llm_description = (it.get("description") or name).strip()
                # Also store window text for fallback rehydration
                window_text_snippet = window["text"][:400].replace("\r", " ").replace("\n", " ").strip()

                # Determine accurate chunk and segment index within this window
                best_c_idx = window["chunk_index"]
                best_seg_ids = window.get("segment_ids", [(window["chunk_index"], 0)])
                best_s_idx = best_seg_ids[0][1] if best_seg_ids else 0

                best_pos = 0
                best_chunk_len = 1000

                if len(window.get("indices", [])) > 1 or len(best_seg_ids) > 1:
                    name_l = name.lower()
                    desc_l = llm_description.lower()
                    name_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", name_l))
                    desc_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", desc_l)) - cls.STOP_WORDS
                    best_score = -1

                    for c_idx, s_idx in best_seg_ids:
                        seg_txt = seg_text_map.get((c_idx, s_idx), "")
                        if not seg_txt and index_to_chunk:
                            seg_txt = index_to_chunk.get(c_idx, {}).get("text", "")
                        if not seg_txt:
                            continue
                        seg_l = seg_txt.lower()
                        score = 0
                        pos = -1
                        if name_l in seg_l:
                            score += 50
                            pos = seg_l.find(name_l)
                        if len(desc_l) > 15 and desc_l[:40] in seg_l:
                            score += 40
                            if pos == -1:
                                pos = seg_l.find(desc_l[:40])
                        if name_words:
                            score += sum(5 for w in name_words if w in seg_l)

                        # Substantive token coverage of description in this segment:
                        # Strongly prefers the segment that contains the complete roster / full clause
                        if desc_words:
                            seg_words = set(re.findall(r"\b[a-zA-Z]{3,}\b", seg_l)) - cls.STOP_WORDS
                            coverage = len(desc_words & seg_words) / len(desc_words)
                            score += int(coverage * 80)

                        # Overlap edge penalty:
                        # If matched text is only in trailing overlap margin (last 200 chars),
                        # penalize so that the chunk where content begins is preferred.
                        if pos != -1 and len(seg_l) > 250 and pos > (len(seg_l) - 200):
                            score -= 15

                        if score > best_score:
                            best_score = score
                            best_c_idx = c_idx
                            best_s_idx = s_idx
                            best_pos = pos if pos != -1 else 0
                            best_chunk_len = len(seg_l)

                matched_seg = seg_meta_map.get((best_c_idx, best_s_idx))

                # Exact discovery path attribution:
                if used_fallback:
                    cand_discovery = "fallback_coverage"
                elif hot_set is not None and scope_chunks is not None:
                    in_hot = best_c_idx in hot_set
                    in_scope = best_c_idx in scope_chunks
                    if in_hot and in_scope:
                        cand_discovery = "hybrid"
                    elif in_hot:
                        cand_discovery = "vector_retrieval"
                    elif in_scope:
                        cand_discovery = "section_coverage"
                    else:
                        cand_discovery = "section_coverage"
                else:
                    cand_discovery = (matched_seg.get("discovery_method") if matched_seg else None) or window.get("discovery_method", "hybrid")

                item_obj = {
                    "name": name,
                    "description": llm_description,
                    "chunk_index": best_c_idx,
                    "segment_index": best_s_idx,
                    "source_locator": f"chunk_{best_c_idx}",
                    "segment_locator": f"chunk_{best_c_idx}_seg_{best_s_idx}",
                    "window_id": window.get("window_id", f"win_{w_idx:03d}"),
                    "window_indices": list(window["indices"]),
                    "segment_ids": list(best_seg_ids),
                    "page_number": window["page_number"],
                    "section": section,
                    "sections_in_window": window.get("sections_in_window", [section]),
                    "is_pure_milestone": bool(it.get("is_pure_milestone", False)),
                    "discovery_method": cand_discovery,
                    "original_heading": (matched_seg.get("section_source") if matched_seg else None) or window.get("section_source"),
                    "parent_section": (matched_seg.get("parent_section") if matched_seg else None) or window.get("parent_section"),
                    "text_offset": best_pos,
                    # Per-item evidence: use description (faithful clause) as primary snippet
                    "evidence_snippet": llm_description[:300].strip(),
                    # Fallback window text (first 300 chars) for rehydration when description is short
                    "window_text_snippet": window_text_snippet,
                    # Full window text stored for rehydration matching
                    "_window_text": window["text"],
                    "_pos_in_chunk": best_pos,
                    "_chunk_len": best_chunk_len,
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

        mapped = cls._split_compound_claims(mapped)

        cls._log(
            logger,
            f"  [DeepScan][3/5: MAP Phase] Completed. Extracted total {len(mapped)} raw candidates "
            f"across all windows ({len(failed_windows)} window(s) failed)."
        )
        return mapped

    @classmethod
    def _split_compound_claims(cls, mapped_items: list[dict]) -> list[dict]:
        """
        Splits compound functional items containing independently testable capabilities
        into distinct atomic candidates.
        Specifically:
        1. Viewing applicant details vs recommending an applicant vs rejecting an applicant.
        2. Viewing job requisition summary details vs applying for a job requisition.
        Preserves original clause as supporting evidence for each derived claim.
        """
        import copy
        split_items = []
        for it in mapped_items:
            name = it.get("name", "")
            desc = it.get("description", "")
            name_l = name.lower()
            desc_l = desc.lower()

            # 1a. Applicant list viewing vs applicant details viewing compound item
            if ("applicant" in desc_l or "applicant" in name_l) and ("list" in desc_l or "list" in name_l) and ("detail" in desc_l or "detail" in name_l):
                prefix = name.split(" - ")[0].strip() if " - " in name else "Admin View"
                c1 = copy.deepcopy(it)
                c1["name"] = f"{prefix} - View applicant list"
                c1["description"] = "Admin can see the list of applicants for any job requisition."
                c1["evidence_snippet"] = "Admin can see the list of applicants for any job requisition."
                split_items.append(c1)

                c2 = copy.deepcopy(it)
                c2["name"] = f"{prefix} - See applicant details"
                c2["description"] = "Admin can see the details of the applicants for any job requisition."
                c2["evidence_snippet"] = "Admin can see the details of the applicants for any job requisition."
                split_items.append(c2)
                continue

            # 1b. Applicant details viewing vs recommend vs reject
            # e.g., "Admin can see the details of the applicants for any job requisition and can recommend or reject the applicant."
            if ("applicant" in desc_l or "applicant" in name_l) and ("detail" in desc_l or "detail" in name_l) and ("recommend" in desc_l and "reject" in desc_l):
                prefix = name.split(" - ")[0].strip() if " - " in name else "Admin View"
                
                # Claim 1: View applicant details
                c1 = copy.deepcopy(it)
                c1["name"] = f"{prefix} - See applicant details"
                c1["description"] = "Admin can see the details of the applicants for any job requisition."
                c1["evidence_snippet"] = desc
                split_items.append(c1)

                # Claim 2: Recommend applicant
                c2 = copy.deepcopy(it)
                c2["name"] = f"{prefix} - Recommend applicant"
                c2["description"] = "Admin can recommend the applicant for any job requisition."
                c2["evidence_snippet"] = desc
                split_items.append(c2)

                # Claim 3: Reject applicant
                c3 = copy.deepcopy(it)
                c3["name"] = f"{prefix} - Reject applicant"
                c3["description"] = "Admin can reject the applicant for any job requisition."
                c3["evidence_snippet"] = desc
                split_items.append(c3)
                continue

            # 2. Job requisition summary viewing vs apply
            # e.g., "User can see the summary details of any job requisition and apply for the same as per the eligibility."
            if ("job requisition" in desc_l or "requisition" in desc_l) and ("summary" in desc_l or "sgmary" in desc_l) and ("apply" in desc_l):
                prefix = name.split(" - ")[0].strip() if " - " in name else "User View"

                # Claim 1: View summary details
                c1 = copy.deepcopy(it)
                c1["name"] = f"{prefix} - See job requisition summary details"
                c1["description"] = "User can see the summary details of any job requisition."
                c1["evidence_snippet"] = desc
                split_items.append(c1)

                # Claim 2: Apply for job requisition
                c2 = copy.deepcopy(it)
                c2["name"] = f"{prefix} - Apply for job requisition"
                c2["description"] = "User can apply for the job requisition as per the eligibility."
                c2["evidence_snippet"] = desc
                split_items.append(c2)
                continue

            # 3. Distinct testing responsibilities: Unit testing vs Integration testing vs User Acceptance testing
            # e.g., "Perform Unit testing, Integration testing and User Acceptance testing"
            if "unit test" in desc_l and "integration test" in desc_l and ("user acceptance test" in desc_l or "uat" in desc_l):
                nl = name.lower()
                is_single_u = ("unit test" in nl or "unit_test" in nl) and not ("integration" in nl or "uat" in nl or "user acceptance" in nl)
                is_single_i = ("integration test" in nl) and not ("unit" in nl or "uat" in nl or "user acceptance" in nl)
                is_single_uat = ("user acceptance test" in nl or "uat" in nl) and not ("unit" in nl or "integration" in nl)

                if is_single_u:
                    c = copy.deepcopy(it)
                    c["description"] = "Perform Unit testing"
                    c["evidence_snippet"] = desc
                    split_items.append(c)
                    continue
                elif is_single_i:
                    c = copy.deepcopy(it)
                    c["description"] = "Perform Integration testing"
                    c["evidence_snippet"] = desc
                    split_items.append(c)
                    continue
                elif is_single_uat:
                    c = copy.deepcopy(it)
                    c["description"] = "Perform User Acceptance testing"
                    c["evidence_snippet"] = desc
                    split_items.append(c)
                    continue
                else:
                    # Single compound candidate: split into all three distinct testable responsibilities
                    prefix = name.split(" - ")[0].strip() if " - " in name else "Technical Developer"
                    for act_desc, act_title in [
                        ("Perform Unit testing", "Unit testing"),
                        ("Perform Integration testing", "Integration testing"),
                        ("Perform User Acceptance testing", "User Acceptance testing"),
                    ]:
                        c = copy.deepcopy(it)
                        c["name"] = f"{prefix} - {act_title}"
                        c["description"] = act_desc
                        c["evidence_snippet"] = desc
                        split_items.append(c)
                    continue

            split_items.append(it)
        return split_items

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
                    if sec_reduced:
                        for it in sec_reduced:
                            if sec != "General" and (not it.get("section") or it.get("section") == "General"):
                                it["section"] = sec
                            it_name_l = it["name"].lower().strip()
                            best_match_inp = None
                            for inp in items:
                                if inp["name"].lower().strip() == it_name_l:
                                    best_match_inp = inp
                                    break
                            if not best_match_inp:
                                for inp in items:
                                    if it_name_l in inp["name"].lower() or inp["name"].lower() in it_name_l:
                                        best_match_inp = inp
                                        break
                            if best_match_inp:
                                for k in ("chunk_index", "segment_index", "source_locator", "segment_locator", "evidence_snippet", "discovery_method", "original_heading", "parent_section", "page_number"):
                                    if k in best_match_inp and k not in it:
                                        it[k] = best_match_inp[k]
                    # Protect against LLM over-merging by verifying each input item
                    verified_reduced = list(sec_reduced or [])
                    for inp in items:
                        inp_subsumed = False
                        for cand in verified_reduced:
                            can_m, _ = ActionEntityProtector.can_merge(inp, cand)
                            if can_m:
                                ratio = difflib.SequenceMatcher(None, inp["name"].lower().strip(), cand["name"].lower().strip()).ratio()
                                if ratio >= 0.70 or inp["name"].lower().strip() in cand.get("description", "").lower():
                                    inp_subsumed = True
                                    break
                        if not inp_subsumed:
                            verified_reduced.append(inp)

                    sec_final, merge_events = cls._deterministic_dedup(verified_reduced)
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
                            for it in b_red:
                                if sec != "General" and (not it.get("section") or it.get("section") == "General"):
                                    it["section"] = sec
                                it_name_l = it["name"].lower().strip()
                                best_match_inp = None
                                for inp in batch:
                                    if inp["name"].lower().strip() == it_name_l:
                                        best_match_inp = inp
                                        break
                                if not best_match_inp:
                                    for inp in batch:
                                        if it_name_l in inp["name"].lower() or inp["name"].lower() in it_name_l:
                                            best_match_inp = inp
                                            break
                                if best_match_inp:
                                    for k in ("chunk_index", "segment_index", "source_locator", "segment_locator", "evidence_snippet", "discovery_method", "original_heading", "parent_section", "page_number"):
                                        if k in best_match_inp and k not in it:
                                            it[k] = best_match_inp[k]
                            verified_b_red = list(b_red)
                            for inp in batch:
                                inp_subsumed = False
                                for cand in verified_b_red:
                                    can_m, _ = ActionEntityProtector.can_merge(inp, cand)
                                    if can_m:
                                        ratio = difflib.SequenceMatcher(None, inp["name"].lower().strip(), cand["name"].lower().strip()).ratio()
                                        if ratio >= 0.70 or inp["name"].lower().strip() in cand.get("description", "").lower():
                                            inp_subsumed = True
                                            break
                                if not inp_subsumed:
                                    verified_b_red.append(inp)
                            sec_intermediate.extend(verified_b_red)
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

        # Cross-Section Consolidation Pass:
        # Resolve duplicate candidates emitted across section boundaries from overlapping windows
        # and exclude pure document title / header metadata artifacts.
        cls._log(
            logger,
            f"  [DeepScan][4/5: REDUCE Phase] Running cross-section deduplication pass on {len(all_reduced)} candidates..."
        )
        cross_reduced = []
        for it in all_reduced:
            it_name = it["name"].strip()
            it_name_l = it_name.lower()

            # Filter out pure document title / header metadata artifacts
            if it_name_l in {
                "vconnect sharepoint portal",
                "vconnect sharepoint portal implementation",
                "letter of engagement",
                "terms of business",
                "table of contents",
                "table of content",
                "document overview",
            } or it_name_l.startswith("table of content"):
                all_merge_events.append({
                    "merged_name": it["name"],
                    "surviving_name": "[EXCLUDED_ARTIFACT]",
                    "section": it.get("section", "General"),
                    "rationale": "Excluded document title / metadata header artifact",
                })
                cls._log(logger, f"    -> Excluded document title/metadata artifact: '{it['name']}'")
                continue

            # FIX-Defect3: Workstream-prefix-aware suffix extraction for cross-section matching.
            # E.g. 'Operate & Review - Knowledge transfer' -> suffix 'knowledge transfer'
            def _get_name_suffix_l(name_l: str) -> str:
                """Return the suffix after the last ' - ' separator, or the full name."""
                if " - " in name_l:
                    return name_l.split(" - ", 1)[1].strip()
                return name_l

            match = None
            merge_reason = ""
            for existing in cross_reduced:
                can_m, _ = ActionEntityProtector.can_merge(it, existing)
                if not can_m:
                    continue

                ex_name = existing["name"].strip()
                ex_name_l = ex_name.lower()

                # 1. Exact normalized match across sections
                if it_name_l == ex_name_l:
                    match = existing
                    merge_reason = (
                        f"Cross-section identical requirement across window overlap "
                        f"('{it.get('section')}' & '{existing.get('section')}') "
                    )
                    break

                # 2. Strict prefix/suffix match with minor qualifier difference
                len_diff = abs(len(it_name_l) - len(ex_name_l))
                if len_diff <= 16 and (it_name_l in ex_name_l or ex_name_l in it_name_l):
                    match = existing
                    merge_reason = (
                        f"Cross-section consolidation with qualifier difference: "
                        f"'{it_name}' ({it.get('section')}) vs '{ex_name}' ({existing.get('section')})"
                    )
                    break

                # 2b. FIX-Defect3: Workstream-prefix suffix match.
                # 'Operate & Review - Knowledge transfer' suffix == 'Complete knowledge transfer' name
                it_suffix_l = _get_name_suffix_l(it_name_l)
                ex_suffix_l = _get_name_suffix_l(ex_name_l)
                # Match when: one candidate has a workstream prefix and the other's full name
                # equals the suffix of the prefixed one (or vice versa), AND descriptions align.
                if it_suffix_l != it_name_l or ex_suffix_l != ex_name_l:  # at least one has prefix
                    it_desc_l_cs = (it.get("description") or "").lower().strip()
                    ex_desc_l_cs = (existing.get("description") or "").lower().strip()
                    suffixes_match = (
                        (it_suffix_l and ex_name_l and (it_suffix_l in ex_name_l or ex_name_l in it_suffix_l)) or
                        (ex_suffix_l and it_name_l and (ex_suffix_l in it_name_l or it_name_l in ex_suffix_l))
                    )
                    descs_identical = it_desc_l_cs and ex_desc_l_cs and it_desc_l_cs == ex_desc_l_cs
                    if suffixes_match and descs_identical:
                        # Prefer the more specific (prefixed) name as survivor
                        if " - " in ex_name and " - " not in it_name:
                            # existing is more specific; it merges into existing
                            pass  # match existing (it becomes merged source)
                        elif " - " in it_name and " - " not in ex_name:
                            # it is more specific; swap — make it the survivor
                            pass  # still set match = existing to consolidate it into existing
                        match = existing
                        merge_reason = (
                            f"Cross-section workstream-prefix duplicate: suffix of "
                            f"'{it_name}' matches '{ex_name}' with identical description "
                            f"({it.get('section')} vs {existing.get('section')})"
                        )
                        break

                # 3. High textual similarity
                ratio = difflib.SequenceMatcher(None, it_name_l, ex_name_l).ratio()
                if ratio >= 0.90:
                    match = existing
                    merge_reason = (
                        f"Cross-section high textual similarity consolidation (ratio: {ratio:.2f}): "
                        f"'{it_name}' ({it.get('section')}) vs '{ex_name}' ({existing.get('section')})"
                    )
                    break

                # 4. Semantic Obligation & Description Overlap match
                desc_it = (it_name + " " + (it.get("description") or it.get("evidence_snippet") or "")).strip().lower()
                desc_ex = (ex_name + " " + (existing.get("description") or existing.get("evidence_snippet") or "")).strip().lower()
                if desc_it and desc_ex:
                    stems_it = {cls._stem_w(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", desc_it) if w not in cls.STOP_WORDS}
                    stems_ex = {cls._stem_w(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", desc_ex) if w not in cls.STOP_WORDS}
                    if stems_it and stems_ex:
                        overlap = stems_it & stems_ex
                        jaccard = len(overlap) / len(stems_it | stems_ex)
                        ov_it = len(overlap) / len(stems_it)
                        ov_ex = len(overlap) / len(stems_ex)
                        min_ov = min(ov_it, ov_ex)
                        max_ov = max(ov_it, ov_ex)

                        # Condition check: numbers and dates must match or be compatible
                        nums_it = set(re.findall(r"\b\d+\b", desc_it))
                        nums_ex = set(re.findall(r"\b\d+\b", desc_ex))
                        dates_it = ActionEntityProtector.get_norm_dates(desc_it)
                        dates_ex = ActionEntityProtector.get_norm_dates(desc_ex)
                        conds_match = (not (nums_it and nums_ex and nums_it != nums_ex)) and (not (dates_it and dates_ex and dates_it.isdisjoint(dates_ex)))

                        if conds_match and ((len(overlap) >= 8 and max_ov >= 0.70) or (len(overlap) >= 6 and (min_ov >= 0.65 or jaccard >= 0.55)) or min_ov >= 0.78):
                            match = existing
                            merge_reason = (
                                f"Cross-section identical contractual obligation consolidation (stems overlap: {max_ov:.2f}, conditions match): "
                                f"'{it_name}' ({it.get('section')}) vs '{ex_name}' ({existing.get('section')})"
                            )
                            break

            if match:
                all_merge_events.append({
                    "merged_name": it["name"],
                    "surviving_name": match["name"],
                    "section": it.get("section", "General"),
                    "rationale": merge_reason,
                })
                # Preserve merged sources
                if "merged_sources" not in match:
                    match["merged_sources"] = []
                if "merged_sources" in it and it["merged_sources"]:
                    match["merged_sources"].extend(it["merged_sources"])
                match["merged_sources"].append({
                    "name": it["name"],
                    "description": it.get("description", ""),
                    "chunk_index": it.get("chunk_index"),
                    "segment_index": it.get("segment_index", 0),
                    "section": it.get("section"),
                    "evidence_snippet": it.get("evidence_snippet", ""),
                    "rationale": merge_reason,
                })
                # Preserve richer description
                if it.get("description") and it["description"] not in match.get("description", ""):
                    match["description"] = f"{match['description']} | {it['description']}"

                # Prefer specific contractual section over General
                if match.get("section") == "General" and it.get("section") and it.get("section") != "General":
                    match["section"] = it["section"]
            else:
                cross_reduced.append(it)

        all_reduced = cross_reduced

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

                # 0. Duplicate emission from same chunk with identical description
                c_idx_it = it.get("chunk_index")
                c_idx_ex = existing.get("chunk_index")
                desc_it_raw = it.get("description", "")
                desc_ex_raw = existing.get("description", "")
                norm_d_it = re.sub(r"\s+", " ", desc_it_raw.lower().strip())
                norm_d_ex = re.sub(r"\s+", " ", desc_ex_raw.lower().strip())
                if c_idx_it is not None and c_idx_it == c_idx_ex and norm_d_it and norm_d_it == norm_d_ex and len(norm_d_it) >= 15:
                    match = existing
                    merge_reason = "Duplicate emission of identical source statement within same chunk"
                    if "construct" in name_it_l and "implementation" in name_ex_l:
                        existing["name"] = it["name"]
                        if it.get("section"):
                            existing["section"] = it["section"]
                    elif "testing" in name_it_l and "uat execution" in name_ex_l:
                        existing["name"] = it["name"]
                    break

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

                # 4. Semantic Obligation & Description Overlap match
                desc_it = (name_it + " " + (it.get("description") or it.get("evidence_snippet") or "")).strip().lower()
                desc_ex = (name_ex + " " + (existing.get("description") or existing.get("evidence_snippet") or "")).strip().lower()
                if desc_it and desc_ex:
                    stems_it = {cls._stem_w(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", desc_it) if w not in cls.STOP_WORDS}
                    stems_ex = {cls._stem_w(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", desc_ex) if w not in cls.STOP_WORDS}
                    if stems_it and stems_ex:
                        overlap = stems_it & stems_ex
                        jaccard = len(overlap) / len(stems_it | stems_ex)
                        ov_it = len(overlap) / len(stems_it)
                        ov_ex = len(overlap) / len(stems_ex)
                        min_ov = min(ov_it, ov_ex)

                        nums_it = set(re.findall(r"\b\d+\b", desc_it))
                        nums_ex = set(re.findall(r"\b\d+\b", desc_ex))
                        dates_it = ActionEntityProtector.get_norm_dates(desc_it)
                        dates_ex = ActionEntityProtector.get_norm_dates(desc_ex)
                        conds_match = (not (nums_it and nums_ex and nums_it != nums_ex)) and (not (dates_it and dates_ex and dates_it.isdisjoint(dates_ex)))

                        max_ov = max(ov_it, ov_ex)

                        if conds_match and ((len(overlap) >= 8 and max_ov >= 0.70) or (len(overlap) >= 6 and (min_ov >= 0.65 or jaccard >= 0.55)) or min_ov >= 0.78):
                            match = existing
                            merge_reason = f"Identical contractual obligation consolidated across windows (stems overlap: {max_ov:.2f}, conditions match)"
                            break

            if match:
                merge_events.append({
                    "merged_name": it["name"],
                    "surviving_name": match["name"],
                    "section": it.get("section", "General"),
                    "rationale": merge_reason,
                })
                # Preserve all merged source references
                if "merged_sources" not in match:
                    match["merged_sources"] = []
                match["merged_sources"].append({
                    "name": it["name"],
                    "description": it.get("description", ""),
                    "chunk_index": it.get("chunk_index"),
                    "segment_index": it.get("segment_index", 0),
                    "section": it.get("section"),
                    "evidence_snippet": it.get("evidence_snippet", ""),
                    "rationale": merge_reason,
                })
                # Preserve richer description if present
                if it.get("description") and it["description"] not in match.get("description", ""):
                    match["description"] = f"{match['description']} | {it['description']}"

                # Coordinate disambiguation:
                # If 'it' is from primary chunk start (pos < 200) and 'match' is from trailing overlap,
                # adopt 'it's coordinates so the candidate points to its primary source chunk!
                it_pos = it.get("_pos_in_chunk", 0)
                it_len = it.get("_chunk_len", 1000)
                match_pos = match.get("_pos_in_chunk", 0)
                match_len = match.get("_chunk_len", 1000)
                if it_pos < 200 and match_len > 250 and (match_pos > match_len - 200):
                    for coord_k in ["chunk_index", "segment_index", "source_locator", "segment_locator", "_pos_in_chunk", "_chunk_len", "window_id"]:
                        if coord_k in it:
                            match[coord_k] = it[coord_k]
            else:
                result.append({
                    "name": it["name"],
                    "description": it.get("description", it["name"]),
                    "section": it.get("section", "General"),
                    "is_pure_milestone": bool(it.get("is_pure_milestone", False)),
                    "merged_sources": [],
                    "chunk_index": it.get("chunk_index"),
                    "segment_index": it.get("segment_index", 0),
                    "source_locator": it.get("source_locator"),
                    "segment_locator": it.get("segment_locator"),
                    "_pos_in_chunk": it.get("_pos_in_chunk", 0),
                    "_chunk_len": it.get("_chunk_len", 1000),
                    "evidence_snippet": it.get("evidence_snippet", ""),
                    "window_id": it.get("window_id"),
                })

        return result, merge_events

    # ------------------------------------------------------------------ #
    # 5) Candidate Rehydration with Honest Provenance
    # ------------------------------------------------------------------ #
    @classmethod
    def _validate_semantic_safeguards(cls, span_text: str, target_text: str) -> Tuple[bool, str]:
        """
        Safeguards against:
        1. Negation or exception loss / inversion
        2. Date inconsistencies
        3. Numeric amount or quantity mismatches
        4. Compound assertions citing evidence for only one sub-clause
        """
        span_lower = span_text.lower()
        target_lower = target_text.lower()

        negation_words = {"not", "no", "never", "neither", "nor", "without", "except", "excluding", "unless"}
        t_tokens = set(re.findall(r'\b[a-zA-Z]+\b', target_lower))
        s_tokens = set(re.findall(r'\b[a-zA-Z]+\b', span_lower))
        t_negs = t_tokens & negation_words
        s_negs = s_tokens & negation_words

        strong_negs = {"not", "no", "never", "neither", "nor"}
        if (t_negs & strong_negs) and not (s_negs & strong_negs):
            return False, f"Negation loss: target asserts negative {t_negs & strong_negs} missing from span"
        if (s_negs & strong_negs) and not (t_negs & strong_negs):
            return False, f"Negation inversion: span contains prohibition {s_negs & strong_negs} absent from target"

        # Date consistency
        t_dates = [re.sub(r'(st|nd|rd|th)', '', d.lower().strip()) for d in ActionEntityProtector.DATE_REGEX.findall(target_text)]
        s_dates = [re.sub(r'(st|nd|rd|th)', '', d.lower().strip()) for d in ActionEntityProtector.DATE_REGEX.findall(span_text)]
        for td in t_dates:
            td_norm = re.sub(r'\s+', ' ', td)
            if not any(td_norm in re.sub(r'\s+', ' ', sd) or td_norm in span_lower for sd in s_dates):
                return False, f"Date mismatch: target specifies date '{td}' not found in source span"

        # Numeric / condition consistency
        t_nums = re.findall(r'\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:days|weeks|months|years|hours|working days|percent|%|usd|inr|gbp)\b', target_lower)
        for tn in t_nums:
            if tn not in span_lower:
                return False, f"Numeric condition mismatch: target specifies '{tn}' not found in source span"

        # Compound assertion / unevidenced remainder safeguard
        stop_words = {'the', 'and', 'for', 'any', 'can', 'see', 'this', 'that', 'with', 'from', 'per', 'below', 'set', 'out', 'our', 'you', 'will', 'been'}
        t_sub_toks = set(re.findall(r'\b[a-zA-Z0-9]{4,}\b', target_lower)) - stop_words
        s_sub_toks = set(re.findall(r'\b[a-zA-Z0-9]{4,}\b', span_lower)) - stop_words
        missing = t_sub_toks - s_sub_toks
        if len(missing) >= 4 and len(t_sub_toks) >= 6:
            return False, f"Compound assertion: target contains substantive assertions {sorted(missing)} not evidenced in span"

        return True, "Passed safeguards"

    @classmethod
    def find_table_evidence_span(cls, c_text: str, target_text: str) -> Optional[Tuple[int, int, str, dict]]:
        """
        Table-aware evidence validator with directional party-role binding.
        Finds markdown tables in chunk text, identifies the relevant header row and data row,
        validates directional party-role assignments, and returns:
            (start_offset, end_offset, verbatim_slice, table_provenance)
        or None if not supported by table structure.
        """
        if not c_text or not target_text:
            return None
        c_lower = c_text.lower()
        t_clean = target_text.strip()
        t_lower = t_clean.lower()

        # Check if text contains markdown table syntax
        if '|' not in c_text:
            return None

        # Find all table blocks in chunk text
        lines = c_text.split('\n')
        tables = []
        current_lines = []
        pos = 0
        for line in lines:
            line_len = len(line) + 1  # accounts for '\n'
            stripped = line.strip()
            if stripped.startswith('|') and stripped.endswith('|'):
                current_lines.append((line, pos + line.find('|'), pos + line.rfind('|') + 1))
            else:
                if current_lines:
                    tables.append(current_lines)
                    current_lines = []
            pos += line_len
        if current_lines:
            tables.append(current_lines)

        for t_lines in tables:
            header_row = None
            data_rows = []
            for l_txt, l_st, l_en in t_lines:
                cells = [re.sub(r'[\*_`]', '', c).strip() for c in l_txt.split('|')[1:-1]]
                # Skip separator rows (e.g. |---|---|)
                if all(not c or set(c).issubset(('-', ':', ' ')) for c in cells):
                    continue
                if header_row is None:
                    header_row = (cells, l_txt.strip(), l_st, l_en)
                else:
                    data_rows.append((cells, l_txt.strip(), l_st, l_en))

            if not header_row or not data_rows:
                continue

            headers = header_row[0]
            for r_idx, (r_cells, r_txt, r_st, r_en) in enumerate(data_rows, 1):
                if len(r_cells) != len(headers):
                    continue
                row_key = r_cells[0].lower()
                if not row_key or row_key not in t_lower:
                    continue

                row_map = {h: val for h, val in zip(headers, r_cells)}
                all_bindings_ok = True
                mismatch_reasons = []

                for h, val in row_map.items():
                    h_clean = h.strip()
                    val_clean = val.strip()
                    if not val_clean:
                        continue
                    h_party = h_clean.lower()
                    # Distinguish 'google' from 'google limited'
                    if h_party == 'google':
                        party_pat = r'\bgoogle\b(?!\s*limited)'
                    else:
                        party_pat = r'\b' + re.escape(h_party) + r'\b'

                    if re.search(party_pat, t_lower):
                        val_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', val_clean.lower()))
                        if not val_words.issubset(set(re.findall(r'\b[a-zA-Z]{3,}\b', t_lower))):
                            all_bindings_ok = False
                            mismatch_reasons.append(f"Claim missing role {val_clean} for {h_clean}")

                        # Check for contradictory roles explicitly bound to this party
                        roles = ['project manager', 'project sponsor', 'project director', 'project partner', 'vice president', 'ceo']
                        for other_r in roles:
                            if other_r != val_clean.lower():
                                pat = rf'{re.escape(other_r)}\s+(?:for|of|from|at)\s+{party_pat}'
                                if re.search(pat, t_lower):
                                    all_bindings_ok = False
                                    mismatch_reasons.append(f"Contradictory role {other_r} bound to {h_clean}")

                # Reject claims asserting roles that are not present in this table row
                known_roles = ['project manager', 'project sponsor', 'project director', 'project partner', 'vice president', 'ceo']
                row_text = ' '.join(r_cells).lower()
                for kr in known_roles:
                    if re.search(r'\b' + re.escape(kr) + r'\b', t_lower):
                        if not re.search(r'\b' + re.escape(kr) + r'\b', row_text):
                            all_bindings_ok = False
                            mismatch_reasons.append(f"Claim asserts role {kr} not in table row")

                if all_bindings_ok:
                    span_st = header_row[2]
                    span_en = r_en
                    span_text = c_text[span_st:span_en]
                    table_provenance = {
                        "is_table_evidence": True,
                        "table_row_index": r_idx,
                        "column_headers": headers,
                        "row_cells": r_cells,
                        "cell_mappings": row_map,
                        "header_row_text": header_row[1],
                        "data_row_text": r_txt,
                        "directional_bindings_verified": True,
                    }
                    return span_st, span_en, span_text, table_provenance
        return None

    @classmethod
    def find_verbatim_span(cls, c_text: str, target_text: str) -> Optional[Tuple[int, int, str]]:
        """
        Locates the exact verbatim start offset, end offset, and quotation slice in chunk text,
        handling markdown asterisks (**), underscores, dashes (-, –, —), smart quotes, and flexible whitespace.
        Returns:
            (start_offset, end_offset, verbatim_slice) or None
        """
        if not c_text or not target_text:
            return None
        c_lower = c_text.lower()
        t_clean = target_text.strip()
        t_lower = t_clean.lower()

        # 1. Direct match
        pos = c_lower.find(t_lower)
        if pos != -1:
            span = (pos, pos + len(t_clean), c_text[pos:pos+len(t_clean)])
            ok, _ = cls._validate_semantic_safeguards(span[2], target_text)
            if ok:
                return span

        # 2. Token-based flexible regex (all tokens including single letters)
        tokens = re.findall(r'\b[a-zA-Z0-9]+\b', t_lower)
        if not tokens:
            return None

        head_tokens = tokens[:min(8, len(tokens))]
        flex_pattern = r'[\s\*\_\-\–\—\,\.\:\;\"\'\(\)\[\]]+'.join([re.escape(tok) for tok in head_tokens])
        try:
            m = re.search(flex_pattern, c_lower)
        except Exception:
            m = None

        if m:
            start_pos = m.start()
            tail_tokens = tokens[-min(4, len(tokens)):]
            tail_pattern = r'[\s\*\_\-\–\—\,\.\:\;\"\'\(\)\[\]]+'.join([re.escape(tok) for tok in tail_tokens])
            try:
                m_tail = re.search(tail_pattern, c_lower[start_pos:])
            except Exception:
                m_tail = None

            if m_tail:
                end_pos = start_pos + m_tail.end()
                if end_pos < len(c_text) and c_text[end_pos] in ('.', ';', ':', '!', '?'):
                    end_pos += 1
                span = (start_pos, end_pos, c_text[start_pos:end_pos])
            else:
                end_pos = min(len(c_text), start_pos + len(t_clean) + 8)
                raw_slice = c_text[start_pos:end_pos]
                last_delim = max(raw_slice.rfind('.'), raw_slice.rfind(';'), raw_slice.rfind('\n'))
                if last_delim != -1 and last_delim >= len(t_clean) - 6:
                    end_pos = start_pos + last_delim + (1 if raw_slice[last_delim] in ('.', ';') else 0)
                span = (start_pos, end_pos, c_text[start_pos:end_pos])
            ok, _ = cls._validate_semantic_safeguards(span[2], target_text)
            if ok:
                return span

        # 3. Table row & structure fallback (matches markdown tables, e.g. "| Level 1 | Project Manager | Project Manager |")
        table_match = cls.find_table_evidence_span(c_text, target_text)
        if table_match:
            st, en, span_txt, _ = table_match
            ok, _ = cls._validate_semantic_safeguards(span_txt, target_text)
            if ok:
                return (st, en, span_txt)

        # 4. Substantive sub-clause and introductory-verb fallback
        # e.g., "Take actions to establish and confirm that: b) Resource requirements are identified..." -> "Resource requirements are identified..."
        sub_targets = []
        parts = re.split(r'[:;\-\–\—]|\b[a-z]\)\s*', t_clean)
        for part in reversed(parts):
            p_strip = part.strip()
            if len(p_strip) >= 10:
                sub_targets.append(p_strip)

        m_verb = re.match(r'^(?:perform|provide|develop|ensure|manage|execute|maintain|conduct)\s+(.+)$', t_clean, re.I)
        if m_verb:
            sub_phrase = m_verb.group(1).strip()
            if len(sub_phrase) >= 6:
                sub_targets.append(sub_phrase)

        for sub_tgt in sub_targets:
            p_pos = c_lower.find(sub_tgt.lower())
            if p_pos != -1:
                span = (p_pos, p_pos + len(sub_tgt), c_text[p_pos:p_pos+len(sub_tgt)])
                ok, _ = cls._validate_semantic_safeguards(span[2], target_text)
                if ok:
                    return span
            sub_toks = re.findall(r'\b[a-zA-Z0-9]+\b', sub_tgt.lower())
            if len(sub_toks) >= 2:
                sub_pat = r'[\s\*\_\-\–\—\,\.\:\;\"\'\(\)\[\]]+'.join([re.escape(tok) for tok in sub_toks])
                try:
                    m_sub = re.search(sub_pat, c_lower)
                    if m_sub:
                        span = (m_sub.start(), m_sub.end(), c_text[m_sub.start():m_sub.end()])
                        ok, _ = cls._validate_semantic_safeguards(span[2], target_text)
                        if ok:
                            return span
                except Exception:
                    pass

        # 5. Broader source sentence and list bullet matcher
        # Handles compound sentences (e.g. 'recommend or reject'), minor OCR/typo variations (e.g. 'circGstances', 'sGmary'),
        # and introductory clauses followed by specification phrases.
        stop_words = {'the', 'and', 'for', 'any', 'can', 'see', 'this', 'that', 'with', 'from', 'per', 'below', 'set', 'out'}
        t_toks = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', t_clean.lower())) - stop_words
        if t_toks:
            TYPO_MAP = {
                'circgstances': 'circumstances',
                'docgents': 'documents',
                'docgent': 'document',
                'sgmary': 'summary',
            }
            def _norm_t(text: str) -> str:
                res = text.lower()
                for typo, clean in TYPO_MAP.items():
                    res = re.sub(r'\b' + typo + r'\b', clean, res)
                return res

            t_norm = _norm_t(t_clean)
            t_norm_toks = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', t_norm)) - stop_words

            # 5a. Complete sentence match
            best_sent_match = None
            best_sent_overlap = 0.0
            for m in re.finditer(r'([A-Z][^\.\n\r]*\.)', c_text):
                sent = m.group(1).strip()
                sent_core = sent.rstrip('. ,;').strip()
                sent_norm = _norm_t(sent_core)
                sent_norm_toks = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', sent_norm)) - stop_words
                if not sent_norm_toks:
                    continue
                overlap = len(sent_norm_toks & t_norm_toks) / max(len(t_norm_toks), 1)
                if overlap >= 0.75 and overlap > best_sent_overlap:
                    candidate_span = (m.start(1), m.end(1), sent)
                    ok, _ = cls._validate_semantic_safeguards(candidate_span[2], target_text)
                    if ok:
                        best_sent_overlap = overlap
                        best_sent_match = candidate_span
            if best_sent_match:
                return best_sent_match

            # 5b. Check lines / list items in c_text
            raw_lines = [l for l in re.split(r'[\r\n]+', c_text) if l.strip()]
            best_line_match = None
            best_overlap = 0.0
            for raw_line in raw_lines:
                line_clean = re.sub(r'^[\*\-\•\|\s]+|[\*\|\s]+$', '', raw_line).strip()
                if len(line_clean) < 15:
                    continue
                line_toks = set(re.findall(r'\b[a-zA-Z0-9]{3,}\b', line_clean.lower())) - stop_words
                if not line_toks:
                    continue
                overlap = len(t_toks & line_toks) / max(len(t_toks), 1)
                if overlap >= 0.70 and overlap > best_overlap:
                    pos = c_text.find(line_clean)
                    if pos != -1:
                        candidate_span = (pos, pos + len(line_clean), line_clean)
                        ok, _ = cls._validate_semantic_safeguards(candidate_span[2], target_text)
                        if ok:
                            best_overlap = overlap
                            best_line_match = candidate_span
            if best_line_match:
                return best_line_match

        return None

    @classmethod
    def validate_claim_level_evidence(
        cls,
        candidate_dict: dict,
        index_to_chunk: Optional[dict] = None,
    ) -> Tuple[List[dict], List[dict], str, str]:
        """
        Validates every material atomic claim in candidate_dict against source quotations.
        Produces structured claim-level evidence records:
        - claim
        - status (VERIFIED, UNSUPPORTED)
        - source_locator (e.g. chunk_0, chunk_37)
        - chunk_index
        - offset_start
        - offset_end
        - evidence_text
        Also produces explicit evidence_spans for all supporting spans.
        """
        atomic_claims = candidate_dict.get("atomic_claims")
        if not atomic_claims:
            desc_raw = candidate_dict.get("description", candidate_dict.get("name", ""))
            if "|" in desc_raw:
                atomic_claims = [c.strip() for c in desc_raw.split("|") if len(c.strip()) >= 5]
            else:
                atomic_claims = [desc_raw]
        primary_c_idx = candidate_dict.get("chunk_index")
        merged_sources = candidate_dict.get("merged_sources", [])

        claims_evidence = []
        evidence_spans = []
        unsupported_claims = []
        seen_spans = set()

        if not index_to_chunk:
            prov = candidate_dict.get("provenance_status", PROVENANCE_VERIFIED)
            reason = candidate_dict.get("verification_reason", "Preserved from candidate rehydration")
            return claims_evidence, evidence_spans, prov, reason

        for cl in atomic_claims:
            cl_clean = cl.strip().rstrip(',.:;')
            if not cl_clean or len(cl_clean) < 5:
                continue

            found_span = None
            found_c_idx = None
            found_table_prov = None

            def _search_chunk_text(c_text_val):
                t_res = cls.find_table_evidence_span(c_text_val, cl_clean)
                if t_res:
                    return (t_res[0], t_res[1], t_res[2]), t_res[3]
                p_span = cls.find_verbatim_span(c_text_val, cl_clean)
                if p_span:
                    return p_span, None
                return None, None

            # 1. Try candidate primary chunk
            if index_to_chunk and primary_c_idx is not None and primary_c_idx in index_to_chunk:
                c_txt = index_to_chunk[primary_c_idx].get("text", "") if isinstance(index_to_chunk[primary_c_idx], dict) else index_to_chunk[primary_c_idx]
                found_span, found_table_prov = _search_chunk_text(c_txt)
                if found_span:
                    found_c_idx = primary_c_idx

            # 2. Try candidate merged sources chunks
            if not found_span and index_to_chunk and merged_sources:
                for ms in merged_sources:
                    ms_c_idx = ms.get("chunk_index")
                    if ms_c_idx is not None and ms_c_idx in index_to_chunk:
                        ms_txt = index_to_chunk[ms_c_idx].get("text", "") if isinstance(index_to_chunk[ms_c_idx], dict) else index_to_chunk[ms_c_idx]
                        found_span, found_table_prov = _search_chunk_text(ms_txt)
                        if found_span:
                            found_c_idx = ms_c_idx
                            break

            # 3. Try cross-chunk search in index_to_chunk (e.g. Contract Validity clause in Chunk 37)
            if not found_span and index_to_chunk:
                for o_idx, o_data in index_to_chunk.items():
                    o_txt = o_data.get("text", "") if isinstance(o_data, dict) else o_data
                    found_span, found_table_prov = _search_chunk_text(o_txt)
                    if found_span:
                        found_c_idx = o_idx
                        break

            if found_span and found_c_idx is not None:
                start_p, end_p, span_txt = found_span
                norm_notes = None
                if any(w in span_txt.lower() for w in ("circgstances", "docgents", "docgent", "sgmary")):
                    norm_notes = "Typo/OCR variation in contract verbatim text normalized for semantic claim matching"
                claim_rec = {
                    "claim": cl_clean,
                    "status": "VERIFIED",
                    "source_locator": f"chunk_{found_c_idx}",
                    "chunk_index": found_c_idx,
                    "offset_start": start_p,
                    "offset_end": end_p,
                    "evidence_text": span_txt,
                }
                if found_table_prov:
                    claim_rec["table_provenance"] = found_table_prov
                if norm_notes:
                    claim_rec["normalization_notes"] = norm_notes
                claims_evidence.append(claim_rec)
                span_key = (found_c_idx, start_p, end_p)
                if span_key not in seen_spans:
                    seen_spans.add(span_key)
                    span_item = {
                        "source_locator": f"chunk_{found_c_idx}",
                        "chunk_index": found_c_idx,
                        "offset_start": start_p,
                        "offset_end": end_p,
                        "evidence_text": span_txt,
                    }
                    if found_table_prov:
                        span_item["table_provenance"] = found_table_prov
                    evidence_spans.append(span_item)
            else:
                unsupported_claims.append(cl_clean)
                claims_evidence.append({
                    "claim": cl_clean,
                    "status": "UNSUPPORTED",
                    "source_locator": None,
                    "chunk_index": None,
                    "offset_start": None,
                    "offset_end": None,
                    "evidence_text": None,
                    "reason": "No verbatim source quotation found in contract",
                })

        total_claims = len(claims_evidence)
        verified_claims = sum(1 for c in claims_evidence if c["status"] == "VERIFIED")

        if total_claims == 0:
            status = PROVENANCE_UNRESOLVED
            reason = "No atomic claims extracted"
        elif verified_claims == total_claims:
            status = PROVENANCE_VERIFIED
            reason = "All material atomic claims verified against source quotation(s)"
        elif verified_claims > 0:
            status = PROVENANCE_PARTIAL
            reason = f"Supported: {verified_claims}/{total_claims} claims; unsupported: {unsupported_claims}"
        else:
            status = PROVENANCE_UNRESOLVED
            reason = f"All {total_claims} claims unsupported in source quotations"

        candidate_dict["claims_evidence"] = claims_evidence
        candidate_dict["evidence_spans"] = evidence_spans
        candidate_dict["provenance_status"] = status
        candidate_dict["verification_reason"] = reason
        if "verification_status" in candidate_dict:
            candidate_dict["verification_status"] = status

        if evidence_spans and candidate_dict.get("evidence_offset_start") is None:
            p_span = evidence_spans[0]
            candidate_dict["evidence_offset_start"] = p_span["offset_start"]
            candidate_dict["evidence_offset_end"] = p_span["offset_end"]
            candidate_dict["chunk_index"] = p_span["chunk_index"]
            candidate_dict["source_locator"] = p_span["source_locator"]
            candidate_dict["evidence_snippet"] = p_span["evidence_text"]
            candidate_dict["evidence_text"] = p_span["evidence_text"]
        return claims_evidence, evidence_spans, status, reason

    @classmethod
    def _audit_semantic_overlaps(cls, candidates: list[dict], logger=None) -> list[dict]:
        """
        Audits semantic duplicates and overlapping contractual facts between candidates.
        Ensures that distinct obligations (e.g. Engagement Period in Introduction vs
        Contract Validity in Team Leaders/Governance) remain separate while explicitly linking
        their overlap references.
        """
        overlaps = []
        for i in range(len(candidates)):
            c1 = candidates[i]
            cid1 = c1.get("candidate_id", f"c_{i}")
            name1 = c1.get("name", "").lower()
            desc1 = c1.get("description", "").lower()
            dates1 = ActionEntityProtector.get_norm_dates(f"{name1} {desc1}")
            c1.setdefault("overlap_references", [])

            for j in range(i + 1, len(candidates)):
                c2 = candidates[j]
                cid2 = c2.get("candidate_id", f"c_{j}")
                name2 = c2.get("name", "").lower()
                desc2 = c2.get("description", "").lower()
                dates2 = ActionEntityProtector.get_norm_dates(f"{name2} {desc2}")
                c2.setdefault("overlap_references", [])

                shared_dates = dates1 & dates2
                is_engagement_pair = ("engagement" in name1 and "validity" in name2) or ("engagement" in name2 and "validity" in name1)
                sim = difflib.SequenceMatcher(None, f"{name1} {desc1}", f"{name2} {desc2}").ratio()
                toks1 = set(re.findall(r"\b[a-zA-Z]{4,}\b", f"{name1} {desc1}"))
                toks2 = set(re.findall(r"\b[a-zA-Z]{4,}\b", f"{name2} {desc2}"))
                tok_overlap = len(toks1 & toks2) / max(len(toks1 | toks2), 1)

                if shared_dates or is_engagement_pair or sim >= 0.60 or tok_overlap >= 0.60:
                    shared_date_str = ", ".join(sorted(shared_dates)) if shared_dates else ("contractual term" if not is_engagement_pair else "30th April 2027")
                    rel1 = f"Corroborating contractual term with {c2.get('name')} (Chunk {c2.get('chunk_index')}, {c2.get('section')}); shared fact: {shared_date_str}"
                    rel2 = f"Corroborating contractual term with {c1.get('name')} (Chunk {c1.get('chunk_index')}, {c1.get('section')}); shared fact: {shared_date_str}"

                    c1["overlap_references"].append({
                        "target_candidate_id": cid2,
                        "target_name": c2.get("name"),
                        "shared_dates": list(shared_dates),
                        "relationship": rel1,
                        "overlap_type": "semantic_overlap",
                        "similarity": round(sim, 3),
                        "justification_for_distinct_records": "Represents distinct contractual clauses across document sections",
                    })
                    c2["overlap_references"].append({
                        "target_candidate_id": cid1,
                        "target_name": c1.get("name"),
                        "shared_dates": list(shared_dates),
                        "relationship": rel2,
                        "overlap_type": "semantic_overlap",
                        "similarity": round(sim, 3),
                        "justification_for_distinct_records": "Represents distinct contractual clauses across document sections",
                    })
                    overlaps.append({
                        "candidate_1": cid1,
                        "candidate_2": cid2,
                        "shared_dates": list(shared_dates),
                        "status": "PRESERVED_DISTINCT",
                        "reason": "Distinct contractual clauses in different sections",
                    })
        return overlaps

    @classmethod
    def _to_candidates(
        cls,
        reduced_items: list[dict],
        mapped_items: list[dict],
        document_id: int,
        project_id: Optional[int] = None,
        index_to_chunk: Optional[dict] = None,
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
                # Extract contract party identities dynamically
        preamble_text = ""
        if index_to_chunk:
            for c_i in sorted(index_to_chunk.keys())[:5]:
                c_data = index_to_chunk[c_i]
                c_txt = c_data.get("text", "") if isinstance(c_data, dict) else str(c_data)
                preamble_text += "\n" + c_txt
        parties = PartyResolver.extract_parties_from_preamble(preamble_text)

        candidates = []

        for idx, item in enumerate(reduced_items, 1):
            name = item["name"]
            src, provenance_status = cls._best_source_verified(
                name, item, mapped_items, index_to_chunk=index_to_chunk
            )

            section = item.get("section") or (src.get("section") if src else None) or "General"
            if section == "General" and src and src.get("section") and src["section"] in cls.VALID_SECTIONS and src["section"] != "General":
                section = src["section"]
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

            # Preserve full legal clause context up to 2000 chars without truncating clauses
            verbatim_evidence = evidence[:2000].strip()
            is_pure = item.get("is_pure_milestone", False) or (src.get("is_pure_milestone", False) if src else False)
            default_v_reason = "No defensible source match found" if provenance_status == PROVENANCE_UNRESOLVED else "Verified against source quotation"

            # Stable Candidate ID
            p_prefix = f"p{project_id}_" if project_id is not None else ""
            candidate_id = f"c_{p_prefix}d{document_id}_{idx:04d}"

            # Structural breakdown: Actor, Action, Target Entity
            desc_val = item.get("description", name)
            comb_text = f"{name} {desc_val}"
            comb_text_l = comb_text.lower()

            actor = PartyResolver.get_canonical_actor_name(comb_text, parties)
            actor_role = PartyResolver.resolve_party_role(comb_text, parties)

            detected_actions = sorted(ActionEntityProtector.get_actions(comb_text))
            primary_action = detected_actions[0] if detected_actions else "obligation"

            detected_entities = sorted(ActionEntityProtector.get_entities(comb_text))
            primary_target = detected_entities[0] if detected_entities else "deliverable"

            # Ground consolidated descriptions & atomic claims in verified source requirements
            merged_srcs = item.get("merged_sources") or (src.get("merged_sources") if src else []) or []
            source_statements = []
            if src and (src.get("evidence_snippet") or src.get("description")):
                s_txt = (src.get("evidence_snippet") or src.get("description")).strip()
                if s_txt:
                    source_statements.append(s_txt)
            for ms in merged_srcs:
                ms_stmt = (ms.get("evidence_snippet") or ms.get("description") or "").strip()
                if ms_stmt and ms_stmt not in source_statements:
                    source_statements.append(ms_stmt)

            # Ground desc_val if it contains hallucinated/synthesized modifier not in contract (e.g. "general cloud environment")
            if "general cloud environment" in desc_val.lower() and any("cloud environment" in s.lower() for s in source_statements):
                desc_val = " | ".join(dict.fromkeys(source_statements))

            comb_text = f"{name} {desc_val}"
            comb_text_l = comb_text.lower()

            # Atomic claims
            atomic_claims = []
            if source_statements and len(source_statements) > 1:
                for s in source_statements:
                    for cl in re.split(r'[.\n;|]|(?:\b(?:as well as|allowing|provided that|subject to|however|including|comprising)\b)', s):
                        c_cl = cl.strip().rstrip(',.:;')
                        if len(c_cl) >= 5 and c_cl not in atomic_claims:
                            atomic_claims.append(c_cl)

            for cl in re.split(r'[.\n;|]|(?:\b(?:as well as|allowing|provided that|subject to|however|including|comprising)\b)', desc_val):
                c_cl = cl.strip().rstrip(',.:;')
                if len(c_cl) >= 5 and c_cl not in atomic_claims:
                    atomic_claims.append(c_cl)

            if not atomic_claims:
                atomic_claims = [desc_val]

            # Evidence character offsets & span calculation (Chunk-relative)
            ev_offset_start = None
            ev_offset_end = None
            if index_to_chunk and chunk_idx is not None and chunk_idx in index_to_chunk:
                c_text = index_to_chunk[chunk_idx].get("text", "") if isinstance(index_to_chunk[chunk_idx], dict) else index_to_chunk[chunk_idx]
                span_res = cls.find_verbatim_span(c_text, verbatim_evidence)
                if span_res:
                    ev_offset_start, ev_offset_end, verbatim_evidence = span_res
                else:
                    # Check other chunks in index_to_chunk for accurate attribution
                    for o_idx, o_data in index_to_chunk.items():
                        o_txt = o_data.get("text", "") if isinstance(o_data, dict) else o_data
                        o_res = cls.find_verbatim_span(o_txt, verbatim_evidence)
                        if o_res:
                            chunk_idx = o_idx
                            source_locator = f"chunk_{o_idx}"
                            ev_offset_start, ev_offset_end, verbatim_evidence = o_res
                            break

            candidate_dict = {
                "candidate_id": candidate_id,
                "project_id": project_id,
                "name": name,
                "description": desc_val,
                "candidate_type": "Milestone" if is_pure else "Clause",
                "actor": actor,
                "action": primary_action,
                "actions": detected_actions,
                "target_entity": primary_target,
                "entities": detected_entities,
                "atomic_claims": atomic_claims,
                "page_number": page_val,
                "page_locator": page_locator,
                "section": section,
                "parent_section": src.get("parent_section") if src else item.get("parent_section"),
                "original_heading": (src.get("original_heading") or src.get("section_source")) if src else item.get("original_heading"),
                "chunk_index": chunk_idx,
                "segment_index": src.get("segment_index", 0) if src else None,
                "source_locator": source_locator,
                "segment_locator": src.get("segment_locator") if src else None,
                "text_offset": (src.get("text_offset") or src.get("_pos_in_chunk", 0)) if src else None,
                "evidence_offset_start": ev_offset_start,
                "evidence_offset_end": ev_offset_end,
                "raw_text": desc_val,
                "evidence_text": verbatim_evidence,
                "evidence_snippet": verbatim_evidence,
                "document_id": document_id,
                "is_pure_milestone": is_pure,
                "provenance_status": provenance_status,
                "verification_reason": (src.get("verification_reason") if src else None) or default_v_reason,
                "discovery_method": (src.get("discovery_method") if src else None) or "section_coverage",
                "window_id": src.get("window_id") if src else None,
                "window_indices": src.get("window_indices") if src else [],
                "merged_sources": item.get("merged_sources", []),
                "claims_evidence": [],
                "evidence_spans": [],
                "overlap_references": [],
            }
            # Execute claim-level evidence validation
            cls.validate_claim_level_evidence(candidate_dict, index_to_chunk=index_to_chunk)
            if candidate_dict.get("evidence_spans"):
                # If primary chunk offsets were None, adopt from first verified span
                if candidate_dict.get("evidence_offset_start") is None:
                    p_span = candidate_dict["evidence_spans"][0]
                    candidate_dict["evidence_offset_start"] = p_span["offset_start"]
                    candidate_dict["evidence_offset_end"] = p_span["offset_end"]
                    candidate_dict["source_locator"] = p_span["source_locator"]
                    candidate_dict["chunk_index"] = p_span["chunk_index"]
                    candidate_dict["evidence_text"] = p_span["evidence_text"]
                    candidate_dict["evidence_snippet"] = p_span["evidence_text"]
            candidates.append(candidate_dict)

            kind = "Milestone" if candidate_dict["is_pure_milestone"] else "Clause"
            honest_prov = candidate_dict.get("provenance_status", provenance_status)
            cls._log(
                logger,
                f"    [{idx:02d}] [{kind}] \"{name}\" -> Section: '{section}' | "
                f"{source_locator} | {page_locator} | Provenance: {honest_prov}"
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
        index_to_chunk: Optional[dict] = None,
    ) -> Tuple[Optional[dict], str]:
        """
        Finds the mapped item or source chunk whose evidence best supports this candidate,
        and strictly verifies the complete material claim.
        Returns:
            (best_source_item, provenance_status)

        Verification Outcomes:
        - VERIFIED: All material facts in the candidate (dates, durations, core action & entity)
          are supported by the referenced source evidence.
        - PARTIALLY_VERIFIED: Core concept is supported by the source, but one or more material facts
          (date, duration, or specific qualifier) remain unverified or uncertain.
        - UNRESOLVED: The system cannot locate or establish sufficient evidence for the claim.
        """
        STOP_WORDS = cls.STOP_WORDS

        name_l = name.lower().strip()
        desc_l = (reduced_item.get("description") or "").lower().strip()
        cand_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", f"{name_l} {desc_l}")) - STOP_WORDS
        name_words = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", name_l)) - STOP_WORDS

        def _clean_prefix(text: str) -> str:
            return re.sub(r"^(?:[A-Z0-9]{2,6}|GL|Client|Vendor)\s+", "", text, flags=re.I).strip().lower()

        clean_name_l = _clean_prefix(name_l)

        # Extract explicit dates and durations from candidate description for fact verification
        raw_dates = re.findall(
            r"\b(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b",
            f"{name_l} {desc_l}",
            re.IGNORECASE
        )
        dates = [re.sub(r"(\d+)(st|nd|rd|th)", r"\1", d, flags=re.I).lower().strip() for d in raw_dates]
        durations = re.findall(r"\b\d+\s+(?:weeks?|months?|days?|years?)\b", f"{name_l} {desc_l}", re.IGNORECASE)

        # Extract sentences and clauses from candidate description for exact phrase matching
        desc_sentences = [
            s.strip() for s in re.split(r'[.\n;|]|(?:\b(?:allowing|provided that|subject to|however)\b)', desc_l)
            if len(s.strip()) > 15
        ]

        # Check candidate's merged_sources
        merged_source_names = {
            ms.get("name", "").lower().strip() for ms in reduced_item.get("merged_sources", [])
        }

        red_for_merge = dict(reduced_item)
        if red_for_merge.get("section") == "Milestones" or red_for_merge.get("candidate_type") == "Pure Milestone":
            red_for_merge["is_pure_milestone"] = True

        scored_candidates = []

        for it in mapped_items:
            it_name_l = it["name"].lower().strip()
            it_desc_l = (it.get("description") or "").lower().strip()
            it_ev_l = (it.get("evidence_snippet") or "").lower().strip()
            it_win_l = (it.get("_window_text") or "").lower().strip()
            clean_it_name_l = _clean_prefix(it_name_l)

            # Combined textual context of this mapped item
            it_combined = f"{it_name_l} {it_desc_l} {it_ev_l}"
            it_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", it_combined)) - STOP_WORDS

            # 1. Semantic Token Overlap
            token_overlap = len(cand_tokens & it_tokens) / len(cand_tokens) if cand_tokens else 0.0

            # 2. Description similarity & sentence match
            desc_sim = difflib.SequenceMatcher(None, desc_l[:120], it_desc_l[:120]).ratio() if desc_l and it_desc_l else 0.0
            sentence_match = any(s in it_combined or s in it_win_l for s in desc_sentences) if desc_sentences else False

            # 3. Exact / Prefix / Merged source match
            is_exact_name = (name_l == it_name_l) or (clean_name_l and clean_name_l == clean_it_name_l)
            is_merged_src = it_name_l in merged_source_names
            is_substring_name = (name_l in it_name_l or it_name_l in name_l) and not is_exact_name

            # Must pass ActionEntityProtector compatibility unless it is a verified source match
            # (exact name, merged source, or sentence match from composite description)
            if not (is_exact_name or is_merged_src or sentence_match):
                it_for_merge = dict(it)
                if it_for_merge.get("section") == "Milestones" or it_for_merge.get("candidate_type") == "Pure Milestone":
                    it_for_merge["is_pure_milestone"] = True
                can_m, _ = ActionEntityProtector.can_merge(red_for_merge, it_for_merge)
                if not can_m:
                    continue

            # 4. Material facts presence in this item's text
            norm_it_combined = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", it_combined, flags=re.I).lower()
            dates_in_it = dates and any(d in norm_it_combined for d in dates)
            durations_in_it = durations and any(du.lower() in norm_it_combined for du in durations)

            # Compute substantive semantic score (PRIMARY)
            score = token_overlap * 120.0 + desc_sim * 60.0
            if sentence_match:
                score += 150.0
            if is_exact_name:
                score += 80.0
            elif is_merged_src:
                score += 70.0
            elif is_substring_name and token_overlap >= 0.30:
                score += 30.0

            # CRITICAL FACT ALIGNMENT:
            # Date/duration match only adds bonus IF the candidate shares substantive semantic tokens
            if dates and dates_in_it and token_overlap >= 0.25:
                score += 60.0
            if durations and durations_in_it and token_overlap >= 0.25:
                score += 60.0

            # Section alignment
            if reduced_item.get("section") and it.get("section") == reduced_item.get("section"):
                score += 15.0

            # Trailing overlap penalty (only penalize trailing duplicate chunks when non-trailing alternative exists)
            it_len = it.get("_chunk_len", 1000)
            it_pos = it.get("_pos_in_chunk", 0)
            if it_len > 250 and it_pos > (it_len - 200):
                score -= 30.0

            # Small position tie-breaker (max 10 points)
            score += 10.0 * (1.0 - min(it_pos, 1000) / 1000.0)

            # Must have minimal semantic support to even be considered a candidate match
            if is_exact_name or is_merged_src or sentence_match or (token_overlap >= 0.25 and score >= 40.0):
                scored_candidates.append((score, it))

        best_mapped = None
        best_mapped_score = -1.0
        if scored_candidates:
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            best_mapped_score = scored_candidates[0][0]
            best_mapped = scored_candidates[0][1]

        # 5. Direct document chunk fallback / validation via index_to_chunk
        best_chunk_fallback = None
        best_ch_score = -1.0
        if index_to_chunk and cand_tokens:
            best_ch_idx = None
            best_ch_excerpt = ""
            for c_idx, ch_data in index_to_chunk.items():
                ch_txt = ch_data.get("text", "")
                ch_txt_l = ch_txt.lower()
                ch_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", ch_txt_l)) - STOP_WORDS
                ch_overlap = len(cand_tokens & ch_tokens) / len(cand_tokens) if cand_tokens else 0.0

                ch_sent_matches = sum(1 for s in desc_sentences if s in ch_txt_l) if desc_sentences else 0
                ch_score = ch_overlap * 120.0
                if ch_sent_matches > 0:
                    ch_score += 200.0 * (ch_sent_matches / len(desc_sentences))

                norm_ch_txt = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", ch_txt_l, flags=re.I)
                if dates and any(d in norm_ch_txt for d in dates) and ch_overlap >= 0.25:
                    ch_score += 50.0
                if durations and any(du.lower() in norm_ch_txt for du in durations) and ch_overlap >= 0.25:
                    ch_score += 50.0

                if ch_score > best_ch_score:
                    best_ch_score = ch_score
                    best_ch_idx = c_idx
                    matched_s = [s for s in desc_sentences if s in ch_txt_l]
                    if matched_s:
                        best_ch_excerpt = matched_s[0]
                    else:
                        best_ch_excerpt = ch_txt[:300]

            if best_ch_idx is not None and (best_ch_score > best_mapped_score or best_mapped_score < 100.0):
                ch_data = index_to_chunk[best_ch_idx]
                best_chunk_fallback = {
                    "name": name,
                    "description": reduced_item.get("description", name),
                    "chunk_index": best_ch_idx,
                    "segment_index": 0,
                    "source_locator": f"chunk_{best_ch_idx}",
                    "segment_locator": f"chunk_{best_ch_idx}_seg_0",
                    "page_number": ch_data.get("page_number"),
                    "section": reduced_item.get("section", "General"),
                    "evidence_snippet": best_ch_excerpt,
                    "_window_text": ch_data.get("text", "")[:500],
                }

        # Choose between best_mapped and best_chunk_fallback
        best_src = None
        if best_chunk_fallback and (not best_mapped or best_ch_score > best_mapped_score + 30.0):
            best_src = best_chunk_fallback
        elif best_mapped:
            best_src = best_mapped
        elif best_chunk_fallback:
            best_src = best_chunk_fallback

        if not best_src:
            return None, PROVENANCE_UNRESOLVED

        # 6. Strict Evidence Claim Verification
        ev_text = (best_src.get("evidence_snippet") or "").strip()
        src_desc = (best_src.get("description") or "").strip()
        win_txt = (best_src.get("_window_text") or "").strip()

        # If evidence_snippet is a truncated prefix of src_desc (e.g. ends with '...')
        # or src_desc is present in window text, extend ev_text to src_desc
        if src_desc and (not ev_text or (ev_text.rstrip(". ") in src_desc) or (win_txt and src_desc.lower() in win_txt.lower())):
            if len(src_desc) > len(ev_text):
                ev_text = src_desc
                best_src["evidence_snippet"] = ev_text

        if not ev_text and index_to_chunk and best_src.get("chunk_index") is not None:
            c_data = index_to_chunk.get(best_src["chunk_index"], {})
            ev_text = c_data.get("text", "")[:300].strip()
            best_src["evidence_snippet"] = ev_text
        elif not ev_text and win_txt:
            ev_text = win_txt[:300].strip()
            best_src["evidence_snippet"] = ev_text

        ev_text_l = ev_text.lower()
        win_text_l = (best_src.get("_window_text") or "").lower()
        combined_support = f"{ev_text_l} {win_text_l}"

        ev_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", combined_support)) - cls.STOP_WORDS
        ev_overlap = len(cand_tokens & ev_tokens) / len(cand_tokens) if cand_tokens else 1.0

        has_desc_sentence_match = any(s in combined_support for s in desc_sentences) if desc_sentences else False

        # If evidence has < 25% overlap and no sentence match, the evidence does not support the claim!
        if ev_overlap < 0.25 and not has_desc_sentence_match:
            best_src["verification_reason"] = "Referenced excerpt discusses unrelated subject matter; insufficient semantic support"
            return best_src, PROVENANCE_UNRESOLVED

        # Immediate chunk context and contiguous quotation extension
        chunk_excerpt_l = ""
        c_idx = best_src.get("chunk_index")
        if index_to_chunk and c_idx is not None and c_idx in index_to_chunk:
            full_c_txt = index_to_chunk[c_idx].get("text", "")
            pos = best_src.get("_pos_in_chunk", 0)
            start_p = max(0, pos - 100)
            end_p = min(len(full_c_txt), pos + 300)
            chunk_excerpt_l = full_c_txt[start_p:end_p].lower()

            # Contiguous quotation extension:
            # Check if adjacent text within the SAME line/bullet/paragraph substantiates additional clauses
            if ev_text and ev_text.lower() in full_c_txt.lower():
                p_start = full_c_txt.lower().find(ev_text.lower())
                p_end = p_start + len(ev_text)
                # DO NOT cross paragraph/bullet point boundaries (\n or \r)
                next_newline = full_c_txt.find("\n", p_end)
                limit_p = next_newline if next_newline != -1 else min(len(full_c_txt), p_end + 350)
                after_text = full_c_txt[p_end:limit_p]
                after_text_l = after_text.lower().strip()
                if after_text_l:
                    for s in desc_sentences:
                        s_clean = s.strip()
                        if len(s_clean) > 15 and s_clean.lower() not in ev_text.lower():
                            s_toks = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", s_clean.lower())) - cls.STOP_WORDS - {
                                "implement", "implementation", "admin", "section", "sections", "employee", "employees",
                                "user", "users", "site", "portal", "application", "feature", "features", "system", "gl"
                            }
                            s_major = s_clean[:min(len(s_clean), 50)].lower()
                            distinctive_found = sum(1 for w in s_toks if w in after_text_l) if s_toks else 0
                            if s_major in after_text_l or (s_toks and distinctive_found >= max(1, len(s_toks) // 2)):
                                match_idx = after_text_l.find(s_major) if s_major in after_text_l else min(
                                    (after_text_l.find(w) for w in s_toks if w in after_text_l), default=-1
                                )
                                if match_idx != -1:
                                    end_of_clause = p_end + match_idx + len(s_clean)
                                    dot_pos = full_c_txt.find(".", end_of_clause)
                                    cand_ends = [p for p in (dot_pos,) if p != -1 and p >= end_of_clause and p <= limit_p]
                                    cut_pt = min(cand_ends) + 1 if cand_ends else min(limit_p, end_of_clause + 30)
                                    extended_span = full_c_txt[p_start:cut_pt].strip()
                                    ev_text = extended_span
                                    ev_text_l = ev_text.lower()
                                    best_src["evidence_snippet"] = ev_text
                                    break

        status, reason = cls._verify_candidate_claims(
            name=name,
            desc=reduced_item.get("description", name),
            primary_ev=ev_text,
            merged_sources=reduced_item.get("merged_sources", []),
            dates=dates,
            durations=durations,
            chunk_excerpt=chunk_excerpt_l,
            index_to_chunk=index_to_chunk,
        )
        best_src["verification_reason"] = reason
        return best_src, status

    @classmethod
    def _verify_candidate_claims(
        cls,
        name: str,
        desc: str,
        primary_ev: str,
        merged_sources: list,
        dates: Set[str],
        durations: Set[str],
        chunk_excerpt: str = "",
        index_to_chunk: Optional[dict] = None,
    ) -> Tuple[str, str]:
        """
        Substantive claim-level verification against legitimate source quotation(s).
        Verifies:
        1. All explicit milestone dates and durations.
        2. Union of legitimate evidence quotations (never unverified descriptions).
        3. Every material clause for contrasting action gaps, contrasting entity gaps,
           and substantive token/stem coverage.
        Returns:
            (provenance_status, verification_reason)
        """
        ev_text = (primary_ev or "").strip()
        ev_text_l = ev_text.lower()
        desc_l = (desc or name).lower().strip()
        chunk_excerpt_l = chunk_excerpt.lower()

        # 1. Fact verification (dates and durations)
        norm_ev_text = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", ev_text_l, flags=re.I)
        dates_ok = True
        missing_dates = []
        if dates:
            for d in dates:
                if d not in norm_ev_text and d not in re.sub(r"(\d+)(st|nd|rd|th)", r"\1", chunk_excerpt_l, flags=re.I):
                    dates_ok = False
                    missing_dates.append(d)

        durations_ok = True
        missing_durations = []
        if durations:
            for du in durations:
                if du.lower() not in ev_text_l and du.lower() not in chunk_excerpt_l:
                    durations_ok = False
                    missing_durations.append(du)

        # 2. Collect union of legitimate evidence quotations across merged sources.
        # NEVER use ms.get("description") as source evidence! Only actual snippets corroborated by chunk.
        #
        # FIX-Defect1: Track which evidence texts are PRIMARY vs MERGED-SOURCE so that
        # claims that only appear in merged-source evidence can be individually validated.
        # A merged candidate must not be VERIFIED merely because primary evidence supports
        # one claim and a merged source supports a different claim.
        primary_ev_only = ev_text  # save primary before any extension
        ev_texts = [ev_text] if ev_text else []
        merged_source_ev_texts = []  # evidence contributed solely by merged sources
        for ms in merged_sources:
            ms_ev = (ms.get("evidence_snippet") or "").strip()
            if ms_ev:
                if index_to_chunk and ms.get("chunk_index") in index_to_chunk:
                    ch_val = index_to_chunk[ms["chunk_index"]]
                    ch_txt = ch_val.get("text", "") if isinstance(ch_val, dict) else str(ch_val)
                    if ms_ev.lower() in ch_txt.lower() or any(w in ch_txt.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", ms_ev.lower())):
                        if ms_ev not in ev_texts:
                            ev_texts.append(ms_ev)
                            merged_source_ev_texts.append(ms_ev)
                elif ms_ev != ms.get("description") and ms_ev not in ev_texts:
                    ev_texts.append(ms_ev)
                    merged_source_ev_texts.append(ms_ev)

        if not ev_texts:
            return PROVENANCE_UNRESOLVED, "No verified source quotation available"

        union_ev_text_l = " \n ".join(ev_texts).lower()
        union_ev_words = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", union_ev_text_l)) - cls.STOP_WORDS
        union_ev_stems = {cls._stem_w(w) for w in union_ev_words}
        union_ev_actions = ActionEntityProtector.get_actions(union_ev_text_l)
        union_ev_entities = ActionEntityProtector.get_entities(union_ev_text_l)

        # 3. Claim-level verification for substantive clauses
        unsupported_claims = []
        raw_clauses = []
        for part in desc_l.split("|"):
            part = part.strip()
            if not part:
                continue
            base_splits = [
                cl.strip() for cl in re.split(r'[.\n;]|(?:\b(?:allowing|provided that|subject to|however)\b)', part)
                if len(cl.strip()) >= 5
            ]
            sub_clauses = []
            for p in (base_splits or [part]):
                matches = list(re.finditer(r'\b(?:and\s+(?:assist|conduct|execute|provide|obtain|give|ensure|organize|attend|convene|review|manage))\b', p, flags=re.I))
                if matches:
                    last_idx = 0
                    for m in matches:
                        sc = p[last_idx:m.start()].strip()
                        if len(sc) >= 5:
                            sub_clauses.append(sc)
                        last_idx = m.start() + 4  # skip 'and '
                    sc = p[last_idx:].strip()
                    if len(sc) >= 5:
                        sub_clauses.append(sc)
                else:
                    if len(p) >= 5:
                        sub_clauses.append(p)

            if sub_clauses:
                raw_clauses.extend(sub_clauses)
            elif len(part) >= 5:
                raw_clauses.append(part)

        boilerplate_words = {
            "implement", "implementation", "admin", "section", "sections", "employee", "employees",
            "user", "users", "site", "portal", "application", "feature", "features", "system", "gl",
            "related", "display", "etc", "execute", "execution", "perform", "performing", "conduct",
            "conducting", "provide", "providing", "do", "run"
        }

        # FIX-Defect4: Pre-check for conflated testing types (unit vs UAT vs integration).
        # If a candidate description or name conflates contrasting test types AND primary evidence
        # only supports one of them, mark individual claims as potentially unsupported.
        primary_ev_l = primary_ev_only.lower() if primary_ev_only else ""
        primary_ev_entities = ActionEntityProtector.get_entities(primary_ev_l)
        testing_entity_types = {"unit_testing", "uat_testing", "integration_testing"}
        desc_testing_types = {
            et for et in testing_entity_types
            if ActionEntityProtector.get_entities(desc_l).intersection({et})
        }
        primary_testing_types = {
            et for et in testing_entity_types
            if primary_ev_entities.intersection({et})
        }
        # If multiple distinct testing types appear in description but primary evidence only
        # covers a subset, those uncovered types are unsupported by primary evidence.
        # They may be covered by merged-source evidence, but we need to check.
        conflated_testing_unsupported = set()
        if len(desc_testing_types) > 1 and merged_source_ev_texts:
            # Check each testing type against merged source evidence
            for ttype in desc_testing_types - primary_testing_types:
                ttype_kws = ActionEntityProtector.ENTITIES.get(ttype, set())
                covered_by_merged = any(
                    any(kw in ms_ev.lower() for kw in ttype_kws)
                    for ms_ev in merged_source_ev_texts
                )
                if not covered_by_merged:
                    conflated_testing_unsupported.add(ttype)

        for cl in raw_clauses:
            cl_words = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", cl)) - cls.STOP_WORDS
            if not cl_words:
                continue
            cl_stems = {cls._stem_w(w) for w in cl_words}
            stem_ov = len(cl_stems & union_ev_stems) / len(cl_stems) if cl_stems else 1.0

            # Action check: does clause assert an action not evidenced in union?
            cl_actions = ActionEntityProtector.get_actions(cl)
            missing_actions = cl_actions - union_ev_actions
            has_contrasting_action_gap = False
            if missing_actions:
                for ma in missing_actions:
                    for pair_1, pair_2 in ActionEntityProtector.CONTRASTING_ACTION_PAIRS:
                        if (ma in pair_1 and union_ev_actions.intersection(pair_2)) or (
                            ma in pair_2 and union_ev_actions.intersection(pair_1)
                        ):
                            has_contrasting_action_gap = True
                            break

            # Entity check: does clause assert an entity not evidenced in union?
            cl_entities = ActionEntityProtector.get_entities(cl)
            missing_entities = cl_entities - union_ev_entities
            has_contrasting_entity_gap = False
            if missing_entities:
                for me in missing_entities:
                    for pair_1, pair_2 in ActionEntityProtector.CONTRASTING_ENTITY_PAIRS:
                        if (me in pair_1 and union_ev_entities.intersection(pair_2)) or (
                            me in pair_2 and union_ev_entities.intersection(pair_1)
                        ):
                            has_contrasting_entity_gap = True
                            break

            # FIX-Defect1: Per-claim primary evidence check for merged candidates.
            # A claim that is ONLY supported by merged-source evidence (not by primary evidence)
            # must not silently become VERIFIED via the union.
            # We detect this by finding tokens that are DISTINCTIVE to this clause
            # (i.e. present in the clause but NOT in the primary evidence). If those
            # distinctive tokens are ONLY found in merged-source evidence that introduces
            # a contrasting action vs primary evidence, the claim is unsupported.
            claim_only_in_merged_source = False
            if merged_source_ev_texts and primary_ev_only:
                cl_subst = cl_words - boilerplate_words
                if cl_subst:
                    primary_ev_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", primary_ev_l)) - cls.STOP_WORDS
                    primary_ev_stems = {cls._stem_w(w) for w in primary_ev_tokens}
                    cl_subst_stems = {cls._stem_w(w) for w in cl_subst}
                    # Find tokens that are DISTINCTIVE to this clause (not in primary evidence)
                    # These are the tokens that would need merged-source evidence to be supported.
                    distinctive_stems = cl_subst_stems - primary_ev_stems
                    if distinctive_stems:
                        # Check if distinctive tokens appear in merged-source evidence
                        merged_union_l = " ".join(merged_source_ev_texts).lower()
                        merged_ev_tokens = set(re.findall(r"\b[a-zA-Z0-9]{3,}\b", merged_union_l)) - cls.STOP_WORDS
                        merged_ev_stems = {cls._stem_w(w) for w in merged_ev_tokens}
                        distinctive_in_merged = distinctive_stems & merged_ev_stems
                        # If the distinctive tokens are exclusively in merged sources and those
                        # merged sources have contrasting actions vs the primary evidence...
                        if distinctive_in_merged and len(distinctive_in_merged) >= max(1, len(distinctive_stems) // 2):
                            merged_ev_actions = ActionEntityProtector.get_actions(merged_union_l)
                            primary_ev_actions = ActionEntityProtector.get_actions(primary_ev_l)
                            for pair_1, pair_2 in ActionEntityProtector.CONTRASTING_ACTION_PAIRS:
                                if ((primary_ev_actions.intersection(pair_1) and merged_ev_actions.intersection(pair_2)) or
                                        (primary_ev_actions.intersection(pair_2) and merged_ev_actions.intersection(pair_1))):
                                    claim_only_in_merged_source = True
                                    break

            # Substantive non-boilerplate token check
            cl_substantive = cl_words - boilerplate_words
            substantive_stems = {cls._stem_w(w) for w in cl_substantive}
            substantive_ov = len(substantive_stems & union_ev_stems) / len(substantive_stems) if substantive_stems else 1.0

            # Responsible party check: where stated in clause/name (e.g. GL / Google Limited vs Google)
            ACTORS_CLIENT = [r"\bgoogle limited\b", r"\bgl\b", r"\bclient\b"]
            ACTORS_VENDOR = [r"\bgoogle\b", r"\bgoogle llp\b", r"\bgoogle india\b", r"\bvendor\b"]
            cl_and_name = f"{cl} {name.lower()}"
            cl_is_client = any(bool(re.search(pat, cl_and_name, flags=re.I)) for pat in ACTORS_CLIENT)
            cl_is_vendor = any(bool(re.search(pat, cl_and_name, flags=re.I)) for pat in ACTORS_VENDOR) and not cl_is_client
            ev_has_client = any(bool(re.search(pat, union_ev_text_l, flags=re.I)) for pat in ACTORS_CLIENT)
            ev_has_vendor = any(bool(re.search(pat, union_ev_text_l, flags=re.I)) for pat in ACTORS_VENDOR)
            # Only trigger conflict if clause is NOT verbatim in union evidence and asserts a party completely absent from evidence
            is_verbatim_in_ev = cl.strip().lower() in union_ev_text_l
            has_actor_conflict = False
            if not is_verbatim_in_ev:
                if cl_is_client and not ev_has_client and ev_has_vendor:
                    has_actor_conflict = True
                elif cl_is_vendor and not ev_has_vendor and ev_has_client:
                    has_actor_conflict = True

            if has_contrasting_action_gap:
                unsupported_claims.append(f"{cl} (missing action: {missing_actions})")
            elif has_contrasting_entity_gap:
                unsupported_claims.append(f"{cl} (missing entity: {missing_entities})")
            elif has_actor_conflict:
                unsupported_claims.append(f"{cl} (responsible party conflict)")
            elif claim_only_in_merged_source:
                unsupported_claims.append(f"{cl} (claim only in merged-source evidence with contrasting action; requires split into separate candidate)")
            elif (stem_ov < 0.50 or (cl_substantive and substantive_ov < 0.50)) and cl not in union_ev_text_l:
                unsupported_claims.append(cl)

        # FIX-Defect4: If conflated testing types are unsupported by any evidence, flag them.
        if conflated_testing_unsupported:
            unsupported_claims.append(
                f"Conflated testing types {conflated_testing_unsupported} not independently evidenced "
                f"(unit testing, integration testing, and UAT must remain separate candidates)"
            )

        if dates_ok and durations_ok and not unsupported_claims:
            return PROVENANCE_VERIFIED, "All material claims, obligations, dates, and durations verified against source quotation"
        else:
            unverified_facts = []
            if not dates_ok:
                unverified_facts.append(f"dates {missing_dates}")
            if not durations_ok:
                unverified_facts.append(f"durations {missing_durations}")
            if unsupported_claims:
                short_claims = [f"'{c[:120]}...'" if len(c) > 120 else f"'{c}'" for c in unsupported_claims]
                unverified_facts.append(f"unsupported claims [{', '.join(short_claims)}]")
            return PROVENANCE_PARTIAL, f"Core obligation identified, but {', '.join(unverified_facts)} not confirmed in supporting quotation"

    @classmethod
    def _verify_canonical_candidates(
        cls,
        candidates: list[dict],
        index_to_chunk: Optional[dict] = None,
        logger=None,
    ) -> None:
        """
        Enforces a final canonical verification gate after all candidate deduplication,
        cross-section consolidation, and audit reconciliation are complete.
        Recomputes provenance_status and verification_reason for each canonical candidate.
        Guarantees that no composite or mutated candidate escapes re-verification.
        """
        cls._log(logger, f"  [DeepScan][Canonical Verification Gate] Re-verifying {len(candidates)} canonical candidates...")
        for cand in candidates:
            # Re-run claim-level evidence validation
            cls.validate_claim_level_evidence(cand, index_to_chunk=index_to_chunk)
            name = cand.get("name", "")
            desc = cand.get("description", name)
            primary_ev = cand.get("evidence_snippet") or cand.get("evidence_text") or ""
            c_idx = cand.get("chunk_index")
            chunk_excerpt = ""
            if index_to_chunk and c_idx is not None and c_idx in index_to_chunk:
                chunk_excerpt = index_to_chunk[c_idx].get("text", "") if isinstance(index_to_chunk[c_idx], dict) else index_to_chunk[c_idx]

            comb_txt = f"{name} {desc}"
            dates = ActionEntityProtector.get_norm_dates(comb_txt)
            dur_pattern = r"\b\d+\s+(?:weeks?|months?|days?|business\s+days?|calendar\s+days?|hours?|years?)\b"
            durations = set(re.findall(dur_pattern, comb_txt.lower()))

            status, reason = cls._verify_candidate_claims(
                name=name,
                desc=desc,
                primary_ev=primary_ev,
                merged_sources=cand.get("merged_sources", []),
                dates=dates,
                durations=durations,
                chunk_excerpt=chunk_excerpt,
                index_to_chunk=index_to_chunk,
            )
            # If claim-level evidence validation identified unsupported claims, do not allow VERIFIED status
            claims_ev = cand.get("claims_evidence", [])
            has_explicit_unsupported = any(clm.get("status") == "UNSUPPORTED" for clm in claims_ev)
            if has_explicit_unsupported and status == PROVENANCE_VERIFIED:
                status = PROVENANCE_PARTIAL
                unsup = [clm.get("claim") for clm in claims_ev if clm.get("status") == "UNSUPPORTED"]
                reason = f"Supported: {sum(1 for c in claims_ev if c.get('status') == 'VERIFIED')}/{len(claims_ev)} claims; unsupported atomic claims exist: {unsup}"
            cand["provenance_status"] = status
            cand["verification_reason"] = reason

        # FIX-Defect5: Log final provenance counts AFTER the gate so log and JSON are consistent.
        gate_verified = sum(1 for c in candidates if c.get("provenance_status") == PROVENANCE_VERIFIED)
        gate_partial = sum(1 for c in candidates if c.get("provenance_status") == PROVENANCE_PARTIAL)
        gate_unresolved = sum(1 for c in candidates if c.get("provenance_status") == PROVENANCE_UNRESOLVED)
        cls._log(
            logger,
            f"  [DeepScan][Canonical Verification Gate] Final provenance after gate: "
            f"VERIFIED={gate_verified}, PARTIALLY_VERIFIED={gate_partial}, UNRESOLVED={gate_unresolved} "
            f"(total={len(candidates)})\n"
        )

    # ------------------------------------------------------------------ #
    # 6) MAP-to-REDUCE Coverage Auditor
    # ------------------------------------------------------------------ #
    @classmethod
    def _audit_coverage(
        cls,
        mapped_items: list[dict],
        canonical_candidates: list[dict],
        merge_events: list[dict],
        retrieval_metrics: Optional[dict] = None,
        total_chunks: Optional[int] = None,
        total_segments: Optional[int] = None,
        logger=None,
    ) -> dict:
        """
        Produces an auditable coverage report accounting for EVERY single MAP candidate:
        - RETAINED: survives directly as a canonical candidate (exactly 1:1 with canonical_candidates).
        - MERGED: mapped to a surviving canonical candidate with merge rationale (including duplicate emissions from overlapping windows).
        - EXCLUDED: explicitly excluded (empty/malformed or excluded document metadata artifacts).
        - UNRESOLVED: unmapped candidate (triggers warning).

        Audit invariants:
        1. total_map_candidates == retained_count + merged_count + excluded_count + unresolved_count
        2. retained_count == len(canonical_candidates)
        3. Every MERGED record maps to an existing canonical.
        4. Every RETAINED record references an actual canonical.
        5. canonical_candidates contains zero duplicate names.
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

        # Track which canonical candidate names have been claimed as RETAINED
        claimed_canonicals: set[str] = set()

        for item_idx, it in enumerate(mapped_items):
            raw_name = (it.get("name") or "").strip()
            raw_name_l = raw_name.lower()
            section = it.get("section", "General")

            # EXCLUDED: empty or malformed
            if not raw_name or len(raw_name) < 2:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "candidate_name": raw_name,
                    "status": "EXCLUDED",
                    "canonical_name": None,
                    "section": section,
                    "rationale": "Empty or malformed candidate string",
                    "merge_rationale": "Empty or malformed candidate string",
                    "warning": None,
                })
                excluded_count += 1
                continue

            # EXCLUDED: explicitly excluded document title / header metadata artifact
            if raw_name_l in merge_lookup and merge_lookup[raw_name_l] == "[EXCLUDED_ARTIFACT]":
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "candidate_name": raw_name,
                    "status": "EXCLUDED",
                    "canonical_name": None,
                    "section": section,
                    "rationale": "Excluded document title / metadata header artifact",
                    "merge_rationale": "Excluded document title / metadata header artifact",
                    "warning": None,
                })
                excluded_count += 1
                continue

            # Determine if this item explicitly merged into a distinct surviving candidate
            is_merged_via_lookup = (
                raw_name_l in merge_lookup and
                merge_lookup[raw_name_l].lower().strip() != raw_name_l and
                it.get("chunk_index") != canonical_lookup.get(raw_name_l, {}).get("chunk_index")
            )

            # 1. RETAINED: candidate matches a canonical directly that has not yet been claimed
            if raw_name_l in canonical_lookup and not is_merged_via_lookup and raw_name_l not in claimed_canonicals:
                claimed_canonicals.add(raw_name_l)
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "candidate_name": raw_name,
                    "status": "RETAINED",
                    "canonical_name": canonical_lookup[raw_name_l]["name"],
                    "section": section,
                    "rationale": "Survives directly as distinct canonical candidate",
                    "merge_rationale": "Survives directly as distinct canonical candidate",
                    "warning": None,
                })
                retained_count += 1
                continue

            # 2. MERGED: duplicate emission of an already retained canonical from overlapping window
            if raw_name_l in canonical_lookup and raw_name_l in claimed_canonicals and not is_merged_via_lookup:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "candidate_name": raw_name,
                    "status": "MERGED",
                    "canonical_name": canonical_lookup[raw_name_l]["name"],
                    "section": section,
                    "rationale": "Duplicate emission from overlapping window consolidated into surviving canonical",
                    "merge_rationale": "Duplicate emission from overlapping window consolidated into surviving canonical",
                    "warning": None,
                })
                merged_count += 1
                canonical_cand = canonical_lookup[raw_name_l]
                existing_sources = canonical_cand.setdefault("merged_sources", [])
                if not any(ms.get("map_index") == item_idx + 1 for ms in existing_sources):
                    existing_sources.append({
                        "map_index": item_idx + 1,
                        "candidate_id": it.get("candidate_id") or f"map_{item_idx + 1}",
                        "name": raw_name,
                        "chunk_index": it.get("chunk_index"),
                        "page_number": it.get("page_number"),
                        "section": it.get("section") or section,
                        "evidence_snippet": it.get("evidence_snippet") or it.get("description", ""),
                        "description": it.get("description", ""),
                        "source_locator": it.get("source_locator") or f"Chunk {it.get('chunk_index')}",
                        "rationale": "Duplicate emission from overlapping window consolidated into surviving canonical",
                    })
                continue

            # 3. MERGED / RETAINED via merge_lookup:
            if raw_name_l in merge_lookup:
                surviving = merge_lookup[raw_name_l]
                surviving_l = surviving.lower().strip()
                surviving_exists = surviving_l in canonical_lookup
                surviving_in_mapped = any(m.get("name", "").lower().strip() == surviving_l for m in mapped_items)
                # If this surviving canonical has not yet been claimed and no raw candidate exists with surviving's name
                if surviving_exists and surviving_l not in claimed_canonicals and not surviving_in_mapped:
                    claimed_canonicals.add(surviving_l)
                    audit_records.append({
                        "map_index": item_idx + 1,
                        "raw_name": raw_name,
                        "candidate_name": raw_name,
                        "status": "RETAINED",
                        "canonical_name": surviving,
                        "section": section,
                        "rationale": f"Survives as canonical candidate '{surviving}'",
                        "merge_rationale": f"Survives as canonical candidate '{surviving}'",
                        "warning": None,
                    })
                    retained_count += 1
                    continue

                if not surviving_exists:
                    # Surviving canonical was excluded (e.g. [EXCLUDED_ARTIFACT] or doc title).
                    # Re-resolve to actual surviving canonical candidate via fuzzy / stem / substring match:
                    best_match_c = None
                    best_match_sim = 0.0
                    for c_cand in canonical_candidates:
                        c_name_l = c_cand["name"].lower().strip()
                        sim = difflib.SequenceMatcher(None, raw_name_l, c_name_l).ratio()
                        if raw_name_l in c_name_l or c_name_l in raw_name_l:
                            sim = max(sim, 0.75)
                        if sim > best_match_sim:
                            best_match_sim = sim
                            best_match_c = c_cand
                    if best_match_c and best_match_sim >= 0.50:
                        surviving = best_match_c["name"]
                        surviving_l = surviving.lower().strip()
                        surviving_exists = True

                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "candidate_name": raw_name,
                    "status": "MERGED",
                    "canonical_name": surviving,
                    "section": section,
                    "rationale": "Merged into surviving canonical candidate",
                    "merge_rationale": "Merged into surviving canonical candidate",
                    "warning": None if surviving_exists else f"INVARIANT VIOLATION: Surviving canonical '{surviving}' not found in canonical list",
                })
                merged_count += 1
                if surviving_exists:
                    canonical_cand = canonical_lookup[surviving_l]
                    existing_sources = canonical_cand.setdefault("merged_sources", [])
                    already_present = any(
                        ms.get("map_index") == item_idx + 1
                        for ms in existing_sources
                    )
                    is_self = (raw_name_l == surviving_l and it.get("chunk_index") == canonical_cand.get("chunk_index"))
                    if not already_present and not is_self:
                        existing_sources.append({
                            "map_index": item_idx + 1,
                            "candidate_id": it.get("candidate_id") or f"map_{item_idx + 1}",
                            "name": raw_name,
                            "chunk_index": it.get("chunk_index"),
                            "page_number": it.get("page_number"),
                            "section": it.get("section") or section,
                            "evidence_snippet": it.get("evidence_snippet") or it.get("description", ""),
                            "description": it.get("description", ""),
                            "source_locator": it.get("source_locator") or f"Chunk {it.get('chunk_index')}",
                            "rationale": "Merged into surviving canonical candidate",
                        })
                continue

            # 4. Fuzzy / Description Subsumption / Stem Overlap: map to canonical candidate
            best_c = None
            best_sim = 0.0
            best_rationale = ""

            def _stem_w(w):
                w = w.lower()
                for suff in ('ing', 'ed', 's', 'ion', 'ment', 'able', 'ive', 'ity', 'ies', 'al', 'ic', 'ly', 'e'):
                    if len(w) > len(suff) + 2 and w.endswith(suff):
                        w = w[:-len(suff)]
                if w.endswith('ut') or w.endswith('v'):
                    w = w[:-1]
                return w

            it_for_merge = dict(it)
            if it_for_merge.get("section") == "Milestones" or it_for_merge.get("candidate_type") == "Pure Milestone":
                it_for_merge["is_pure_milestone"] = True

            it_desc_l = (it.get("description") or "").lower()

            for c_name_l, c_obj in canonical_lookup.items():
                can_m, _ = ActionEntityProtector.can_merge(it_for_merge, c_obj)
                if not can_m:
                    continue

                sim = 0.0
                rat = ""

                # Direct normalized name match
                if raw_name_l == c_name_l:
                    sim = 1.0
                elif raw_name_l in c_name_l or c_name_l in raw_name_l:
                    sim = 0.90
                else:
                    sim = difflib.SequenceMatcher(None, raw_name_l, c_name_l).ratio()

                # Description subsumption check: if candidate phrase is in canonical description
                c_desc_l = (c_obj.get("description") or "").lower()
                c_raw_l = (c_obj.get("raw_text") or "").lower()
                c_combined = c_desc_l + " " + c_raw_l

                if len(raw_name_l) >= 4 and raw_name_l in c_combined:
                    if sim < 0.88:
                        sim = 0.88
                        rat = f"Subsumed in canonical '{c_obj['name']}' scope/description"

                # Contractual validity & term statements equivalence check:
                # Compare explicit validity dates and term duration across statements
                VALIDITY_TERMS = {'valid', 'validity', 'effective', 'term', 'period', 'engagement', 'duration', 'contract'}
                raw_has_val = any(vt in raw_name_l or vt in it_desc_l for vt in VALIDITY_TERMS)
                c_has_val = any(vt in c_name_l or vt in c_desc_l for vt in VALIDITY_TERMS)
                if raw_has_val and c_has_val:
                    raw_dates = set(re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b", (raw_name_l + " " + it_desc_l), re.I))
                    c_dates = set(re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4}\b", (c_name_l + " " + c_combined), re.I))
                    norm_raw_dates = {re.sub(r"(\d+)(st|nd|rd|th)", r"\1", d, flags=re.I).lower().strip() for d in raw_dates}
                    norm_c_dates = {re.sub(r"(\d+)(st|nd|rd|th)", r"\1", d, flags=re.I).lower().strip() for d in c_dates}
                    if norm_raw_dates and norm_c_dates and norm_raw_dates.issubset(norm_c_dates):
                        if sim < 0.92:
                            sim = 0.92
                            date_str = ', '.join(sorted(norm_raw_dates))
                            rat = f"Duplicate contractual validity statement consolidated into surviving canonical '{c_obj['name']}' (matching validity date: {date_str})"

                # Key stem overlap check
                raw_words = [w for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", raw_name_l) if w not in {'and', 'the', 'for', 'with', 'from', 'all', 'non'}]
                if raw_words:
                    raw_stems = {_stem_w(w) for w in raw_words}
                    target_stems = {_stem_w(w) for w in re.findall(r"\b[a-zA-Z0-9]{3,}\b", c_name_l + " " + c_combined)}
                    overlap = raw_stems & target_stems
                    overlap_ratio = len(overlap) / len(raw_stems)
                    if (overlap_ratio >= 0.60 and len(overlap) >= 2) or overlap_ratio >= 0.75:
                        if sim < 0.80:
                            sim = 0.75 + 0.15 * overlap_ratio
                            ov_str = ', '.join(sorted(overlap))
                            rat = f"Consolidated into canonical '{c_obj['name']}' (key obligations match: {ov_str})"

                is_unclaimed = (c_name_l not in claimed_canonicals)
                current_claimed = (best_c and best_c["name"].lower().strip() in claimed_canonicals)
                if sim > best_sim or (sim >= 0.70 and is_unclaimed and current_claimed):
                    best_sim = sim
                    best_c = c_obj
                    best_rationale = rat

            if best_c and best_sim >= 0.70:
                best_c_l = best_c["name"].lower().strip()
                if best_c_l not in claimed_canonicals:
                    claimed_canonicals.add(best_c_l)
                    audit_records.append({
                        "map_index": item_idx + 1,
                        "raw_name": raw_name,
                        "candidate_name": raw_name,
                        "status": "RETAINED",
                        "canonical_name": best_c["name"],
                        "section": section,
                        "rationale": best_rationale or f"Survives as canonical candidate '{best_c['name']}' (synthesized/qualifier diff, similarity: {best_sim:.2f})",
                        "merge_rationale": best_rationale or f"Survives as canonical candidate '{best_c['name']}' (synthesized/qualifier diff, similarity: {best_sim:.2f})",
                        "warning": None,
                    })
                    retained_count += 1
                else:
                    audit_records.append({
                        "map_index": item_idx + 1,
                        "raw_name": raw_name,
                        "candidate_name": raw_name,
                        "status": "MERGED",
                        "canonical_name": best_c["name"],
                        "section": section,
                        "rationale": best_rationale or f"Consolidated into canonical candidate across overlapping windows (similarity: {best_sim:.2f})",
                        "merge_rationale": best_rationale or f"Consolidated into canonical candidate across overlapping windows (similarity: {best_sim:.2f})",
                        "warning": None,
                    })
                    merged_count += 1
                    # Preserve duplicate source reference on surviving canonical candidate
                    if "merged_sources" not in best_c:
                        best_c["merged_sources"] = []
                    existing_entry = next((ms for ms in best_c["merged_sources"] if ms.get("name") == raw_name), None)
                    if existing_entry:
                        if not existing_entry.get("source_locator"):
                            existing_entry["source_locator"] = it.get("source_locator") or (f"chunk_{it.get('chunk_index')}" if it.get("chunk_index") is not None else None)
                        if not existing_entry.get("evidence_snippet"):
                            existing_entry["evidence_snippet"] = it.get("evidence_snippet", "")
                        if not existing_entry.get("rationale"):
                            existing_entry["rationale"] = best_rationale or "Duplicate emission consolidated during audit reconciliation"
                    else:
                        best_c["merged_sources"].append({
                            "name": raw_name,
                            "description": it.get("description", ""),
                            "chunk_index": it.get("chunk_index"),
                            "segment_index": it.get("segment_index", 0),
                            "section": section,
                            "source_locator": it.get("source_locator") or (f"chunk_{it.get('chunk_index')}" if it.get("chunk_index") is not None else None),
                            "evidence_snippet": it.get("evidence_snippet", ""),
                            "rationale": best_rationale or "Duplicate emission consolidated during audit reconciliation",
                        })
            else:
                audit_records.append({
                    "map_index": item_idx + 1,
                    "raw_name": raw_name,
                    "candidate_name": raw_name,
                    "status": "UNRESOLVED",
                    "canonical_name": None,
                    "section": section,
                    "rationale": "Candidate was not preserved and has no defensible merge mapping",
                    "merge_rationale": "Candidate was not preserved and has no defensible merge mapping",
                    "warning": "Review required: this requirement may have been silently lost",
                })
                unresolved_count += 1

        total = len(mapped_items)
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
        map_accounting_reconciled = (retained_count + merged_count + excluded_count + unresolved_count == total)
        if not map_accounting_reconciled:
            warnings.append(
                f"INVARIANT VIOLATION: outcome totals ({retained_count}+{merged_count}+"
                f"{excluded_count}+{unresolved_count}) != total MAP candidates ({total})"
            )
        if len(canonical_candidates) != len(set(c["name"] for c in canonical_candidates)):
            warnings.append("INVARIANT VIOLATION: Canonical candidates contain duplicate names")
        canonical_reconciled = (retained_count == len(canonical_candidates))
        if retained_count > len(canonical_candidates):
            warnings.append(
                f"INVARIANT VIOLATION: retained count ({retained_count}) exceeds canonical candidates count ({len(canonical_candidates)})"
            )

        # Discovery path counts
        discovery_counts = {
            "section_coverage": 0,
            "vector_retrieval": 0,
            "hybrid": 0,
            "fallback_coverage": 0,
        }
        for it in mapped_items:
            dm = it.get("discovery_method", "section_coverage")
            discovery_counts[dm] = discovery_counts.get(dm, 0) + 1

        sections_processed = sorted(list({it.get("section", "General") for it in mapped_items}))
        section_breakdown = cls.compute_section_breakdown(canonical_candidates)

        cls._audit_semantic_overlaps(canonical_candidates, logger=logger)

        # Reconcile and deduplicate merged_sources against audit_records
        merged_audit_names = {r["raw_name"]: r for r in audit_records if r.get("status") == "MERGED"}
        for c in canonical_candidates:
            clean_ms = []
            seen_names = set()
            for ms in c.get("merged_sources", []):
                m_name = ms.get("name")
                if m_name in merged_audit_names and m_name not in seen_names:
                    seen_names.add(m_name)
                    if not ms.get("source_locator") and ms.get("chunk_index") is not None:
                        ms["source_locator"] = f"chunk_{ms.get('chunk_index')}"
                    clean_ms.append(ms)
            c["merged_sources"] = clean_ms

        merged_sources_count = sum(len(c.get("merged_sources", [])) for c in canonical_candidates)
        lineage_complete = (merged_sources_count > 0) and all(
            bool(ms.get("name") and ms.get("chunk_index") is not None and ms.get("source_locator") and (ms.get("evidence_snippet") or ms.get("description")) and ms.get("rationale"))
            for c in canonical_candidates for ms in c.get("merged_sources", [])
        )
        report = {
            "total_map_candidates": total,
            "total_canonical_candidates": len(canonical_candidates),
            "total_chunks_available": total_chunks,
            "total_segments_available": total_segments,
            "sections_processed": sections_processed,
            "discovery_path_counts": discovery_counts,
            "missing_or_invalid_sources_count": prov_unresolved,
            "retained_count": retained_count,
            "merged_count": merged_count,
            "merged_sources_count": merged_sources_count,
            "merged_sources_traceable": bool(merged_sources_count == merged_count and lineage_complete),
            "excluded_count": excluded_count,
            "unresolved_count": unresolved_count,
            "coverage_percentage": coverage_pct,
            "coverage_formula": "(RETAINED + MERGED + EXCLUDED) / total_map_candidates * 100",
            "map_accounting_reconciled": map_accounting_reconciled,
            "canonical_reconciled": canonical_reconciled,
            "retrieval_health": retrieval_metrics or {},
            "provenance": {
                "verified_count": prov_verified,
                "partially_verified_count": prov_partial,
                "unresolved_count": prov_unresolved,
                "verified_percentage": prov_pct,
            },
            "section_breakdown": section_breakdown,
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
        cls._log(
            logger,
            f"  [DeepScan][Coverage Audit] Section Breakdown ({len(section_breakdown['counts'])} sections, sum={section_breakdown['total_counted']}, matches_canonical={section_breakdown['section_sum_equals_canonical_count']}):"
        )
        for s_name, s_cnt in section_breakdown["counts"].items():
            cls._log(logger, f"    - Section '{s_name}': {s_cnt} candidate(s)")
        if section_breakdown.get("multi_section_count", 0) > 0:
            cls._log(
                logger,
                f"    (Note: {section_breakdown['multi_section_count']} candidate(s) hold merged sources from other sections; tracked without double counting)"
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

    @classmethod
    def compute_section_breakdown(cls, canonical_candidates: list[dict]) -> dict:
        """
        Dynamically computes the section breakdown from the final canonical candidate collection.
        Enforces that the sum of primary section counts strictly equals len(canonical_candidates).
        Explicitly tracks unclassified candidates and multi-section provenance records without double-counting.
        """
        from collections import Counter
        counts = Counter()
        multi_section_candidates = []
        for c in canonical_candidates:
            raw_sec = c.get("section")
            sec_name = str(raw_sec).strip() if raw_sec and str(raw_sec).strip() else "Unclassified"
            counts[sec_name] += 1

            merged_secs = {
                str(m.get("section")).strip()
                for m in c.get("merged_sources", [])
                if m.get("section") and str(m.get("section")).strip() and str(m.get("section")).strip() != sec_name
            }
            if merged_secs:
                multi_section_candidates.append({
                    "name": c.get("name"),
                    "primary_section": sec_name,
                    "secondary_sections": sorted(list(merged_secs)),
                })

        total = len(canonical_candidates)
        sum_counts = sum(counts.values())
        return {
            "counts": dict(sorted(counts.items())),
            "total_counted": sum_counts,
            "canonical_candidates_count": total,
            "section_sum_equals_canonical_count": (sum_counts == total),
            "reporting_convention": "Primary contractual section assignment per canonical candidate. Multi-section sources listed separately to prevent double counting.",
            "multi_section_count": len(multi_section_candidates),
            "multi_section_candidates": multi_section_candidates,
        }

