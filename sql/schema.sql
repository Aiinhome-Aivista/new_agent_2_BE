CREATE DATABASE IF NOT EXISTS acse_db
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE acse_db;

CREATE TABLE users (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
name VARCHAR(150) NOT NULL,
email VARCHAR(255) NOT NULL UNIQUE,
password_hash VARCHAR(255) NOT NULL,
role ENUM(
'ADMIN',
'ENGAGEMENT_MANAGER',
'PROJECT_LEAD',
'PMO_REVIEWER',
'FINANCE_COMMERCIAL'
) NOT NULL,
is_active BOOLEAN NOT NULL DEFAULT TRUE,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE projects (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_name VARCHAR(255) NOT NULL,
client_name VARCHAR(255),
description TEXT,
monitoring_status ENUM(
'DRAFT',
'BASELINE_PENDING_REVIEW',
'ACTIVE',
'PAUSED'
) NOT NULL DEFAULT 'DRAFT',
created_by BIGINT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_projects_created_by
        FOREIGN KEY (created_by)
        REFERENCES users(id)
);

CREATE TABLE project_users (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
user_id BIGINT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_project_user
        UNIQUE (project_id, user_id),

    CONSTRAINT fk_project_users_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_project_users_user
        FOREIGN KEY (user_id)
        REFERENCES users(id)
        ON DELETE CASCADE
);

CREATE TABLE stakeholders (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
name VARCHAR(150) NOT NULL,
email VARCHAR(255),
role VARCHAR(100),
responsibility TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_stakeholders_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE
);

CREATE TABLE documents (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
document_name VARCHAR(255) NOT NULL,
document_type VARCHAR(255) NOT NULL,
storage_key VARCHAR(500) NOT NULL,
processing_status ENUM(
'UPLOADED',
'PARSING',
'EMBEDDED',
'PROCESSING',
'COMPLETED',
'FAILED'
) NOT NULL DEFAULT 'UPLOADED',
processing_error TEXT,
uploaded_by BIGINT NOT NULL,
uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_documents_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_documents_user
        FOREIGN KEY (uploaded_by)
        REFERENCES users(id)
);

CREATE TABLE master_document_types (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL UNIQUE,
    label VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO master_document_types (name, label, description) VALUES
('EL', 'Engagement Letter (EL)', 'Official contract or engagement letter'),
('IFA', 'Inter-Firm Approval (IFA)', 'Internal financial approval document'),
('STATUS_REPORT', 'Status Report', 'Weekly or monthly project status report'),
('MOM', 'Minutes of Meeting (MOM)', 'Meeting minutes and decisions');

CREATE TABLE document_types (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
name VARCHAR(100) NOT NULL,
label VARCHAR(255) NOT NULL,
description TEXT,
added_by BIGINT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_doctype_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_doctype_user
        FOREIGN KEY (added_by)
        REFERENCES users(id)
);

CREATE TABLE scope_baselines (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
version INT NOT NULL DEFAULT 1,
status ENUM(
'DRAFT',
'APPROVED',
'SUPERSEDED'
) NOT NULL DEFAULT 'DRAFT',
approved_by BIGINT NULL,
approved_at DATETIME NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_baseline_version
        UNIQUE (project_id, version),

    CONSTRAINT fk_baseline_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_baseline_approved_by
        FOREIGN KEY (approved_by)
        REFERENCES users(id)
);

CREATE TABLE scope_items (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
baseline_id BIGINT NOT NULL,
project_id BIGINT NOT NULL,
name VARCHAR(500) NOT NULL,
description TEXT,
scope_type ENUM(
'IN_SCOPE',
'OUT_OF_SCOPE',
'UNCERTAIN'
) NOT NULL,
source_document_id BIGINT NOT NULL,
source_page INT NULL,
source_section VARCHAR(255),
evidence_text TEXT NOT NULL,
confidence DECIMAL(5,4),
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_scope_item_baseline
        FOREIGN KEY (baseline_id)
        REFERENCES scope_baselines(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_scope_item_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_scope_item_document
        FOREIGN KEY (source_document_id)
        REFERENCES documents(id)
);

CREATE TABLE deliverables (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
baseline_id BIGINT NOT NULL,
project_id BIGINT NOT NULL,
name VARCHAR(500) NOT NULL,
description TEXT,
deadline DATE NULL,
owner VARCHAR(255),
delivery_status ENUM(
'NOT_STARTED',
'IN_PROGRESS',
'COMPLETED',
'BLOCKED',
'DELAYED',
'AT_RISK',
'UNKNOWN'
) NOT NULL DEFAULT 'NOT_STARTED',
    progress_percentage DECIMAL(5,2) NULL,
    last_update_at DATETIME NULL,
    source_document_id BIGINT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_deliverable_baseline
        FOREIGN KEY (baseline_id)
        REFERENCES scope_baselines(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_deliverable_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_deliverable_document
        FOREIGN KEY (source_document_id)
        REFERENCES documents(id)
        ON DELETE CASCADE
);

CREATE TABLE project_activities (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
document_id BIGINT NOT NULL,
activity_name VARCHAR(500) NOT NULL,
description TEXT,
activity_status ENUM(
'NOT_STARTED',
'PLANNED',
'IN_PROGRESS',
'COMPLETED',
'BLOCKED',
'DELAYED',
'UNKNOWN'
) NOT NULL DEFAULT 'UNKNOWN',
progress_percentage DECIMAL(5,2) NULL,
requested_by VARCHAR(255),
owner VARCHAR(255),
mentioned_deadline DATE NULL,
source_page INT NULL,
source_section VARCHAR(255),
evidence_text TEXT NOT NULL,
confidence DECIMAL(5,4),
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_activity_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_activity_document
        FOREIGN KEY (document_id)
        REFERENCES documents(id)
        ON DELETE CASCADE
);

CREATE TABLE new_requests (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
document_id BIGINT NOT NULL,
request_name VARCHAR(500) NOT NULL,
requested_by VARCHAR(255),
request_status ENUM(
'DETECTED',
'UNDER_REVIEW',
'APPROVED_CHANGE',
'REJECTED'
) NOT NULL DEFAULT 'DETECTED',
source_page INT NULL,
evidence_text TEXT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_request_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_request_document
        FOREIGN KEY (document_id)
        REFERENCES documents(id)
        ON DELETE CASCADE
);

CREATE TABLE risk_findings (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
activity_id BIGINT NULL,
deliverable_id BIGINT NULL,
classification ENUM(
'IN_SCOPE',
'OUT_OF_SCOPE',
'POTENTIAL_SCOPE_CREEP',
'AT_RISK',
'MISSING_DELIVERABLE',
'MISSING_UPDATE',
'UNCERTAIN'
) NOT NULL,
severity ENUM(
'LOW',
'MEDIUM',
'HIGH',
'CRITICAL'
) NOT NULL,
reason TEXT NOT NULL,
confidence DECIMAL(5,4),
recommended_action TEXT,
finding_status ENUM(
'OPEN',
'UNDER_REVIEW',
'CONFIRMED',
'DISMISSED',
'RESOLVED'
) NOT NULL DEFAULT 'OPEN',
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_finding_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_finding_activity
        FOREIGN KEY (activity_id)
        REFERENCES project_activities(id)
        ON DELETE SET NULL,

    CONSTRAINT fk_finding_deliverable
        FOREIGN KEY (deliverable_id)
        REFERENCES deliverables(id)
        ON DELETE SET NULL
);

CREATE TABLE finding_evidence (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
finding_id BIGINT NOT NULL,
evidence_type ENUM(
'CONTRACT',
'ACTIVITY'
) NOT NULL,
document_id BIGINT NOT NULL,
document_name VARCHAR(255) NOT NULL,
page_number INT NULL,
section VARCHAR(255),
chunk_id VARCHAR(255),
evidence_text TEXT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_evidence_finding
        FOREIGN KEY (finding_id)
        REFERENCES risk_findings(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_evidence_document
        FOREIGN KEY (document_id)
        REFERENCES documents(id)
);

CREATE TABLE alerts (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
finding_id BIGINT NOT NULL,
alert_type VARCHAR(100) NOT NULL,
severity ENUM(
'LOW',
'MEDIUM',
'HIGH',
'CRITICAL'
) NOT NULL,
title VARCHAR(255) NOT NULL,
message TEXT NOT NULL,
recipient_role VARCHAR(100),
recipient_email VARCHAR(255),
channel ENUM('EMAIL') NOT NULL DEFAULT 'EMAIL',
status ENUM(
'PENDING',
'SENT',
'FAILED'
) NOT NULL DEFAULT 'PENDING',
error_message TEXT,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
sent_at DATETIME NULL,

    CONSTRAINT fk_alert_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_alert_finding
        FOREIGN KEY (finding_id)
        REFERENCES risk_findings(id)
        ON DELETE CASCADE
);

CREATE TABLE workflow_runs (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
run_id VARCHAR(100) NOT NULL UNIQUE,
project_id BIGINT NOT NULL,
workflow_type ENUM(
'BASELINE_EXTRACTION',
'MONITORING_RECONCILIATION'
) NOT NULL,
status ENUM(
'RUNNING',
'COMPLETED',
'FAILED'
) NOT NULL,
started_at DATETIME NOT NULL,
completed_at DATETIME NULL,
error_message TEXT,

    CONSTRAINT fk_workflow_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE
);

CREATE TABLE episodic_memory (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
run_id VARCHAR(100),
event_type VARCHAR(100) NOT NULL,
event_summary TEXT NOT NULL,
entity_type VARCHAR(100),
entity_id BIGINT NULL,
importance_score DECIMAL(5,4) DEFAULT 0.5000,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_episodic_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE
);

CREATE TABLE context_compactions (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NOT NULL,
summary_text LONGTEXT NOT NULL,
source_event_count INT NOT NULL,
oldest_event_at DATETIME NULL,
newest_event_at DATETIME NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_compaction_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE
);

CREATE TABLE audit_logs (
id BIGINT PRIMARY KEY AUTO_INCREMENT,
project_id BIGINT NULL,
run_id VARCHAR(100),
agent_name VARCHAR(100) NOT NULL,
action VARCHAR(255) NOT NULL,
entity_type VARCHAR(100),
entity_id BIGINT NULL,
details_json JSON,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_audit_project
        FOREIGN KEY (project_id)
        REFERENCES projects(id)
        ON DELETE SET NULL
);

CREATE TABLE risk_evaluations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL,
    document_id BIGINT NOT NULL,
    evaluation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    overall_risk_score INT DEFAULT 0,
    overall_risk_level ENUM('LOW', 'MEDIUM', 'HIGH', 'CRITICAL') DEFAULT 'LOW',
    summary TEXT,
    recommendations JSON,
    sub_agent_results JSON,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_projects_status ON projects(monitoring_status);
CREATE INDEX idx_documents_project ON documents(project_id);
CREATE INDEX idx_documents_type ON documents(project_id, document_type);
CREATE INDEX idx_scope_items_project ON scope_items(project_id);
CREATE INDEX idx_scope_items_baseline ON scope_items(baseline_id);
CREATE INDEX idx_deliverables_project ON deliverables(project_id);
CREATE INDEX idx_activities_project ON project_activities(project_id);
CREATE INDEX idx_findings_project ON risk_findings(project_id);
CREATE INDEX idx_findings_classification ON risk_findings(project_id, classification);
CREATE INDEX idx_findings_severity ON risk_findings(project_id, severity);
CREATE INDEX idx_alerts_project ON alerts(project_id);
CREATE INDEX idx_workflow_project ON workflow_runs(project_id);
CREATE INDEX idx_episodic_project ON episodic_memory(project_id, created_at);
CREATE INDEX idx_audit_project ON audit_logs(project_id, created_at);

DELIMITER //

CREATE PROCEDURE truncate_all_tables_except_users_and_master()
BEGIN
    SET FOREIGN_KEY_CHECKS = 0;

    TRUNCATE TABLE alerts;
    TRUNCATE TABLE audit_logs;
    TRUNCATE TABLE context_compactions;
    TRUNCATE TABLE deliverables;
    TRUNCATE TABLE document_types;
    TRUNCATE TABLE documents;
    TRUNCATE TABLE episodic_memory;
    TRUNCATE TABLE finding_evidence;
    TRUNCATE TABLE new_requests;
    TRUNCATE TABLE project_activities;
    TRUNCATE TABLE project_users;
    TRUNCATE TABLE projects;
    TRUNCATE TABLE risk_findings;
    TRUNCATE TABLE scope_baselines;
    TRUNCATE TABLE scope_items;
    TRUNCATE TABLE stakeholders;
    TRUNCATE TABLE tracker_items;
    TRUNCATE TABLE workflow_runs;

    SET FOREIGN_KEY_CHECKS = 1;
END //

DELIMITER ;
