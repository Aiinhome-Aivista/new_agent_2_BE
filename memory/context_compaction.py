from services.llm_service import LLMService

class ContextCompaction:
    @staticmethod
    def compact_events(events: list[dict]) -> str:
        if not events:
            return "No recent events."
            
        events_text = "\n".join([f"- [{e['created_at']}] {e['event_type']}: {e['event_summary']}" for e in events])
        
        prompt = f"""
Summarize the following recent events into a concise context block for an AI agent.
Preserve key facts, dates, and identified risks.
Events:
{events_text}
"""
        try:
            return LLMService.generate(prompt)
        except Exception:
            # Fallback
            return events_text
