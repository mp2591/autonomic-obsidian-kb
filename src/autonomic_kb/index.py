from __future__ import annotations

import fnmatch
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import KBConfig
from .markdown import parse_markdown
from .models import MemoryRecord
from .observability import EventLog
from .util import stable_json, terms, utc_now

SCHEMA_VERSION = 1


@dataclass(slots=True)
class IndexStats:
    scanned: int = 0
    indexed: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    malformed: int = 0
    duplicate_ids: int = 0
    fts5: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KnowledgeIndex:
    def __init__(self, config: KBConfig):
        self.config = config
        self.config.ensure_runtime()
        self.connection = sqlite3.connect(config.index_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.fts5 = False
        self._ensure_schema()
        self.events = EventLog(config.log_path)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "KnowledgeIndex":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                declared_id TEXT NOT NULL,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                scope TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                confidence REAL NOT NULL,
                authority TEXT NOT NULL,
                repo TEXT NOT NULL,
                project TEXT NOT NULL,
                module TEXT NOT NULL,
                branch TEXT NOT NULL,
                created TEXT NOT NULL,
                updated TEXT NOT NULL,
                validated TEXT NOT NULL,
                freshness TEXT NOT NULL,
                token_cost INTEGER NOT NULL,
                utility REAL NOT NULL,
                l0 TEXT NOT NULL,
                l1 TEXT NOT NULL,
                l2 TEXT NOT NULL,
                l3 TEXT NOT NULL,
                l4 TEXT NOT NULL,
                body TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                schema_valid INTEGER NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS links (
                source_id TEXT NOT NULL,
                target TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY(source_id, target, relation)
            );
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT NOT NULL,
                retrieval_id TEXT NOT NULL,
                task_hash TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,
                rank INTEGER NOT NULL,
                score REAL NOT NULL,
                tokens INTEGER NOT NULL,
                layer INTEGER NOT NULL,
                helpful INTEGER
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                retrieval_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                selected INTEGER NOT NULL,
                score REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS validation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                errors INTEGER NOT NULL,
                warnings INTEGER NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes(scope, repo, project, module, branch);
            CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
            CREATE INDEX IF NOT EXISTS idx_usage_note ON usage(note_id, retrieved_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_note ON decisions(note_id, created_at);
            """
        )
        try:
            self.connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                "note_id UNINDEXED, title, summary, l1, l2, l3, path, tokenize='porter unicode61')"
            )
            self.fts5 = True
        except sqlite3.OperationalError:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS notes_fts ("
                "note_id TEXT PRIMARY KEY, title TEXT, summary TEXT, l1 TEXT, l2 TEXT, l3 TEXT, path TEXT)"
            )
            self.fts5 = False
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),)
        )
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES('fts5', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if self.fts5 else "0",)
        )
        self.connection.commit()

    def _markdown_paths(self) -> Iterable[Path]:
        ignored_parts = {".git", ".obsidian", ".kb", "__pycache__", ".venv"}
        for path in self.config.vault.rglob("*.md"):
            relative = path.relative_to(self.config.vault)
            if any(part in ignored_parts for part in relative.parts):
                continue
            yield path

    def rebuild(self) -> IndexStats:
        self.connection.execute("DELETE FROM links")
        self.connection.execute("DELETE FROM notes")
        self.connection.execute("DELETE FROM notes_fts")
        self.connection.commit()
        return self.index_vault(force=True)

    def index_vault(self, force: bool = False) -> IndexStats:
        stats = IndexStats(fts5=self.fts5)
        existing = {row["path"]: dict(row) for row in self.connection.execute("SELECT path, source_hash, id FROM notes")}
        seen_paths: set[str] = set()
        declared_ids: dict[str, str] = {}
        for absolute in self._markdown_paths():
            relative = absolute.relative_to(self.config.vault).as_posix()
            seen_paths.add(relative)
            stats.scanned += 1
            text = absolute.read_text(encoding="utf-8", errors="replace")
            record = MemoryRecord.from_text(relative, text)
            if not record.schema_valid:
                stats.malformed += 1
            current = existing.get(relative)
            if current and current["source_hash"] == record.source_hash and not force:
                stats.unchanged += 1
                continue
            declared = record.id
            if declared in declared_ids and declared_ids[declared] != relative:
                stats.duplicate_ids += 1
                record.id = f"{declared}::duplicate::{record.source_hash[:8]}"
            else:
                declared_ids[declared] = relative
            self._upsert(record, declared)
            if current:
                stats.updated += 1
            else:
                stats.indexed += 1
        deleted = set(existing) - seen_paths
        for relative in deleted:
            note_id = existing[relative]["id"]
            self.connection.execute("DELETE FROM links WHERE source_id=?", (note_id,))
            self.connection.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))
            self.connection.execute("DELETE FROM notes WHERE path=?", (relative,))
            stats.deleted += 1
        self.connection.commit()
        self.events.emit("index.completed", **stats.to_dict())
        return stats

    def _upsert(self, record: MemoryRecord, declared_id: str) -> None:
        previous = self.connection.execute("SELECT id FROM notes WHERE path=?", (record.path,)).fetchone()
        if previous and previous["id"] != record.id:
            self.connection.execute("DELETE FROM notes WHERE path=?", (record.path,))
            self.connection.execute("DELETE FROM notes_fts WHERE note_id=?", (previous["id"],))
            self.connection.execute("DELETE FROM links WHERE source_id=?", (previous["id"],))
        layers = {level: record.layers.get(level, "") for level in range(5)}
        values = (
            record.id, declared_id, record.path, record.title, record.type, record.scope, record.status,
            record.summary, record.confidence, record.authority, record.repo, record.project, record.module,
            record.branch, record.created, record.updated, record.validated, record.freshness,
            record.token_cost, record.utility, layers[0], layers[1], layers[2], layers[3], layers[4],
            record.body, stable_json(record.metadata), record.source_hash, int(record.schema_valid), utc_now(),
        )
        self.connection.execute(
            """
            INSERT INTO notes(
                id, declared_id, path, title, type, scope, status, summary, confidence, authority,
                repo, project, module, branch, created, updated, validated, freshness, token_cost,
                utility, l0, l1, l2, l3, l4, body, metadata_json, source_hash, schema_valid, indexed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                declared_id=excluded.declared_id, path=excluded.path, title=excluded.title,
                type=excluded.type, scope=excluded.scope, status=excluded.status,
                summary=excluded.summary, confidence=excluded.confidence, authority=excluded.authority,
                repo=excluded.repo, project=excluded.project, module=excluded.module,
                branch=excluded.branch, created=excluded.created, updated=excluded.updated,
                validated=excluded.validated, freshness=excluded.freshness,
                token_cost=excluded.token_cost, utility=excluded.utility, l0=excluded.l0,
                l1=excluded.l1, l2=excluded.l2, l3=excluded.l3, l4=excluded.l4,
                body=excluded.body, metadata_json=excluded.metadata_json,
                source_hash=excluded.source_hash, schema_valid=excluded.schema_valid,
                indexed_at=excluded.indexed_at
            """, values
        )
        self.connection.execute("DELETE FROM notes_fts WHERE note_id=?", (record.id,))
        self.connection.execute(
            "INSERT INTO notes_fts(note_id,title,summary,l1,l2,l3,path) VALUES(?,?,?,?,?,?,?)",
            (record.id, record.title, record.summary, layers[1], layers[2], layers[3], record.path),
        )
        self.connection.execute("DELETE FROM links WHERE source_id=?", (record.id,))
        parsed = parse_markdown(record.body)
        for target in parsed.links:
            self.connection.execute(
                "INSERT OR IGNORE INTO links(source_id,target,relation) VALUES(?,?,?)",
                (record.id, target, "related-to"),
            )
        relations = record.metadata.get("relations", {})
        if isinstance(relations, dict):
            for relation, targets in relations.items():
                if isinstance(targets, str):
                    targets = [targets]
                if isinstance(targets, list):
                    for target in targets:
                        self.connection.execute(
                            "INSERT OR IGNORE INTO links(source_id,target,relation) VALUES(?,?,?)",
                            (record.id, str(target), str(relation).replace("_", "-")),
                        )

    def get(self, memory_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT n.*, COALESCE(u.uses,0) AS uses, u.last_used FROM notes n "
            "LEFT JOIN (SELECT note_id, COUNT(*) uses, MAX(retrieved_at) last_used FROM usage GROUP BY note_id) u "
            "ON n.id=u.note_id WHERE n.id=? OR n.declared_id=? LIMIT 1",
            (memory_id, memory_id),
        ).fetchone()
        return self._row(row) if row else None

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM notes WHERE path=?", (path,)).fetchone()
        return self._row(row) if row else None

    def all_notes(self, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.connection.execute(
                f"SELECT * FROM notes WHERE status IN ({placeholders}) ORDER BY path", tuple(statuses)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM notes ORDER BY path").fetchall()
        return [self._row(row) for row in rows]

    def search(self, query: str, limit: int = 200) -> list[dict[str, Any]]:
        query_terms = terms(query)
        if not query_terms:
            rows = self.connection.execute("SELECT * FROM notes LIMIT ?", (limit,)).fetchall()
            result = [self._row(row) for row in rows]
            for item in result:
                item["lexical"] = 0.2
            return result
        if self.fts5:
            fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in query_terms[:24])
            try:
                matches = self.connection.execute(
                    "SELECT note_id, bm25(notes_fts, 3.0, 2.5, 2.2, 1.8, 1.0, 0.8) AS rank "
                    "FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                matches = []
            result: list[dict[str, Any]] = []
            for position, match in enumerate(matches):
                row = self.connection.execute("SELECT * FROM notes WHERE id=?", (match["note_id"],)).fetchone()
                if row:
                    item = self._row(row)
                    item["lexical"] = 1.0 / (1.0 + position * 0.22)
                    item["bm25"] = float(match["rank"])
                    result.append(item)
            if result:
                return result
        clauses = " OR ".join("LOWER(title || ' ' || summary || ' ' || l2 || ' ' || path) LIKE ?" for _ in query_terms)
        params = tuple(f"%{term.lower()}%" for term in query_terms) + (limit,)
        rows = self.connection.execute(f"SELECT * FROM notes WHERE {clauses} LIMIT ?", params).fetchall()
        result = [self._row(row) for row in rows]
        for item in result:
            haystack = f"{item['title']} {item['summary']} {item['l2']} {item['path']}".lower()
            hits = sum(1 for term in query_terms if term in haystack)
            item["lexical"] = hits / max(1, len(query_terms))
        return sorted(result, key=lambda item: item["lexical"], reverse=True)

    def neighbors(self, memory_ids: list[str], limit: int = 30) -> list[dict[str, Any]]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self.connection.execute(
            f"SELECT * FROM links WHERE source_id IN ({placeholders}) LIMIT ?", (*memory_ids, limit)
        ).fetchall()
        result: list[dict[str, Any]] = []
        seen: set[str] = set(memory_ids)
        for link in rows:
            target = str(link["target"])
            row = self.connection.execute(
                "SELECT * FROM notes WHERE id=? OR declared_id=? OR path=? OR title=? LIMIT 1",
                (target, target, target if target.endswith('.md') else target + '.md', Path(target).stem),
            ).fetchone()
            if row and row["id"] not in seen:
                item = self._row(row)
                item["graph_relation"] = link["relation"]
                item["graph_source"] = link["source_id"]
                item["lexical"] = 0.0
                result.append(item)
                seen.add(row["id"])
        return result

    def record_decisions(self, retrieval_id: str, decisions: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.connection.executemany(
            "INSERT INTO decisions(retrieval_id,note_id,selected,score,reason,created_at) VALUES(?,?,?,?,?,?)",
            [
                (
                    retrieval_id, item["id"], int(item.get("selected", False)), float(item.get("score", 0.0)),
                    "; ".join(item.get("reasons", [])), now,
                )
                for item in decisions
            ],
        )
        self.connection.commit()

    def record_usage(self, retrieval_id: str, task_hash: str, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.connection.executemany(
            "INSERT INTO usage(note_id,retrieval_id,task_hash,retrieved_at,rank,score,tokens,layer) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                (item["id"], retrieval_id, task_hash, now, rank, item["score"], item["tokens"], item["layer"])
                for rank, item in enumerate(items, 1)
            ],
        )
        self.connection.commit()

    def why(self, memory_id: str) -> dict[str, Any]:
        note = self.get(memory_id)
        rows = self.connection.execute(
            "SELECT retrieval_id,selected,score,reason,created_at FROM decisions "
            "WHERE note_id=? ORDER BY id DESC LIMIT 10", (note["id"] if note else memory_id,)
        ).fetchall()
        return {"memory": note, "recent_decisions": [dict(row) for row in rows]}

    def stats(self) -> dict[str, Any]:
        total = self.connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        by_status = {row[0]: row[1] for row in self.connection.execute("SELECT status,COUNT(*) FROM notes GROUP BY status")}
        by_scope = {row[0]: row[1] for row in self.connection.execute("SELECT scope,COUNT(*) FROM notes GROUP BY scope")}
        use = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens),0), COUNT(DISTINCT retrieval_id) FROM usage"
        ).fetchone()
        return {
            "notes": total,
            "by_status": by_status,
            "by_scope": by_scope,
            "retrieved_items": use[0],
            "retrieved_tokens": use[1],
            "retrievals": use[2],
            "fts5": self.fts5,
            "index_path": str(self.config.index_path),
        }

    def save_validation(self, report: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO validation_runs(created_at,errors,warnings,report_json) VALUES(?,?,?,?)",
            (utc_now(), report.get("errors", 0), report.get("warnings", 0), stable_json(report)),
        )
        self.connection.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json", "{}"))
        except json.JSONDecodeError:
            item["metadata"] = {}
        return item
