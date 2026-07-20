import mysql.connector
from core.config import settings

def migrate():
    db = mysql.connector.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )
    c = db.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS risk_evaluations (
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
    """)
    db.commit()
    c.close()
    db.close()
    print("Migration successful")

if __name__ == "__main__":
    migrate()
