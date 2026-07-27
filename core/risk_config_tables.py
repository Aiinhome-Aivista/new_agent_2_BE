"""
Risk Configuration Tables — DB init script.
Creates and seeds the five config tables on server startup.
Called from services/risk_config_service.py on first import.
"""
from core.database import get_db_connection


def create_risk_config_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # ── 1. Risk Parameter Config (weights per scoring dimension) ────────
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_parameter_config (
            id INT AUTO_INCREMENT PRIMARY KEY,
            parameter_code VARCHAR(50) UNIQUE NOT NULL,
            parameter_name VARCHAR(100) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            weight INT NOT NULL,
            description TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # Seed default parameters
        cursor.execute("""
        INSERT IGNORE INTO risk_parameter_config
            (parameter_code, parameter_name, enabled, weight, description)
        VALUES
            ('SCOPE_MATCH',          'Scope Match',           TRUE, 30, 'Is the activity approved by the contract baseline?'),
            ('TIMELINE',             'Timeline / Deadline',   TRUE, 20, 'Has the contractual deadline been missed?'),
            ('MILESTONE',            'Milestone Slippage',    TRUE, 10, 'Is a contractual milestone slipping?'),
            ('CUSTOMER_DEPENDENCY',  'Customer Dependency',   TRUE, 10, 'Is a customer obligation (VPN, API creds, infrastructure) pending?'),
            ('PROGRESS',             'Progress Behind',       TRUE, 10, 'Is deliverable progress behind schedule?'),
            ('TECHNICAL_DEPENDENCY', 'Technical Dependency',  TRUE,  5, 'Is work blocked by an internal technical dependency?'),
            ('MISSING_DELIVERABLE',  'Missing Deliverable',   TRUE,  5, 'Is a contractual deliverable not progressing at all?'),
            ('CONFIDENCE',           'Evidence Confidence',   TRUE,  5, 'How confident is the AI in its assessment?'),
            ('BUSINESS_IMPACT',      'Business Impact',       TRUE,  5, 'What is the estimated business severity?')
        """)

        # ── 2. Risk Threshold Config (severity bands) ────────────────────────
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_threshold_config (
            severity VARCHAR(20) PRIMARY KEY,
            min_score INT NOT NULL,
            max_score INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
        INSERT IGNORE INTO risk_threshold_config (severity, min_score, max_score) VALUES
            ('LOW',       0,  29),
            ('MEDIUM',   30,  59),
            ('HIGH',     60,  79),
            ('CRITICAL', 80, 100)
        """)

        # ── 3. Business Rule Config (enable / disable logic) ─────────────────
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_rule_config (
            rule_code VARCHAR(60) PRIMARY KEY,
            rule_description TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
        INSERT IGNORE INTO business_rule_config (rule_code, rule_description, enabled) VALUES
            ('SCOPE_CREEP_BLOCKS_IN_SCOPE',    'IN_SCOPE baseline items can never be SCOPE_CREEP',       TRUE),
            ('CUSTOMER_DEP_INCREASES_RISK',    'Customer dependency (VPN, API creds) adds to risk score', TRUE),
            ('DEADLINE_MISSED_INCREASES_RISK', 'Missed contractual deadline adds to risk score',          TRUE),
            ('MISSING_DELIVERABLE_RISK',       'Missing contractual deliverable triggers elevated risk',  TRUE),
            ('IGNORE_INTERNAL_TASKS',          'Internal housekeeping / process tasks are not risks',    TRUE)
        """)

        # ── 4. Business Impact Matrix (LLM picks level, rules pick score) ────
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS impact_matrix (
            impact_level VARCHAR(20) PRIMARY KEY,
            score_addition INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
        INSERT IGNORE INTO impact_matrix (impact_level, score_addition) VALUES
            ('LOW',    0),
            ('MEDIUM', 5),
            ('HIGH',  10)
        """)

        # ── 5. Alert Rule Config (which severities trigger email alerts) ─────
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_rule_config (
            severity VARCHAR(20) PRIMARY KEY,
            send_email BOOLEAN NOT NULL DEFAULT FALSE,
            min_score_threshold INT NOT NULL DEFAULT 70
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
        INSERT IGNORE INTO alert_rule_config (severity, send_email, min_score_threshold) VALUES
            ('LOW',      FALSE, 100),
            ('MEDIUM',   FALSE,  60),
            ('HIGH',     TRUE,   70),
            ('CRITICAL', TRUE,   80)
        """)

        conn.commit()
        print("Risk config tables successfully checked/created.")
    except Exception as e:
        print(f"Risk Config Tables Setup Error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
