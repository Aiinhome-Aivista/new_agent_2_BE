import sys
sys.path.insert(0, '.')
from services.alert_service import AlertService

item_name = 'ques_gen_CBSE'
project_id = 9
recipients_list = '6guqnik763@ozsaip2.com, rohandas9064@gmail.com'
summary = 'Task CBSE_all failure due to lack of specific error details'
root_cause = 'The task CBSE_all failed abruptly without providing specific error details in the logs, which is commonly caused by manual cancellation, cluster termination (e.g., spot instance reclaim), or an unhandled exception that aborted the job without proper logging.'
suggested_fix = '1. Check the Databricks job UI for any manual cancellation or cluster events.<br>2. Review the cluster driver logs for unhandled exceptions or Out-of-Memory (OOM) errors.'
reasoning = 'Task CBSE_all failed with message: Workload failed, see run output for details.'

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

print('Sending HTML test email to rohandas9064@gmail.com ...')

AlertService.send_email(
    to_email='rohandas9064@gmail.com',
    subject='[ESCALATION] Unactioned incident #9 — ques_gen_CBSE',
    body='HTML email failed to render. Please view in an HTML-compatible client.',
    html_body=html_body
)
print('Done. Check your inbox at rohandas9064@gmail.com')
