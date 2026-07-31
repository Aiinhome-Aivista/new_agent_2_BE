"""
RiskConfigurationService
========================
Single source of truth for all risk engine configuration.

The RiskEvaluationAgent NEVER reads configuration directly from the DB.
It always goes through this service, which loads once and caches in memory.

Tables used:
  risk_parameter_config   — weights per scoring dimension (enable/disable per client)
  risk_threshold_config   — severity bands (LOW / MEDIUM / HIGH / CRITICAL)
  impact_matrix           — LLM says "HIGH impact" → this table says +10 score
  alert_rule_config       — which severities trigger email alerts
  risk_category_priority  — order for tie breaking the highest priority items

To change a weight without a deployment:
    UPDATE risk_parameter_config SET weight = 40 WHERE parameter_code = 'SCOPE_MATCH';
Then call RiskConfigurationService.invalidate_cache().
"""

import threading


class RiskConfigurationService:
    """
    Loads all risk configuration from DB tables and caches in memory.
    Thread-safe via a simple lock.
    """

    _cache: dict = {}
    _lock = threading.Lock()

    # ── Public Loaders ──────────────────────────────────────────────────────

    @classmethod
    def get_parameters(cls, db_cursor) -> dict:
        """
        Returns: { "EXECUTION_PRIORITY": {"weight": 1.0, "max_score": 35, ...}, ... }
        """
        return cls._load("parameters", db_cursor, cls._fetch_parameters)

    @classmethod
    def get_thresholds(cls, db_cursor) -> list:
        """
        Returns: [{"severity": "CRITICAL", "min_score": 80, "max_score": 100}, ...]
        Sorted descending by min_score so CRITICAL is checked first.
        """
        return cls._load("thresholds", db_cursor, cls._fetch_thresholds)

    @classmethod
    def get_impact_matrix(cls, db_cursor) -> dict:
        """
        Returns: { "LOW": 0, "MEDIUM": 5, "HIGH": 10 }
        """
        return cls._load("impact_matrix", db_cursor, cls._fetch_impact_matrix)

    @classmethod
    def get_alert_rules(cls, db_cursor) -> dict:
        """
        Returns: { "HIGH": {"send_email": True, "min_score_threshold": 70}, ... }
        """
        return cls._load("alert_rules", db_cursor, cls._fetch_alert_rules)

    @classmethod
    def get_category_priorities(cls, db_cursor) -> dict:
        """
        Returns: { "ROOT_CAUSE": 1, "EXECUTION_BLOCKER": 2, ... }
        """
        return cls._load("category_priorities", db_cursor, cls._fetch_category_priorities)

    @classmethod
    def get_category_rules(cls, db_cursor) -> list:
        """
        Returns: [{"entity_type": "DEPENDENCY", "dependency_source": "CUSTOMER", "status": "BLOCKED", "result_category": "CUSTOMER_DEPENDENCY"}, ...]
        """
        return cls._load("category_rules", db_cursor, cls._fetch_category_rules)

    # ── Utility ─────────────────────────────────────────────────────────────

    @classmethod
    def classify_severity(cls, score: int, thresholds: list) -> str:
        """
        Maps a numeric score → severity level using DB thresholds.
        Example: 72 → "HIGH"
        """
        for t in sorted(thresholds, key=lambda x: x["min_score"], reverse=True):
            if score >= t["min_score"]:
                return t["severity"]
        return "LOW"

    @classmethod
    def invalidate_cache(cls):
        """Call after updating any config table so next request reloads from DB."""
        with cls._lock:
            cls._cache.clear()
        print("[RiskConfigService] Cache invalidated.")

    # ── Internal ─────────────────────────────────────────────────────────────

    @classmethod
    def _load(cls, key: str, db_cursor, fetcher):
        with cls._lock:
            if key not in cls._cache:
                cls._cache[key] = fetcher(db_cursor)
        return cls._cache[key]

    @staticmethod
    def _fetch_parameters(db_cursor) -> dict:
        db_cursor.execute(
            "SELECT parameter_code, parameter_name, enabled, weight, max_score, evaluation_type "
            "FROM risk_parameter_config"
        )
        rows = db_cursor.fetchall()
        result = {}
        for row in rows:
            if isinstance(row, dict):
                code = row["parameter_code"]
                result[code] = {
                    "name": row["parameter_name"],
                    "enabled": bool(row["enabled"]),
                    "weight": float(row["weight"]),
                    "max_score": int(row["max_score"]),
                    "evaluation_type": row["evaluation_type"]
                }
            else:
                code = row[0]
                result[code] = {
                    "name": row[1],
                    "enabled": bool(row[2]),
                    "weight": float(row[3]),
                    "max_score": int(row[4]),
                    "evaluation_type": row[5]
                }
        return result

    @staticmethod
    def _fetch_thresholds(db_cursor) -> list:
        db_cursor.execute(
            "SELECT severity, min_score, max_score "
            "FROM risk_threshold_config ORDER BY min_score DESC"
        )
        rows = db_cursor.fetchall()
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append({
                    "severity": row["severity"],
                    "min_score": int(row["min_score"]),
                    "max_score": int(row["max_score"]),
                })
            else:
                result.append({
                    "severity": row[0],
                    "min_score": int(row[1]),
                    "max_score": int(row[2]),
                })
        return result

    @staticmethod
    def _fetch_impact_matrix(db_cursor) -> dict:
        db_cursor.execute(
            "SELECT impact_level, score_addition FROM impact_matrix"
        )
        rows = db_cursor.fetchall()
        result = {}
        for row in rows:
            if isinstance(row, dict):
                result[row["impact_level"].upper()] = int(row["score_addition"])
            else:
                result[row[0].upper()] = int(row[1])
        return result

    @staticmethod
    def _fetch_alert_rules(db_cursor) -> dict:
        db_cursor.execute(
            "SELECT severity, send_email, min_score_threshold FROM alert_rule_config"
        )
        rows = db_cursor.fetchall()
        result = {}
        for row in rows:
            if isinstance(row, dict):
                result[row["severity"]] = {
                    "send_email": bool(row["send_email"]),
                    "min_score_threshold": int(row["min_score_threshold"]),
                }
            else:
                result[row[0]] = {
                    "send_email": bool(row[1]),
                    "min_score_threshold": int(row[2]),
                }
        return result

    @staticmethod
    def _fetch_category_priorities(db_cursor) -> dict:
        db_cursor.execute("SELECT category, priority_order FROM risk_category_priority")
        rows = db_cursor.fetchall()
        result = {}
        for row in rows:
            if isinstance(row, dict):
                result[row["category"]] = int(row["priority_order"])
            else:
                result[row[0]] = int(row[1])
        return result

    @staticmethod
    def _fetch_category_rules(db_cursor) -> list:
        db_cursor.execute("SELECT entity_type, dependency_source, status, result_category FROM category_assignment_rules")
        rows = db_cursor.fetchall()
        result = []
        for row in rows:
            if isinstance(row, dict):
                result.append(row)
            else:
                result.append({
                    "entity_type": row[0],
                    "dependency_source": row[1],
                    "status": row[2],
                    "result_category": row[3]
                })
        return result
