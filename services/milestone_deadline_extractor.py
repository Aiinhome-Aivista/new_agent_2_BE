import re
from datetime import datetime
from services.llm_service import LLMService

class MilestoneDeadlineExtractor:
    
    # Deterministic keywords
    # Captures things like "completed by 15 April", "due on End of June", "scheduled for 2026-04-15", "target date is Q2 2027"
    DATE_PATTERNS = [
        r"(?i)(?:completed\s+by|before|after|scheduled\s+for|due\s+on|delivery\s+date|target\s+date|completion\s+date|acceptance\s+date)\s+([A-Za-z0-9\-\/\s]+)(?:\.|\,|$|\n)"
    ]

    MILESTONE_KEYWORDS = ["UAT", "Deployment", "Go Live", "Training", "Knowledge Transfer", "milestone"]

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

            # Default values
            candidate["milestone"] = None
            candidate["deadline_text"] = None
            candidate["deadline"] = None
            candidate["extraction_method"] = None
            candidate["extraction_confidence"] = None

            # 1. Deterministic Extraction
            found_date_text = None
            for pattern in cls.DATE_PATTERNS:
                match = re.search(pattern, combined_text)
                if match:
                    found_date_text = match.group(1).strip()
                    break
            
            found_milestone = None
            for mk in cls.MILESTONE_KEYWORDS:
                if mk.lower() in combined_text.lower():
                    # Check if the milestone is the subject (like "Go Live is scheduled for...")
                    # or if the candidate itself is the milestone
                    found_milestone = mk
                    break

            if found_date_text:
                candidate["deadline_text"] = found_date_text
                candidate["deadline"] = cls._normalize_date(found_date_text)
                candidate["milestone"] = found_milestone if found_milestone else candidate.get("name")
                candidate["extraction_method"] = "Deterministic"
                candidate["extraction_confidence"] = 0.95
                continue
                
            # If we found a milestone keyword but no clear date format deterministically,
            # or if we suspect there is scheduling info but regex failed, we can use LLM.
            # We'll use LLM if any keyword is present just to be safe.
            keywords = ["completed by", "before", "after", "scheduled for", "due on", "delivery date", "target date", "completion date", "acceptance date"]
            needs_llm = any(kw in combined_text.lower() for kw in keywords) or found_milestone
            
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
        Prompt says: "Never guess missing year. Never invent a normalized date."
        So if it's "15 Apr", standard datetime parsing might append current year. We should be careful.
        Actually, dateparser or similar is best, but we'll use a safe approach.
        """
        if not text:
            return None
            
        # Common strict formats
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

        # 15 April 2026
        months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
                  "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        
        # If year is missing in text, we DO NOT normalize. "Never guess missing year."
        if not re.search(r"\d{4}", text):
            return None
            
        # Example for explicit parsing of like 15 April 2026 if regex above failed
        # Since dateutil might not be installed, we just return None for complex strings.
        # Strict formats were handled above.
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
