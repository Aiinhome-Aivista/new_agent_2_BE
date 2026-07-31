import re
from datetime import datetime
from services.llm_service import LLMService

class MilestoneDeadlineExtractor:
    
    DATE_PATTERNS = [
        r"(?i)(?:completed\s+by|before|after|scheduled\s+for|due\s+on|delivery\s+date|target\s+date|completion\s+date|acceptance\s+date|\bby\b|\bon\b)\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4}|[0-9]{1,2}\s+[A-Za-z]+|[A-Za-z]+\s+[0-9]{1,2}(?:,\s+[0-9]{4})?|[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4}|Q[1-4]\s+[0-9]{4}|End of [A-Za-z]+)"
    ]

    MILESTONE_KEYWORDS = ["UAT", "Deployment", "Go Live", "Training", "Knowledge Transfer"]

    @classmethod
    def extract(cls, candidates: list[dict]) -> list[dict]:
        """
        Enriches scope items with milestone and deadline information.
        Must run AFTER deduplication.
        """
        llm_batch = []
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
                if len(sentences) > 1 and not (has_item or has_mk or " | " in sentence):
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
                llm_batch.append({
                    "candidate": candidate,
                    "item_name": candidate["name"],
                    "evidence": combined_text
                })

        # Process LLM queries in batches of 10
        import json
        BATCH_SIZE = 10
        for i in range(0, len(llm_batch), BATCH_SIZE):
            batch_slice = llm_batch[i:i+BATCH_SIZE]
            print(f"[LLM] Extracting milestones for batch of {len(batch_slice)} candidates...")
            
            items_for_prompt = []
            for idx, item in enumerate(batch_slice):
                items_for_prompt.append({
                    "id": str(idx),
                    "item_name": item["item_name"],
                    "evidence": item["evidence"]
                })
                
            prompt = f"""
You are an expert contract scheduling extractor.
Analyze the following array of scope items and their evidence text to extract Milestone and Deadline information.

Items to analyze:
{json.dumps(items_for_prompt, indent=2)}

Rules for EACH item:
1. Only extract if explicitly mentioned.
2. If no deadline/milestone is mentioned, set has_schedule to false.
3. If deadline is mentioned, return the EXACT original text (e.g., "15 Apr", "End of June").
4. If a milestone name is mentioned (e.g. "UAT", "Go Live"), extract it. If the item itself is the milestone, use the item name.

Output strictly as a JSON ARRAY of objects, matching the input "id".
Schema Example:
[
  {{
    "id": "0",
    "has_schedule": true,
    "milestone": "UAT",
    "deadline_text": "15 Apr"
  }}
]
"""
            try:
                batch_results = LLMService.generate_json(prompt)
                if not isinstance(batch_results, list):
                    batch_results = [batch_results]
                    
                result_map = {str(res.get("id", "")): res for res in batch_results}
                for idx, item in enumerate(batch_slice):
                    candidate_ref = item["candidate"]
                    res = result_map.get(str(idx), {})
                    if res.get("has_schedule"):
                        candidate_ref["deadline_text"] = res.get("deadline_text")
                        candidate_ref["deadline"] = cls._normalize_date(candidate_ref["deadline_text"])
                        candidate_ref["milestone"] = res.get("milestone")
                        candidate_ref["extraction_method"] = "LLM"
                        candidate_ref["extraction_confidence"] = 0.85
                    else:
                        candidate_ref["extraction_method"] = "LLM"
                        candidate_ref["extraction_confidence"] = 0.90
            except Exception as e:
                print(f"Failed to extract milestones for batch {i}: {e}")
                for item in batch_slice:
                    candidate_ref = item["candidate"]
                    candidate_ref["extraction_method"] = "LLM_FAILED"
                    candidate_ref["extraction_confidence"] = 0.0

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

        # 15 April 2026 or 15 Apr 2026
        match = re.search(r"(?i)(\d{1,2})\s+([a-z]+)\s+(\d{4})", text)
        if match:
            months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
                      "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
            day, month_str, year = match.groups()
            month_str_lower = month_str.lower()
            month_idx = -1
            
            # Check full months
            for i, m in enumerate(months[:12]):
                if m.startswith(month_str_lower):
                    month_idx = i + 1
                    break
                    
            if month_idx == -1:
                # Check short months
                for i, m in enumerate(months[12:]):
                    if month_str_lower.startswith(m):
                        month_idx = i + 1
                        break
                        
            if month_idx != -1:
                return f"{year}-{month_idx:02d}-{int(day):02d}"
                
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
