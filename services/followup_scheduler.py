import datetime
import json
import logging
from typing import Optional, List, Dict, Any
from apscheduler.schedulers.background import BackgroundScheduler
import mysql.connector

from core.database import get_db_connection
from core.config import settings
from services.alert_service import AlertService
from repositories.document_repository import DocumentRepository

logger = logging.getLogger("followup_scheduler")

# Background scheduler instance
scheduler = BackgroundScheduler()

def get_followup_recipients(cursor: Any, project_id: int) -> List[Dict[str, str]]:
    """
    Fetches the Team Lead and ENGAGEMENT_MANAGER for the project.
    Looks both in stakeholders and assigned project users.
    """
    recipients = []
    
    # 1. Fetch from stakeholders table
    try:
        cursor.execute(
            "SELECT email, role FROM stakeholders WHERE project_id = %s",
            (project_id,)
        )
        stakeholders = cursor.fetchall()
        for s in stakeholders:
            if s.get('email'):
                recipients.append({"email": s['email'].strip(), "role": s.get('role', 'STAKEHOLDER')})
    except Exception as e:
        logger.error(f"Error fetching stakeholders: {e}")

    # 2. Fetch from users & project_users tables
    try:
        cursor.execute("""
            SELECT u.email, u.role 
            FROM users u 
            JOIN project_users pu ON u.id = pu.user_id 
            WHERE pu.project_id = %s
        """, (project_id,))
        project_users = cursor.fetchall()
        for pu in project_users:
            if pu.get('email'):
                recipients.append({"email": pu['email'].strip(), "role": pu.get('role', 'USER')})
    except Exception as e:
        logger.error(f"Error fetching project users: {e}")

    # Deduplicate by email
    unique_recipients = {}
    for r in recipients:
        email_key = r['email'].lower()
        # Keep if role matches Team Lead or Engagement Manager, prioritize roles
        role_upper = r['role'].upper()
        is_target_role = "LEAD" in role_upper or "MANAGER" in role_upper or "ENGAGEMENT" in role_upper
        
        if email_key not in unique_recipients:
            unique_recipients[email_key] = r
        else:
            # If already added, check if we should override with a better matched role
            current_role_upper = unique_recipients[email_key]['role'].upper()
            if is_target_role and not ("LEAD" in current_role_upper or "MANAGER" in current_role_upper or "ENGAGEMENT" in current_role_upper):
                unique_recipients[email_key] = r

    # Filter to only keep Team Leads and Engagement Managers
    filtered_recipients = []
    for r in unique_recipients.values():
        role_upper = r['role'].upper()
        # Match Project Lead, Team Lead, Engagement Manager
        if "LEAD" in role_upper or "MANAGER" in role_upper or "ENGAGEMENT" in role_upper:
            filtered_recipients.append(r)
            
    return filtered_recipients

def process_followup_for_item(connection: Any, cursor: Any, item: Dict[str, Any], target_date: str) -> int:
    """
    Sends reminders for a single scope item due today and logs it.
    Returns the number of emails sent.
    """
    project_id = item['project_id']
    item_id = item['id']
    item_name = item['name']
    
    # 1. Resolve recipients
    recipients = get_followup_recipients(cursor, project_id)
    if not recipients:
        logger.warning(f"No Team Lead or ENGAGEMENT_MANAGER found for project {project_id}. Skipping follow-up.")
        return 0

    # 2. Check if we already sent an email today for this deliverable to avoid duplicates
    cursor.execute("""
        SELECT COUNT(*) as count 
        FROM alerts a
        JOIN risk_findings rf ON a.finding_id = rf.id
        WHERE rf.project_id = %s 
          AND rf.deliverable_id IS NULL
          AND rf.classification = 'MISSING_UPDATE'
          AND a.alert_type = 'DELIVERABLE_DUE_REMINDER'
          AND a.title LIKE %s
          AND DATE(a.created_at) = %s
    """, (project_id, f"%{item_name}%", target_date))
    already_sent = cursor.fetchone()['count'] > 0
    if already_sent:
        logger.info(f"Follow-up email already sent today for scope item {item_id} ({item_name}). Skipping.")
        return 0

    # 3. Create HTML Email body
    recipients_emails = [r['email'] for r in recipients]
    recipients_str = ", ".join(recipients_emails)
    
    frontend_origin = (settings.FRONTEND_ORIGIN or "").strip()
    if not frontend_origin or frontend_origin == "*" or not frontend_origin.startswith("http"):
        frontend_url = "http://localhost:5173"
    else:
        frontend_url = frontend_origin.split(",")[0].strip()
    
    completed_link = f"{frontend_url}/projects/{project_id}/baseline?selected={item_id}&action=completed"
    pending_link = f"{frontend_url}/projects/{project_id}/baseline?selected={item_id}&action=pending"
    reschedule_link = f"{frontend_url}/projects/{project_id}/baseline?selected={item_id}&action=reschedule"

    html_body = f"""
    <div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 650px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
        <!-- Header -->
        <div style="background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); padding: 30px 20px; text-align: center; color: white;">
            <span style="background-color: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 5px 12px; border-radius: 20px; font-size: 11px; font-weight: bold; text-transform: uppercase; tracking-wider: 1px;">
                ⏰ Action Required
            </span>
            <h2 style="margin: 15px 0 5px 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">
                Deliverable Due Date Follow-up
            </h2>
            <p style="margin: 0; color: #cbd5e1; font-size: 14px;">Autonomous Contract Scope Evaluator (ACSE)</p>
        </div>
        
        <!-- Body -->
        <div style="padding: 30px 25px; background-color: #ffffff;">
            <p style="color: #475569; font-size: 15px; line-height: 1.6; margin-top: 0;">
                Hello Project Team,
            </p>
            <p style="color: #475569; font-size: 15px; line-height: 1.6;">
                The following scope item/deliverable is scheduled for completion today, <strong>{target_date}</strong>. Please update its progress status in the ACSE portal.
            </p>
            
            <!-- Deliverable Details -->
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin: 25px 0;">
                <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                    <tr>
                        <td style="color: #64748b; padding: 6px 0; width: 130px; font-weight: 600;">Deliverable:</td>
                        <td style="color: #0f172a; padding: 6px 0; font-weight: 700;">{item_name}</td>
                    </tr>
                    <tr>
                        <td style="color: #64748b; padding: 6px 0; font-weight: 600;">Planned Due Date:</td>
                        <td style="color: #0f172a; padding: 6px 0; font-weight: 600;">{item.get('deadline')}</td>
                    </tr>
                    <tr>
                        <td style="color: #64748b; padding: 6px 0; font-weight: 600;">Current Status:</td>
                        <td style="padding: 6px 0;"><span style="background-color: #fee2e2; color: #991b1b; padding: 3px 8px; border-radius: 12px; font-weight: 700; font-size: 11px;">PENDING</span></td>
                    </tr>
                </table>
            </div>

            <h3 style="color: #0f172a; font-size: 16px; font-weight: 600; margin-top: 30px; margin-bottom: 15px; text-align: center;">
                Is this deliverable completed?
            </h3>
            
            <!-- Action Buttons -->
            <div style="text-align: center; margin: 20px 0 30px 0; display: flex; justify-content: center; gap: 10px; flex-wrap: wrap;">
                <a href="{completed_link}" style="background-color: #10b981; color: white; padding: 12px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 13px; display: inline-block; margin: 5px; min-width: 120px;">
                    ✓ Completed
                </a>
                <a href="{pending_link}" style="background-color: #3b82f6; color: white; padding: 12px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 13px; display: inline-block; margin: 5px; min-width: 120px;">
                    ⏳ Still Pending
                </a>
                <a href="{reschedule_link}" style="background-color: #f59e0b; color: white; padding: 12px 20px; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 13px; display: inline-block; margin: 5px; min-width: 120px;">
                    📅 Need More Time
                </a>
            </div>

            <p style="color: #64748b; font-size: 12px; text-align: center; margin-top: 25px; font-style: italic;">
                Clicking any option will redirect you to the ACSE React portal to verify and complete the update.
            </p>
        </div>
        
        <!-- Footer -->
        <div style="background-color: #f8fafc; padding: 20px; border-top: 1px solid #e2e8f0; text-align: center; color: #94a3b8; font-size: 12px;">
            ACSE Automated Email Scheduler &middot; Project #{project_id}<br>
            Please do not reply directly to this email.
        </div>
    </div>
    """

    subject = f"[ACSE] Deliverable Due Today: {item_name}"
    plain_body = f"Reminder: Deliverable '{item_name}' is due today ({item.get('deadline')}). Please log in to the ACSE portal to update its status: {frontend_url}/projects/{project_id}/baseline?selected={item_id}"

    # 4. Dispatch Email to all resolved recipients
    sent_count = 0
    for rec in recipients:
        try:
            AlertService.send_email(
                to_email=rec['email'],
                subject=subject,
                body=plain_body,
                html_body=html_body
            )
            sent_count += 1
            
            # 5. Insert into risk_findings & alerts to log notification history
            # Create a risk finding
            cursor.execute("""
                INSERT INTO risk_findings (project_id, classification, severity, reason, confidence, recommended_action, finding_status)
                VALUES (%s, 'MISSING_UPDATE', 'MEDIUM', %s, 1.0, 'Update deliverable status in Baseline review page.', 'OPEN')
            """, (project_id, f"Deliverable follow-up reminder sent today for scope item: '{item_name}' (ID: {item_id})."))
            finding_id = cursor.lastrowid
            
            # Create the alert record linked to it
            cursor.execute("""
                INSERT INTO alerts (project_id, finding_id, alert_type, severity, title, message, recipient_role, recipient_email, channel, status, sent_at)
                VALUES (%s, %s, 'DELIVERABLE_DUE_REMINDER', 'MEDIUM', %s, %s, %s, %s, 'EMAIL', 'SENT', NOW())
            """, (project_id, finding_id, subject, plain_body, rec['role'], rec['email']))
            
        except Exception as e:
            logger.error(f"Error sending email or creating alert log for recipient {rec['email']}: {e}")

    # 6. Create Audit Log entry for the follow-up execution
    try:
        details = {
            "scope_item_id": item_id,
            "scope_item_name": item_name,
            "recipients": recipients_emails,
            "emails_sent": sent_count
        }
        DocumentRepository.log_audit(
            db=connection,
            project_id=project_id,
            agent_name="Followup System",
            action="SEND_FOLLOWUP_EMAIL",
            entity_type="SCOPE_ITEM",
            entity_id=item_id,
            details_json=json.dumps(details)
        )
    except Exception as e:
        logger.error(f"Error writing audit log for follow-up reminder: {e}")

    return sent_count

def run_followup_checks(target_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Scans the database for active scope items due on target_date (defaults to today)
    and dispatches reminders to Team Leads and Engagement Managers.
    """
    logger.info("Executing automated follow-up reminder checks...")
    
    if not target_date:
        target_date = datetime.date.today().isoformat()
        
    conn = get_db_connection()
    if not conn:
        logger.error("Could not obtain database connection for follow-up checks.")
        return {"success": False, "error": "Database connection failed"}
        
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Select active scope items due on target_date
        cursor.execute("""
            SELECT id, project_id, name, deadline, completion_status 
            FROM scope_items 
            WHERE deadline = %s AND completion_status = 'ACTIVE'
        """, (target_date,))
        due_items = cursor.fetchall()
        
        logger.info(f"Found {len(due_items)} active scope items due on {target_date}.")
        
        items_processed = 0
        total_emails_sent = 0
        
        for item in due_items:
            # We wrap single items in their own try-block so a failure in one doesn't crash the whole batch
            try:
                emails_sent = process_followup_for_item(conn, cursor, item, target_date)
                if emails_sent > 0:
                    conn.commit()
                    items_processed += 1
                    total_emails_sent += emails_sent
            except Exception as e:
                conn.rollback()
                logger.error(f"Failed processing item {item.get('id')}: {e}")
                
        cursor.close()
        conn.close()
        
        return {
            "success": True,
            "target_date": target_date,
            "due_items_found": len(due_items),
            "items_processed": items_processed,
            "emails_sent": total_emails_sent
        }
        
    except Exception as e:
        logger.error(f"Exception during follow-up checks execution: {e}")
        try:
            conn.close()
        except Exception:
            pass
        return {"success": False, "error": str(e)}

def start_scheduler():
    """
    Starts the APScheduler background thread.
    Schedules run_followup_checks to execute every day at 9:00 AM.
    """
    if not scheduler.running:
        scheduler.add_job(
            run_followup_checks,
            trigger='cron',
            hour=9,
            minute=0,
            id='daily_followup_checks',
            replace_existing=True
        )
        scheduler.start()
        logger.info("APScheduler initialized and daily follow-up checks scheduled for 9:00 AM.")
