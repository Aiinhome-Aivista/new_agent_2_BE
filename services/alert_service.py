import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from core.config import settings

class AlertService:
    @staticmethod
    def send_email(to_email: str, subject: str, body: str):
        if not settings.SMTP_SERVER:
            # Dummy output if SMTP is not configured
            print(f"--- DUMMY EMAIL ---")
            print(f"To: {to_email}")
            print(f"Subject: {subject}")
            print(f"Body: {body}")
            print(f"-------------------")
            return
            
        try:
            msg = MIMEMultipart()
            msg['From'] = settings.SMTP_EMAIL or "acse@example.com"
            msg['To'] = to_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            server = smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT)
            if settings.SMTP_EMAIL and settings.SMTP_PASSWORD:
                server.starttls()
                server.login(settings.SMTP_EMAIL, settings.SMTP_PASSWORD)
                
            server.send_message(msg)
            server.quit()
        except Exception as e:
            print(f"Failed to send email: {e}")
            
    @classmethod
    def alert_high_risk(cls, project_id: int, item_name: str, reasoning: str, stakeholders: list):
        for stakeholder in stakeholders:
            if stakeholder['role'] in ['ENGAGEMENT_MANAGER', 'PROJECT_LEAD']:
                subject = f"[ACSE ALERT] High Risk Item Detected - Project {project_id}"
                body = f"""
A high-risk out-of-scope item was detected by ACSE.

Item: {item_name}
Reasoning: {reasoning}

Please review this item in the ACSE Tracker.
"""
                cls.send_email(stakeholder['email'], subject, body)
