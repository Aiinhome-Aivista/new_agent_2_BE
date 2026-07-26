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
Your job is to compose a structured email notification to be sent to the project stakeholders and team members.

Project ID: {project_id}
High-Risk Item: {item_name}
AI Reasoning / Evidence: {reasoning}

Output MUST be a valid JSON object matching this schema exactly:
{{
    "subject": "[URGENT] High Risk Detected - Project <project_id>",
    "summary": "1-2 sentence summary of the issue.",
    "root_cause": "Detailed explanation of why this occurred or what the blocker is, based on the AI reasoning.",
    "suggested_fix": "1-2 clear, actionable steps to resolve the issue."
}}
"""
        response = LLMService.generate_json(prompt)
        
        subject = response.get("subject", f"[ACSE ALERT] High Risk Item Detected - Project {project_id}")
        summary = response.get("summary", f"High risk item detected: {item_name}")
        root_cause = response.get("root_cause", "No detailed root cause provided.")
        suggested_fix = response.get("suggested_fix", "Please review this item in the ACSE Tracker.")
        
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

        # 3. Create the beautiful HTML layout and dispatch to all unique recipients
        recipients_list = ", ".join([r['email'] for r in unique_recipients.values()])
        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px;">
            <!-- Header -->
            <div style="background-color: #fff0f0; padding: 15px 20px; border-bottom: 1px solid #ffcccc; border-top-left-radius: 8px; border-top-right-radius: 8px;">
                <h2 style="margin: 0; color: #d32f2f; font-size: 18px;">
                    🚨 Escalation — High Risk Item Detected
                </h2>
            </div>
            
            <!-- Body -->
            <div style="padding: 20px;">
                <p style="color: #666; margin-top: 0;">Originally notified: {recipients_list}</p>
                
                <h1 style="margin: 10px 0; font-size: 24px; color: #333;">{item_name}</h1>
                <p style="color: #888; font-size: 14px;">Project #{project_id} &middot; Awaiting Approval</p>
                
                <!-- Metadata table -->
                <table style="width: 100%; margin: 20px 0; font-size: 14px;">
                    <tr>
                        <td style="color: #666; width: 150px; padding: 5px 0;">Risk tier</td>
                        <td><span style="background-color: #ffebee; color: #c62828; padding: 3px 8px; border-radius: 12px; font-weight: bold; font-size: 12px;">High</span></td>
                    </tr>
                    <tr>
                        <td style="color: #666; padding: 5px 0;">Confidence</td>
                        <td style="color: #333;">High</td>
                    </tr>
                    <tr>
                        <td style="color: #666; padding: 5px 0;">Tracker context used</td>
                        <td style="color: #333;">Yes</td>
                    </tr>
                </table>
                
                <!-- Sections -->
                <h3 style="color: #333; margin-bottom: 8px;">Summary</h3>
                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 6px; border: 1px solid #eee; margin-bottom: 20px; color: #333;">
                    {summary}
                </div>
                
                <h3 style="color: #333; margin-bottom: 8px;">Root cause</h3>
                <div style="background-color: #f9f9f9; padding: 15px; border-radius: 6px; border: 1px solid #eee; margin-bottom: 20px; color: #333;">
                    {root_cause}
                </div>
                
                <h3 style="color: #333; margin-bottom: 8px;">Suggested fix</h3>
                <div style="background-color: #f0fdf4; padding: 15px; border-radius: 6px; border: 1px solid #bbf7d0; margin-bottom: 20px; color: #166534;">
                    {suggested_fix}
                </div>
                
                <h3 style="color: #333; margin-bottom: 8px;">AI Reasoning / Evidence</h3>
                <div style="background-color: #111827; padding: 15px; border-radius: 6px; color: #f9fafb; font-family: monospace; font-size: 13px; margin-bottom: 25px; white-space: pre-wrap;">{reasoning}</div>
                
                <div style="text-align: center; margin: 30px 0 10px;">
                    <a href="http://localhost:5173" style="background-color: #111827; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 14px;">Open Incident</a>
                </div>
            </div>
            
            <!-- Footer -->
            <div style="background-color: #f9f9f9; padding: 15px 20px; border-top: 1px solid #eee; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; color: #999; font-size: 12px;">
                ACSE AI Pipeline Monitoring System &middot; automated alert
            </div>
        </div>
        """
        
        # Plain text fallback
        body = f"High risk item detected: {item_name}\n\nSummary: {summary}\n\nRoot Cause: {root_cause}\n\nSuggested Fix: {suggested_fix}\n\nReasoning: {reasoning}\n\nPlease review in the ACSE Tracker."

        for email, rec in unique_recipients.items():
            AlertService.send_email(rec['email'], subject, body, html_body=html_body)
