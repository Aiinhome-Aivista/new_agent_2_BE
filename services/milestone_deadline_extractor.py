import re
from datetime import datetime
from services.llm_service import LLMService

class MilestoneDeadlineExtractor:

    DATE_PATTERNS = [
        r"(?i)(?:completed\s+by|before|after|scheduled\s+for|due\s+on|delivery\s+date|target\s+date|completion\s+date|acceptance\s+date|\bby\b|\bon\b)\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|[0-9]{1,2}\s+[A-Za-z]+|[A-Za-z]+\s+[0-9]{1,2}(?:,\s+[0-9]{4})?|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|Q[1-4]\s+[0-9]{4}|End of [A-Za-z]+)"
    ]

    MILESTONE_KEYWORDS = ["UAT", "Deployment", "Go Live", "Training", "Knowledge Transfer", "milestone"]

    # >>> NEW: month-name lookup so _normalize_date can handle "15 April 2026",
    # "Apr 15, 2026" etc. without needing an external dateutil dependency.
    MONTHS = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    @classmethod
    def extract(cls, candidates: list[dict]) -> list[dict]:
        """
        Enriches scope items with milestone and deadline information.
        Must run AFTER deduplication.
        """
        for candidate in candidates:
            evidence = candidate.get("evidence_text", "")
            desc = candidate.get("description", "")
            combined_text = f"{desc} {evidence}"

            # Default values (Issue 7: NO SCHEDULE INFORMATION)
            candidate["milestone"] = None
            candidate["deadline_text"] = None
            candidate["deadline"] = None
            candidate["extraction_method"] = None
            candidate["extraction_confidence"] = None

            # 1. Deterministic Extraction (Issue 3 & 6: Inline Deadlines)
            found_date_text = None
            found_milestone = None
            candidate_name_lower = candidate.get("name", "").lower()

            sentences = re.split(r'\.\s+', combined_text)
            for sentence in sentences:
                has_item = candidate_name_lower in sentence.lower()
                has_mk = any(mk.lower() in sentence.lower() for mk in cls.MILESTONE_KEYWORDS)

                # If we have multiple sentences and this one is totally unrelated, skip it
                if len(sentences) > 1 and not (has_item or has_mk):
                    continue

                for pattern in cls.DATE_PATTERNS:
                    match = re.search(pattern, sentence)
                    if match:
                        found_date_text = match.group(1).strip()
                        if has_item:
                            found_milestone = candidate.get("name")
                        else:
                            for mk in cls.MILESTONE_KEYWORDS:
                                if mk.lower() in sentence.lower():
                                    found_milestone = mk
                                    break
                        if not found_milestone:
                            found_milestone = candidate.get("name")
                        break
                if found_date_text:
                    break

            if found_date_text:
                candidate["deadline_text"] = found_date_text
                candidate["deadline"] = cls._normalize_date(found_date_text)
                candidate["milestone"] = found_milestone
                candidate["extraction_method"] = "Deterministic"
                candidate["extraction_confidence"] = 0.95
                continue

            # If we suspect there is scheduling info but regex failed, use LLM.
            keywords = ["completed by", "before", "after", "scheduled for", "due on", "delivery date", "target date", "completion date", "acceptance date"]
            needs_llm = any(kw in combined_text.lower() for kw in keywords) or any(mk.lower() in combined_text.lower() for mk in cls.MILESTONE_KEYWORDS)

            if needs_llm:
                llm_res = cls._extract_via_llm(candidate["name"], combined_text)
                if llm_res.get("has_schedule"):
                    candidate["deadline_text"] = llm_res.get("deadline_text")
                    candidate["deadline"] = cls._normalize_date(candidate["deadline_text"])
                    candidate["milestone"] = llm_res.get("milestone")
                    candidate["extraction_method"] = "LLM"
                    candidate["extraction_confidence"] = 0.85
                else:
                    # Explicitly stated no schedule
                    candidate["extraction_method"] = "LLM"
                    candidate["extraction_confidence"] = 0.90

        return candidates

    @classmethod
    def _normalize_date(cls, text: str) -> str | None:
        """
        Attempts to normalize date strings to YYYY-MM-DD.
        Never guess missing year, but if only day/month provided we might not be able to parse.
        """
        if not text:
            return None
        text = text.strip()

        # 15/04/2026 or 15-04-2026
        match = re.search(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})", text)
        if match:
            day, month, year = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # YYYY-MM-DD
        match = re.search(r"(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})", text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # >>> NEW: "15 April 2026" / "15 Apr 2026" (day-month-year, textual month)
        match = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", text)
        if match:
            day, month_name, year = match.groups()
            month_num = cls.MONTHS.get(month_name.lower())
            if month_num:
                return f"{year}-{month_num:02d}-{int(day):02d}"

        # >>> NEW: "April 15, 2026" / "Apr 15 2026" (month-day-year, textual month)
        match = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", text)
        if match:
            month_name, day, year = match.groups()
            month_num = cls.MONTHS.get(month_name.lower())
            if month_num:
                return f"{year}-{month_num:02d}-{int(day):02d}"

        # If year is missing in text, we DO NOT normalize. "Never guess missing year."
        if not re.search(r"\d{4}", text):
            return None

        # Anything else (e.g. "Q1 2026", "End of June", bare "15 April" with no
        # year caught above) is left unnormalized rather than guessed.
        return None

    @classmethod
    def _extract_via_llm(cls, item_name: str, evidence: str) -> dict:
        prompt = f"""
You are an expert contract scheduling extractor.
Analyze the following scope item and its evidence text to extract Milestone and Deadline information.

Scope Item: {item_name}
Evidence: {evidence}

Rules:
1. Only extract if explicitly mentioned.
2. If no deadline/milestone is mentioned, set has_schedule to false.
3. If deadline is mentioned, return the EXACT original text (e.g., "15 Apr", "End of June").
4. If a milestone name is mentioned (e.g. "UAT", "Go Live"), extract it. If the item itself is the milestone, use the item name.

Return JSON format:
{{
    "has_schedule": boolean,
    "milestone": string or null,
    "deadline_text": string or null
}}
"""
        try:
            res = LLMService.generate_json(prompt)
            return res
        except Exception:
            return {"has_schedule": False}