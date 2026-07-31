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
        cursor.execute("DROP TABLE IF EXISTS risk_parameter_config")
        cursor.execute("""
        CREATE TABLE risk_parameter_config (
            id INT AUTO_INCREMENT PRIMARY KEY,
            parameter_code VARCHAR(50) UNIQUE NOT NULL,
            parameter_name VARCHAR(100) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            weight FLOAT NOT NULL,
            max_score INT NOT NULL,
            evaluation_type VARCHAR(20) NOT NULL DEFAULT 'NUMERIC',
            description TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # Seed Execution Priority model parameters
        cursor.execute("""
        INSERT IGNORE INTO risk_parameter_config
            (parameter_code, parameter_name, enabled, weight, max_score, evaluation_type, description)
        VALUES
            ('EXECUTION_PRIORITY',   'Execution Priority',    TRUE, 1.0, 35, 'NUMERIC', 'Primary execution urgency'),
            ('CASCADE_IMPACT',       'Cascade Impact',        TRUE, 1.0, 20, 'NUMERIC', 'Number of downstream milestones affected'),
            ('DATE_PROXIMITY',       'Date Proximity',        TRUE, 1.0, 15, 'NUMERIC', 'Urgency relative to today\\'s date'),
            ('ROOT_CAUSE',           'Root Cause',            TRUE, 1.0, 15, 'BOOLEAN', 'Primary execution blocker'),
            ('CUSTOMER_DEPENDENCY',  'Customer Dependency',   TRUE, 1.0, 10, 'BOOLEAN', 'Waiting for customer input'),
            ('TECHNICAL_DEPENDENCY', 'Technical Dependency',  TRUE, 1.0,  5, 'BOOLEAN', 'Internal technical blocker'),
            ('BUSINESS_IMPACT',      'Business Impact',       TRUE, 1.0,  5, 'ENUM',    'Business consequence'),
            ('SCOPE_CREEP',          'Scope Creep',           TRUE, 1.0,  5, 'BOOLEAN', 'Contractual impact'),
            ('CONFIDENCE',           'Evidence Confidence',   TRUE, 1.0,  2, 'NUMERIC', 'Extraction confidence')
        """)

        # ── 1.5 Risk Category Priority ───────────────────────────────────────
        cursor.execute("DROP TABLE IF EXISTS risk_category_priority")
        cursor.execute("""
        CREATE TABLE risk_category_priority (
            category VARCHAR(50) PRIMARY KEY,
            priority_order INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
        INSERT INTO risk_category_priority (category, priority_order) VALUES
            ('ROOT_CAUSE', 1),
            ('EXECUTION_BLOCKER', 2),
            ('CUSTOMER_DEPENDENCY', 3),
            ('DELAY', 4),
            ('SCOPE_CREEP', 5)
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

        # ── 6. Category Assignment Rules (Configurable Matrix) ─────────────
        cursor.execute("DROP TABLE IF EXISTS category_assignment_rules")
        cursor.execute("""
        CREATE TABLE category_assignment_rules (
            id INT AUTO_INCREMENT PRIMARY KEY,
            entity_type VARCHAR(50) NOT NULL,
            dependency_source VARCHAR(50) DEFAULT NULL,
            status VARCHAR(50) DEFAULT NULL,
            result_category VARCHAR(50) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        cursor.execute("""
        INSERT INTO category_assignment_rules (entity_type, dependency_source, status, result_category) VALUES
            ('DEPENDENCY', 'CUSTOMER',  'BLOCKED', 'CUSTOMER_DEPENDENCY'),
            ('DEPENDENCY', 'CUSTOMER',  'DELAYED', 'CUSTOMER_DEPENDENCY'),
            ('DEPENDENCY', 'CUSTOMER',  'PENDING', 'CUSTOMER_DEPENDENCY'),
            ('DEPENDENCY', 'TECHNICAL', 'BLOCKED', 'TECHNICAL_DEPENDENCY'),
            ('DEPENDENCY', 'TECHNICAL', 'DELAYED', 'TECHNICAL_DEPENDENCY'),
            ('DEPENDENCY', 'TECHNICAL', 'PENDING', 'TECHNICAL_DEPENDENCY'),
            ('DEPENDENCY', 'PROJECT',   'BLOCKED', 'EXECUTION_BLOCKER'),
            ('DEPENDENCY', 'EXTERNAL',  'BLOCKED', 'EXECUTION_BLOCKER'),
            ('MILESTONE',  NULL,        'BLOCKED', 'EXECUTION_BLOCKER'),
            ('MILESTONE',  NULL,        'DELAYED', 'DELAY'),
            ('SCOPE_REQUEST', NULL,     'NEW',     'SCOPE_CREEP'),
            ('SCOPE_REQUEST', NULL,     'IN_PROGRESS', 'SCOPE_CREEP')
        """)

        conn.commit()
        print("Risk config tables successfully checked/created.")
    except Exception as e:
        print(f"Risk Config Tables Setup Error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
