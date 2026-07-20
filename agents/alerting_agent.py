from services.llm_service import LLMService
from services.alert_service import AlertService

class AlertingAgent:
    @classmethod
    def dispatch_alert(cls, project_id: int, item_name: str, reasoning: str, stakeholders: list):
        """
        Acts as the Alerting Agent. Uses the LLM to compose a context-aware email 
        notification regarding a high-risk item, and then dispatches it deterministically.
        """
        # 1. Use the LLM to compose the message (Plan-and-Execute pattern)
        prompt = f"""You are the Alerting Agent for a project management system.
A high-risk project deviation has just been detected. 
Your job is to compose a professional, urgent, and clear email notification to be sent to the project stakeholders.

Project ID: {project_id}
High-Risk Item: {item_name}
AI Reasoning / Evidence: {reasoning}

Output MUST be a valid JSON object matching this schema exactly:
{{
    "subject": "[URGENT] High Risk Detected - Project <project_id>",
    "body": "Dear Stakeholders,\\n\\nA critical risk has been detected...\\n\\nReasoning:\\n..."
}}
"""
        response = LLMService.generate_json(prompt)
        
        subject = response.get("subject", f"[ACSE ALERT] High Risk Item Detected - Project {project_id}")
        body = response.get("body", f"High risk item detected: {item_name}\n\nReasoning: {reasoning}\n\nPlease review in the ACSE Tracker.")
        
        # 2. Dispatch the composed notification to relevant stakeholders
        for stakeholder in stakeholders:
            if stakeholder.get('role') in ['ENGAGEMENT_MANAGER', 'PROJECT_LEAD']:
                AlertService.send_email(stakeholder.get('email'), subject, body)
