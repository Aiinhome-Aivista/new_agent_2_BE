import time
import threading
import mysql.connector
from mysql.connector import Error, pooling
from core.config import settings
from .base import BaseDBProvider

class DefaultDBProvider(BaseDBProvider):
    _pool = None
    _lock = threading.Lock()
    _last_pool_attempt = 0
    _POOL_RETRY_INTERVAL = 30  # seconds

    @classmethod
    def _init_pool(cls):
        now = time.time()
        if cls._pool is None or (cls._pool is False and (now - cls._last_pool_attempt > cls._POOL_RETRY_INTERVAL)):
            with cls._lock:
                if cls._pool is None or (cls._pool is False and (now - cls._last_pool_attempt > cls._POOL_RETRY_INTERVAL)):
                    cls._last_pool_attempt = now
                    try:
                        cls._pool = pooling.MySQLConnectionPool(
                            pool_name="acse_default_pool",
                            pool_size=10,
                            pool_reset_session=True,
                            host=settings.DB_HOST,
                            port=settings.DB_PORT,
                            database=settings.DB_NAME,
                            user=settings.DB_USER,
                            password=settings.DB_PASSWORD,
                            charset='utf8mb4',
                            collation='utf8mb4_unicode_ci',
                            connect_timeout=5,
                        )
                    except Exception as e:
                        print(f"Warning: Failed to initialize MySQL connection pool: {e}")
                        cls._pool = False
        return cls._pool

    def get_connection(self):
        pool = self._init_pool()
        if pool:
            try:
                conn = pool.get_connection()
                if conn and conn.is_connected():
                    try:
                        conn.ping(reconnect=True, attempts=1, delay=0.1)
                    except Exception:
                        pass
                    return conn
            except Exception as e:
                # If pool is temporarily exhausted or unavailable, fall back to direct connection
                pass

        # Fallback to direct connection
        max_retries = 2
        retry_delay = 0.2  # seconds
        for attempt in range(max_retries):
            try:
                connection = mysql.connector.connect(
                    host=settings.DB_HOST,
                    port=settings.DB_PORT,
                    database=settings.DB_NAME,
                    user=settings.DB_USER,
                    password=settings.DB_PASSWORD,
                    charset='utf8mb4',
                    collation='utf8mb4_unicode_ci',
                    connect_timeout=5,
                    connection_timeout=5
                )
                if connection.is_connected():
                    return connection
            except Error as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    print(f"Error connecting to Default MySQL after {max_retries} attempts: {e}")
                    return None
        return None
