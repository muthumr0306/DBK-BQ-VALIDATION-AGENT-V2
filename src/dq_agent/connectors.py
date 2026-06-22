from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .config import QueryLimits, TablePair


@dataclass
class ColumnMetadata:
    name: str
    data_type: str
    nullable: bool = True
    description: str | None = None
    ordinal_position: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "description": self.description,
            "ordinal_position": self.ordinal_position,
        }


@dataclass
class QueryResult:
    frame: pd.DataFrame
    query_id: str | None = None
    bytes_processed: int | None = None
    executed_at: str = ""

    def __post_init__(self) -> None:
        if not self.executed_at:
            self.executed_at = datetime.now(timezone.utc).isoformat()


class WarehouseConnector(ABC):
    dialect: str

    def __init__(self, limits: QueryLimits):
        self.limits = limits

    @abstractmethod
    def get_columns(self, pair: TablePair, side: str) -> list[ColumnMetadata]:
        raise NotImplementedError

    @abstractmethod
    def execute(self, sql: str) -> QueryResult:
        raise NotImplementedError

    def get_table_metadata(self, qualified_name: str) -> dict[str, Any]:
        return {"table": qualified_name}

    def close(self) -> None:
        return None


class DatabricksConnector(WarehouseConnector):
    dialect = "databricks"

    def __init__(self, limits: QueryLimits):
        super().__init__(limits)
        self._connection: Any = None

    def _connect(self) -> Any:
        if self._connection is not None:
            return self._connection
        try:
            from databricks import sql as dbsql
        except ImportError as exc:
            raise RuntimeError("Install databricks-sql-connector to query Databricks") from exc
        hostname = os.getenv("DATABRICKS_SERVER_HOSTNAME")
        http_path = os.getenv("DATABRICKS_HTTP_PATH")
        token = os.getenv("DATABRICKS_TOKEN")
        if not hostname or not http_path:
            raise RuntimeError("DATABRICKS_SERVER_HOSTNAME and DATABRICKS_HTTP_PATH are required")
        kwargs: dict[str, Any] = {"server_hostname": hostname, "http_path": http_path}
        if token:
            kwargs["access_token"] = token
        self._connection = dbsql.connect(**kwargs)
        return self._connection

    def get_columns(self, pair: TablePair, side: str = "source") -> list[ColumnMetadata]:
        if side != "source" or not pair.source_name:
            raise ValueError("DatabricksConnector only reads source metadata")
        sql = (
            f"SELECT column_name, full_data_type, is_nullable, comment, ordinal_position "
            f"FROM `{pair.source_catalog}`.information_schema.columns "
            f"WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position"
        )
        with self._connect().cursor() as cursor:
            self._execute_with_timeout(cursor, sql, [pair.source_schema, pair.source_table])
            rows = cursor.fetchall()
        return [
            ColumnMetadata(
                name=row[0],
                data_type=row[1],
                nullable=str(row[2]).upper() == "YES",
                description=row[3],
                ordinal_position=int(row[4]),
            )
            for row in rows
        ]

    def get_table_metadata(self, qualified_name: str) -> dict[str, Any]:
        parts = qualified_name.split(".")
        if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$-]*", part) for part in parts):
            raise ValueError(f"Unsafe Databricks table name: {qualified_name!r}")
        sql = "DESCRIBE DETAIL " + ".".join(f"`{part}`" for part in parts)
        with self._connect().cursor() as cursor:
            self._execute_with_timeout(cursor, sql)
            rows = cursor.fetchmany(1)
            columns = [item[0] for item in cursor.description] if cursor.description else []
        detail = dict(zip(columns, rows[0])) if rows else {}
        return {
            "table": qualified_name,
            "description": detail.get("description"),
            "created_at": detail.get("createdAt"),
            "modified_at": detail.get("lastModified"),
            "num_rows": detail.get("numRows"),
            "location": detail.get("location"),
            "format": detail.get("format"),
        }

    def _execute_with_timeout(self, cursor: Any, sql: str, parameters: Any = None) -> None:
        cursor.execute_async(sql, parameters)
        started = time.monotonic()
        while cursor.is_query_pending():
            if time.monotonic() - started >= self.limits.timeout_seconds:
                cursor.cancel()
                raise TimeoutError(
                    f"Databricks query exceeded timeout_seconds={self.limits.timeout_seconds}"
                )
            time.sleep(0.5)
        cursor.get_async_execution_result()

    def execute(self, sql: str) -> QueryResult:
        with self._connect().cursor() as cursor:
            self._execute_with_timeout(cursor, sql)
            rows = cursor.fetchmany(self.limits.max_result_rows + 1)
            if len(rows) > self.limits.max_result_rows:
                raise RuntimeError(
                    f"Databricks result exceeded max_result_rows={self.limits.max_result_rows}"
                )
            columns = [item[0] for item in cursor.description] if cursor.description else []
            query_id = getattr(cursor, "query_id", None)
        return QueryResult(frame=pd.DataFrame(rows, columns=columns), query_id=query_id)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


class BigQueryConnector(WarehouseConnector):
    dialect = "bigquery"

    def __init__(self, limits: QueryLimits, project: str | None = None):
        super().__init__(limits)
        self.project = project
        self._client: Any = None

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("Install google-cloud-bigquery to query BigQuery") from exc
        self._client = bigquery.Client(project=self.project)
        return self._client

    def get_columns(self, pair: TablePair, side: str = "target") -> list[ColumnMetadata]:
        if side != "target":
            raise ValueError("BigQueryConnector only reads target metadata")
        table = self._connect().get_table(pair.target_name)
        return [
            ColumnMetadata(
                name=field.name,
                data_type=field.field_type,
                nullable=field.mode != "REQUIRED",
                description=field.description,
                ordinal_position=index + 1,
            )
            for index, field in enumerate(table.schema)
        ]

    def get_table_metadata(self, qualified_name: str) -> dict[str, Any]:
        """Return the compact metadata shape used by relationship checks."""
        table = self._connect().get_table(qualified_name)
        return {
            "table": qualified_name,
            "location": table.location,
            "description": table.description,
            "created_at": table.created,
            "modified_at": table.modified,
            "num_rows": table.num_rows,
            "columns": [
                ColumnMetadata(
                    name=field.name,
                    data_type=field.field_type,
                    nullable=field.mode != "REQUIRED",
                    description=field.description,
                    ordinal_position=index + 1,
                ).as_dict()
                for index, field in enumerate(table.schema)
            ],
        }

    def execute(self, sql: str) -> QueryResult:
        from google.cloud import bigquery

        client = self._connect()
        dry_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        dry_job = client.query(sql, job_config=dry_config)
        estimated = int(dry_job.total_bytes_processed or 0)
        if estimated > self.limits.max_bytes_billed:
            raise RuntimeError(
                f"BigQuery dry run estimated {estimated} bytes, above "
                f"max_bytes_billed={self.limits.max_bytes_billed}"
            )
        config = bigquery.QueryJobConfig(
            use_query_cache=True,
            maximum_bytes_billed=self.limits.max_bytes_billed,
        )
        job = client.query(sql, job_config=config)
        iterator = job.result(
            timeout=self.limits.timeout_seconds,
            max_results=self.limits.max_result_rows + 1,
        )
        rows = list(iterator)
        if len(rows) > self.limits.max_result_rows:
            raise RuntimeError(
                f"BigQuery result exceeded max_result_rows={self.limits.max_result_rows}"
            )
        frame = pd.DataFrame([dict(row.items()) for row in rows])
        return QueryResult(
            frame=frame,
            query_id=job.job_id,
            bytes_processed=int(job.total_bytes_processed or estimated),
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def make_connectors(limits: QueryLimits, bq_project: str | None = None) -> dict[str, WarehouseConnector]:
    return {
        "source": DatabricksConnector(limits),
        "target": BigQueryConnector(limits, project=bq_project),
    }
