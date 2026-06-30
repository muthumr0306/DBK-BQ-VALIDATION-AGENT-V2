from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

from .config import AppConfig, read_yaml


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    return {token.lower() for token in re.split(r"[^A-Za-z0-9]+", text) if token}


def _search_text(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, dict):
            parts.extend(f"{key} {_search_text(item)}" for key, item in value.items())
        elif isinstance(value, list):
            parts.extend(_search_text(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts)


class ContextRetriever(ABC):
    """Small replaceable contract for local now and vector/GCP retrieval later."""

    @abstractmethod
    def sync(self) -> dict[str, int]: ...

    @abstractmethod
    def rebuild(self) -> dict[str, int]: ...

    @abstractmethod
    def get_exact(self, **selectors: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def search(self, query: str | dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def context_package(self, query: dict[str, Any], limit: int | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def upsert_approved(self, records: list[dict[str, Any]]) -> int: ...


class LocalContextStore(ContextRetriever):
    def __init__(self, config: AppConfig, logger: logging.Logger | None = None):
        self.config = config
        self.path = config.path(config.project.context_store.path)
        self.logger = logger

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS context_records (
                record_id TEXT PRIMARY KEY,
                context_type TEXT NOT NULL,
                subject_key TEXT NOT NULL,
                pair_id TEXT,
                target_table TEXT,
                source_table TEXT,
                column_name TEXT,
                business_entity TEXT,
                table_type TEXT,
                payload_json TEXT NOT NULL,
                search_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                origin TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                confidence REAL NOT NULL DEFAULT 1.0,
                provenance_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_context_subject
                ON context_records(context_type, subject_key, origin);
            CREATE INDEX IF NOT EXISTS ix_context_pair ON context_records(pair_id);
            CREATE INDEX IF NOT EXISTS ix_context_table ON context_records(target_table);
            CREATE INDEX IF NOT EXISTS ix_context_column ON context_records(column_name);
            CREATE INDEX IF NOT EXISTS ix_context_entity ON context_records(business_entity);
            CREATE TABLE IF NOT EXISTS context_edges (
                edge_id TEXT PRIMARY KEY,
                source_key TEXT NOT NULL,
                target_key TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(record_id UNINDEXED, search_text)"
        )

    def _source_files(self) -> list[tuple[str, Path]]:
        project = self.config.project
        return [
            ("tables", self.config.path(project.context_tables)),
            ("relationships", self.config.path(project.relationships)),
            ("measures", self.config.path(project.measures)),
            ("patterns", self.config.path(project.context_patterns)),
            ("issue_patterns", self.config.path(project.issue_patterns)),
            ("learned", self.config.path(project.learned_context)),
        ]

    def _record(
        self,
        context_type: str,
        subject_key: str,
        payload: dict[str, Any],
        source: Path,
        *,
        pair_id: str | None = None,
        target_table: str | None = None,
        source_table: str | None = None,
        column_name: str | None = None,
        entity: str | None = None,
        table_type: str | None = None,
        origin: str = "DEVELOPER_CONTEXT",
        confidence: float = 1.0,
        version: int = 1,
        reviewed_by: str | None = None,
        reviewer_comments: str | None = None,
    ) -> dict[str, Any]:
        content_hash = _hash({
            "payload": payload, "version": version, "reviewed_by": reviewed_by,
            "reviewer_comments": reviewer_comments, "pair_id": pair_id,
            "target_table": target_table, "source_table": source_table,
            "column_name": column_name, "business_entity": entity,
            "table_type": table_type, "origin": origin, "confidence": confidence,
        })
        record_id = hashlib.sha256(
            f"{context_type}|{subject_key}|{origin}".encode("utf-8")
        ).hexdigest()[:32]
        provenance = {
            "source_file": str(source), "reviewed_by": reviewed_by,
            "reviewer_comments": reviewer_comments,
        }
        return {
            "record_id": record_id, "context_type": context_type,
            "subject_key": subject_key, "pair_id": pair_id,
            "target_table": target_table, "source_table": source_table,
            "column_name": column_name, "business_entity": entity,
            "table_type": table_type, "payload_json": _json(payload),
            "search_text": _search_text(subject_key, entity, table_type, payload),
            "content_hash": content_hash, "version": int(version),
            "origin": origin, "status": "ACTIVE", "confidence": float(confidence),
            "provenance_json": _json(provenance), "updated_at": _now(),
        }

    def _records_from_files(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        sources = dict(self._source_files())

        tables = read_yaml(sources["tables"], default={"tables": {}})
        for table_id, raw in (tables.get("tables") or {}).items():
            payload = dict(raw or {})
            target_table = payload.get("target_table")
            source_table = payload.get("source_table")
            entity = payload.get("business_entity")
            table_type = payload.get("table_type")
            records.append(self._record(
                "table", str(table_id), payload, sources["tables"], pair_id=str(table_id),
                target_table=target_table, source_table=source_table, entity=entity,
                table_type=table_type,
            ))
            for column, details in (payload.get("columns") or {}).items():
                column_payload = {"table_id": table_id, **dict(details or {})}
                records.append(self._record(
                    "column", f"{table_id}:{column}", column_payload, sources["tables"],
                    pair_id=str(table_id), target_table=target_table,
                    source_table=source_table, column_name=str(column), entity=entity,
                    table_type=table_type,
                ))

        relationships = read_yaml(sources["relationships"], default={})
        for dimension_id, raw in (relationships.get("dimensions") or {}).items():
            payload = {"dimension_id": dimension_id, **dict(raw or {})}
            records.append(self._record(
                "dimension", str(dimension_id), payload, sources["relationships"],
                target_table=payload.get("table"), entity=str(dimension_id), table_type="dimension",
            ))
        for raw in relationships.get("custom_relationships") or []:
            payload = dict(raw or {})
            relationship_id = str(payload.get("id") or _hash(payload)[:16])
            records.append(self._record(
                "relationship", relationship_id, payload, sources["relationships"],
                pair_id=str(payload.get("child_table") or "") or None,
                target_table=payload.get("parent_table"),
            ))
            edge_id = _hash([payload.get("child_table"), payload.get("parent_table"), relationship_id])[:32]
            edges.append({
                "edge_id": edge_id, "source_key": str(payload.get("child_table") or ""),
                "target_key": str(payload.get("parent_table") or ""),
                "edge_type": "relationship", "payload_json": _json(payload),
            })

        measures = read_yaml(sources["measures"], default={"measures": []})
        for raw in measures.get("measures") or []:
            payload = dict(raw or {})
            measure_id = str(payload.get("measure_id") or _hash(payload)[:16])
            records.append(self._record(
                "measure", f"{payload.get('pair_id')}:{measure_id}", payload, sources["measures"],
                pair_id=str(payload.get("pair_id") or "") or None,
                target_table=payload.get("target_table"), source_table=payload.get("source_table"),
                column_name=str(payload.get("target_expression") or "") or None,
            ))

        patterns = read_yaml(sources["patterns"], default={})
        for group, value in patterns.items():
            records.append(self._record("pattern", str(group), {group: value}, sources["patterns"]))
        issue_patterns = read_yaml(sources["issue_patterns"], default={"patterns": []})
        for raw in issue_patterns.get("patterns") or []:
            payload = dict(raw or {})
            records.append(self._record(
                "issue_pattern", str(payload.get("id") or _hash(payload)[:16]),
                payload, sources["issue_patterns"],
            ))

        learned = read_yaml(sources["learned"], default={"records": []})
        for raw in learned.get("records") or []:
            item = dict(raw or {})
            payload = dict(item.get("payload") or {})
            records.append(self._record(
                str(item.get("context_type") or "learned"),
                str(item.get("subject_key") or _hash(payload)[:16]), payload, sources["learned"],
                pair_id=item.get("pair_id"), target_table=item.get("target_table"),
                source_table=item.get("source_table"), column_name=item.get("column_name"),
                entity=item.get("business_entity"), table_type=item.get("table_type"),
                origin="APPROVED_LEARNING", confidence=float(item.get("confidence", 1.0)),
                version=int(item.get("version", 1)), reviewed_by=item.get("reviewed_by"),
                reviewer_comments=item.get("reviewer_comments"),
            ))
        return records, edges

    def rebuild(self) -> dict[str, int]:
        records, edges = self._records_from_files()
        connection = self._connect()
        try:
            self._schema(connection)
            connection.execute("DELETE FROM context_fts")
            connection.execute("DELETE FROM context_edges")
            connection.execute("DELETE FROM context_records")
            self._insert(connection, records, edges)
            connection.commit()
        finally:
            connection.close()
        if self.logger:
            self.logger.info("CONTEXT_REBUILD path=%s records=%s edges=%s", self.path, len(records), len(edges))
        return {"records": len(records), "edges": len(edges)}

    def sync(self) -> dict[str, int]:
        records, edges = self._records_from_files()
        connection = self._connect()
        inserted = updated = unchanged = 0
        try:
            self._schema(connection)
            existing = {
                row["record_id"]: row["content_hash"]
                for row in connection.execute("SELECT record_id, content_hash FROM context_records")
            }
            for record in records:
                previous = existing.get(record["record_id"])
                if previous == record["content_hash"]:
                    unchanged += 1
                    continue
                if previous:
                    updated += 1
                else:
                    inserted += 1
                self._insert(connection, [record], [])
            connection.execute("DELETE FROM context_edges")
            self._insert(connection, [], edges)
            active_ids = {record["record_id"] for record in records}
            for record_id in set(existing) - active_ids:
                connection.execute("DELETE FROM context_fts WHERE record_id=?", (record_id,))
                connection.execute("DELETE FROM context_records WHERE record_id=?", (record_id,))
            connection.commit()
        finally:
            connection.close()
        result = {"inserted": inserted, "updated": updated, "unchanged": unchanged, "edges": len(edges)}
        if self.logger:
            self.logger.info("CONTEXT_SYNC path=%s result=%s", self.path, result)
        return result

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        records: Iterable[dict[str, Any]],
        edges: Iterable[dict[str, Any]],
    ) -> None:
        columns = [
            "record_id", "context_type", "subject_key", "pair_id", "target_table",
            "source_table", "column_name", "business_entity", "table_type", "payload_json",
            "search_text", "content_hash", "version", "origin", "status", "confidence",
            "provenance_json", "updated_at",
        ]
        for record in records:
            connection.execute(
                f"INSERT OR REPLACE INTO context_records ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [record[column] for column in columns],
            )
            connection.execute("DELETE FROM context_fts WHERE record_id=?", (record["record_id"],))
            connection.execute(
                "INSERT INTO context_fts(record_id, search_text) VALUES (?, ?)",
                (record["record_id"], record["search_text"]),
            )
        for edge in edges:
            connection.execute(
                "INSERT OR REPLACE INTO context_edges(edge_id,source_key,target_key,edge_type,payload_json) VALUES (?,?,?,?,?)",
                (edge["edge_id"], edge["source_key"], edge["target_key"], edge["edge_type"], edge["payload_json"]),
            )

    @staticmethod
    def _decode(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        output = dict(row)
        output["payload"] = json.loads(output.pop("payload_json"))
        output["provenance"] = json.loads(output.pop("provenance_json"))
        return output

    def get_exact(self, **selectors: Any) -> list[dict[str, Any]]:
        allowed = {
            "record_id", "context_type", "subject_key", "pair_id", "target_table",
            "source_table", "column_name", "business_entity", "table_type", "origin", "status",
        }
        clauses: list[str] = ["status='ACTIVE'"]
        values: list[Any] = []
        for key, value in selectors.items():
            if key not in allowed or value in (None, ""):
                continue
            clauses.append(f"{key}=?")
            values.append(value)
        connection = self._connect()
        try:
            self._schema(connection)
            rows = connection.execute(
                "SELECT * FROM context_records WHERE " + " AND ".join(clauses) + " ORDER BY confidence DESC, version DESC",
                values,
            ).fetchall()
            return [self._decode(row) for row in rows]
        finally:
            connection.close()

    def _related_keys(self, keys: set[str]) -> set[str]:
        if not keys:
            return set()
        connection = self._connect()
        try:
            self._schema(connection)
            placeholders = ",".join("?" for _ in keys)
            rows = connection.execute(
                f"SELECT source_key,target_key FROM context_edges WHERE source_key IN ({placeholders}) OR target_key IN ({placeholders})",
                [*keys, *keys],
            ).fetchall()
            return {str(value) for row in rows for value in row if value}
        finally:
            connection.close()

    def search(self, query: str | dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
        if isinstance(query, str):
            query = {"description": query}
        limit = limit or self.config.project.context_store.top_k
        text = _search_text(query.get("pair_id"), query.get("target_table"), query.get("source_table"),
                            query.get("business_entity"), query.get("table_type"), query.get("description"),
                            query.get("columns"), query.get("failure_type"))
        query_tokens = _tokens(text)
        exact: dict[str, dict[str, Any]] = {}
        for selector in (
            {"pair_id": query.get("pair_id")}, {"target_table": query.get("target_table")},
            {"source_table": query.get("source_table")}, {"business_entity": query.get("business_entity")},
        ):
            if not next(iter(selector.values())):
                continue
            for record in self.get_exact(**selector):
                exact[record["record_id"]] = record

        connection = self._connect()
        candidates: dict[str, dict[str, Any]] = dict(exact)
        bm25_by_id: dict[str, float] = {}
        try:
            self._schema(connection)
            safe_terms = [token for token in query_tokens if len(token) > 1]
            if safe_terms and self.config.project.context_store.fts_enabled:
                expression = " OR ".join(f'"{term}"' for term in sorted(safe_terms))
                rows = connection.execute(
                    "SELECT r.*, bm25(context_fts) AS lexical_rank FROM context_fts "
                    "JOIN context_records r ON r.record_id=context_fts.record_id "
                    "WHERE context_fts MATCH ? AND r.status='ACTIVE' ORDER BY lexical_rank LIMIT ?",
                    (expression, max(limit * 6, 30)),
                ).fetchall()
                for row in rows:
                    decoded = self._decode(row)
                    candidates[decoded["record_id"]] = decoded
                    bm25_by_id[decoded["record_id"]] = float(row["lexical_rank"])
        finally:
            connection.close()

        related = self._related_keys({str(query.get("pair_id") or ""), str(query.get("target_table") or "")})
        query_columns = {str(item).lower() for item in query.get("columns", [])}
        scored: list[dict[str, Any]] = []
        for record in candidates.values():
            payload = record["payload"]
            record_tokens = _tokens(record.get("search_text") or _search_text(payload))
            overlap = len(query_tokens & record_tokens) / max(1, len(query_tokens | record_tokens))
            known_columns = {
                str(value).lower() for key, value in payload.items()
                if key.endswith("column") and isinstance(value, str)
            }
            if record.get("column_name"):
                known_columns.add(str(record["column_name"]).lower())
            for key, value in payload.items():
                if key.endswith("columns") and isinstance(value, list):
                    known_columns.update(str(item).lower() for item in value)
            known_columns.update(str(item).lower() for item in (payload.get("columns") or {}) if isinstance(payload.get("columns"), dict))
            column_overlap = len(query_columns & known_columns) / max(1, len(query_columns | known_columns)) if query_columns else 0.0
            identity = 1.0 if record["record_id"] in exact else 0.0
            relation = 1.0 if record.get("subject_key") in related or record.get("target_table") in related else 0.0
            lexical = 1.0 / (1.0 + abs(bm25_by_id.get(record["record_id"], 50.0)))
            trust = max(0.0, min(float(record.get("confidence", 0.0)), 1.0))
            if record.get("origin") in {"DEVELOPER_CONTEXT", "APPROVED_LEARNING"}:
                trust = 1.0
            score = (
                0.28 * identity + 0.15 * column_overlap + 0.20 * lexical
                + 0.15 * relation + 0.12 * overlap + 0.10 * trust
            )
            scored.append({**record, "retrieval_score": round(min(score, 1.0), 4)})
        scored.sort(key=lambda item: (item["retrieval_score"], item["confidence"], item["version"]), reverse=True)

        balanced: list[dict[str, Any]] = []
        per_type: dict[str, int] = {}
        for item in scored:
            context_type = str(item["context_type"])
            if per_type.get(context_type, 0) >= 3:
                continue
            balanced.append(item)
            per_type[context_type] = per_type.get(context_type, 0) + 1
            if len(balanced) >= limit:
                break
        if self.logger:
            self.logger.info("CONTEXT_RETRIEVAL query=%s returned=%s", query, len(balanced))
        return balanced

    def context_package(self, query: dict[str, Any], limit: int | None = None) -> dict[str, Any]:
        records = self.search(query, limit=limit)
        selected: list[dict[str, Any]] = []
        character_count = 0
        maximum = self.config.project.context_store.max_context_characters
        for record in records:
            item = {
                "record_id": record["record_id"], "context_type": record["context_type"],
                "subject_key": record["subject_key"], "payload": record["payload"],
                "score": record["retrieval_score"], "origin": record["origin"],
                "confidence": record["confidence"], "provenance": record["provenance"],
            }
            size = len(_json(item))
            if selected and character_count + size > maximum:
                break
            if size > maximum:
                item["payload"] = {"summary": record.get("search_text", "")[: maximum // 2], "truncated": True}
                size = len(_json(item))
            selected.append(item)
            character_count += size
        return {
            "query": query,
            "records": selected, "character_count": character_count,
            "truncated": len(selected) < len(records),
        }

    def upsert_approved(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        path = self.config.path(self.config.project.learned_context)
        document = read_yaml(path, default={"records": []})
        existing = list(document.get("records") or [])
        index = {
            (str(row.get("context_type")), str(row.get("subject_key"))): position
            for position, row in enumerate(existing)
        }
        for record in records:
            item = dict(record)
            key = (str(item.get("context_type") or "learned"), str(item.get("subject_key") or _hash(item)[:16]))
            previous = existing[index[key]] if key in index else None
            item["context_type"], item["subject_key"] = key
            item["version"] = int(previous.get("version", 0) + 1) if previous else 1
            item["approved_at"] = item.get("approved_at") or _now()
            if previous:
                existing[index[key]] = item
            else:
                index[key] = len(existing)
                existing.append(item)
        path.write_text(yaml.safe_dump({"records": existing}, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.sync()
        return len(records)


def make_context_retriever(config: AppConfig, logger: logging.Logger | None = None) -> LocalContextStore:
    return LocalContextStore(config, logger=logger)


# Compatibility functions used by the exploratory notebooks.
def ensure_context_objects(config: AppConfig, logger: logging.Logger | None = None) -> dict[str, Any]:
    store = make_context_retriever(config, logger)
    return {"database": str(store.path), **store.sync()}


def read_context(config: AppConfig, source: str = "active", logger: logging.Logger | None = None) -> pd.DataFrame:
    del source
    store = make_context_retriever(config, logger)
    store.sync()
    return pd.DataFrame(store.get_exact())


def import_approval_workbook(
    config: AppConfig, workbook_path: str | Path, logger: logging.Logger | None = None
) -> dict[str, Any]:
    from .context_utils import process_approval_workbook

    result = process_approval_workbook(config, Path(workbook_path))
    if logger:
        logger.info("APPROVAL_PROCESSED result=%s", result)
    return result
