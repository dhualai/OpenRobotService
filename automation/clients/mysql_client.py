from typing import Any, Optional, Tuple

from automation.config import load_config
from automation.config.models import DatabaseConfig
from automation.clients.base import BaseClient
from automation.utils.retry import sync_retry, RetryConfig
from automation.clients.exceptions import ClientConnectionError, QueryError


class MySQLClient(BaseClient):
    """MySQL database client with retry, logging, and exception handling.

    Wraps pymysql for executing SQL queries against the backend database.
    """

    def __init__(self, config: Optional[DatabaseConfig] = None, retry_config: Optional[RetryConfig] = None):
        super().__init__(name="MySQLClient")
        self._cfg = config or load_config().database
        self._retry_cfg = retry_config or RetryConfig()
        self._connection: Any = None
        self._cursor: Any = None

    def __enter__(self) -> "MySQLClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def connect(self) -> None:
        import pymysql
        self._connection = pymysql.connect(
            host=self._cfg.host,
            port=self._cfg.port,
            user=self._cfg.user,
            password=self._cfg.password,
            database=self._cfg.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        self._cursor = self._connection.cursor()
        self._connected = True
        self._log.info("Connected to MySQL: %s:%s/%s", self._cfg.host, self._cfg.port, self._cfg.database)

    def close(self) -> None:
        if self._cursor:
            try:
                self._cursor.close()
            except Exception:
                pass
            self._cursor = None
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
            self._connected = False
            self._log.info("MySQL client disconnected")

    def execute(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> int:
        """Execute a SQL query with retry and logging.

        Args:
            query: SQL query string
            params: Query parameters for parameterized queries

        Returns:
            Number of affected rows

        Raises:
            ClientConnectionError: If the database is not connected
            QueryError: If the query fails
        """
        if not self._connection:
            raise ClientConnectionError("MySQL not connected", host=self._cfg.host, port=self._cfg.port)

        self._log.debug("Execute: %s | params: %s", query[:100], params)
        try:
            affected = self._execute_with_retry(query, params)
            self._connection.commit()
            self._log.debug("Affected rows: %s", affected)
            return affected
        except Exception as e:
            self._connection.rollback()
            raise self._wrap_query_error(e, query=query)

    def fetch_one(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> Optional[dict]:
        """Execute a query and fetch one result row."""
        self.execute(query, params)
        if self._cursor:
            return self._cursor.fetchone()
        return None

    def fetch_all(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> list:
        """Execute a query and fetch all result rows."""
        self.execute(query, params)
        if self._cursor:
            return self._cursor.fetchall()
        return []

    @sync_retry()
    def _execute_with_retry(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> int:
        if not self._cursor:
            raise ClientConnectionError("MySQL cursor not available", host=self._cfg.host, port=self._cfg.port)
        self._cursor.execute(query, params)
        return self._cursor.rowcount

