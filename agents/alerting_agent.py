from services.llm_service import LLMService
from services.alert_service import AlertService

class AlertingAgent:
    @classmethod
    def dispatch_alert(cls, project_id: int, item_name: str, reasoning: str, stakeholders: list, db_cursor=None):
        """
        Acts as the Alerting Agent. Uses the LLM to compose a context-aware email 
        notification regarding a high-risk item, and then dispatches it to all personas
        and stakeholders related to this specific project.
        """
        # 1. Use the LLM to compose the message
        prompt = f"""You are the Alerting Agent for a project management system.
A high-risk project deviation has just been detected. 
Your job is to compose a professional, urgent, and clear email notification to be sent to the project stakeholders and team members.

Project ID: {project_id}
High-Risk Item: {item_name}
AI Reasoning / Evidence: {reasoning}

Output MUST be a valid JSON object matching this schema exactly:
{{
    "subject": "[URGENT] High Risk Detected - Project <project_id>",
    "body": "Dear Project Team and Stakeholders,\\n\\nA critical risk has been detected...\\n\\nReasoning:\\n..."
}}
"""
        response = LLMService.generate_json(prompt)
        
        subject = response.get("subject", f"[ACSE ALERT] High Risk Item Detected - Project {project_id}")
        body = response.get("body", f"High risk item detected: {item_name}\n\nReasoning: {reasoning}\n\nPlease review in the ACSE Tracker.")
        
        # 2. Collect stakeholders and assigned project users (personas) to build full recipient list
        recipients = []
        for s in stakeholders:
            if s.get('email'):
                recipients.append({"email": s['email'], "role": s.get('role', 'STAKEHOLDER')})

        # Fetch assigned personas from the database
        close_conn = False
        cursor = db_cursor
        if not cursor:
            from core.database import get_db_connection
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor(dictionary=True)
                close_conn = True

        if cursor:
            try:
                cursor.execute("""
                    SELECT u.email, u.role 
                    FROM users u 
                    JOIN project_users pu ON u.id = pu.user_id 
                    WHERE pu.project_id = %s
                """, (project_id,))
                assigned_users = cursor.fetchall()
                for au in assigned_users:
                    if au.get('email'):
                        recipients.append({"email": au['email'], "role": au.get('role', 'USER')})
            except Exception as e:
                print(f"Failed to fetch assigned users for alert: {e}")
            finally:
                if close_conn:
                    cursor.close()
                    conn.close()

        # Deduplicate recipients by email address
        unique_recipients = {}
        for r in recipients:
            email_key = r['email'].strip().lower()
            if email_key not in unique_recipients:
                unique_recipients[email_key] = r

        # 3. Dispatch the composed notification to all unique recipients
        for email, rec in unique_recipients.items():
            AlertService.send_email(rec['email'], subject, body)
