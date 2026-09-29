from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import KBConfig
from .evidence import EvidenceStore
from .models import MemoryRecord
from .observability import EventLog
from .security import scan_content
from .storage import recover_transactions, vault_lock, vault_markdown_paths
from .util import sha256_text, stable_json, terms, utc_now

SCHEMA_VERSION = 2
# Bump whenever parsing or index-time verdicts (such as the content safety scan) change;
# unchanged notes are then re-read once instead of trusting stale derived columns.
PARSER_FORMAT = "v3.2.0"


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
        self.connection = sqlite3.connect(config.index_path, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.fts5 = False
        self._ensure_schema()
        parser = self.connection.execute("SELECT value FROM meta WHERE key='parser_format'").fetchone()
        self._requires_reparse = not parser or parser[0] != PARSER_FORMAT
        self.events = EventLog(config.log_path)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> KnowledgeIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        prior = self.connection.execute("PRAGMA user_version").fetchone()[0]
        existing_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(notes)").fetchall()}
        legacy_shape = bool(existing_columns) and "schema_version" not in existing_columns
        if prior not in (0, SCHEMA_VERSION) or legacy_shape:
            # The index is explicitly disposable; this avoids fragile in-place migrations.
            self.connection.executescript("""
                DROP TABLE IF EXISTS notes_fts;
                DROP TABLE IF EXISTS file_manifest;
                DROP TABLE IF EXISTS links;
                DROP TABLE IF EXISTS usage;
                DROP TABLE IF EXISTS decisions;
                DROP TABLE IF EXISTS validation_runs;
                DROP TABLE IF EXISTS notes;
                DROP TABLE IF EXISTS meta;
            """)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS file_manifest (
                path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, file_size INTEGER NOT NULL, source_hash TEXT NOT NULL,
                inode INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY, declared_id TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
                schema_version INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, type TEXT NOT NULL,
                scope TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, confidence REAL NOT NULL,
                authority TEXT NOT NULL, repo TEXT NOT NULL, repository_id TEXT NOT NULL, project TEXT NOT NULL,
                module TEXT NOT NULL, branch TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                validated TEXT NOT NULL, freshness TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT NOT NULL,
                as_of_commit TEXT NOT NULL, version_range TEXT NOT NULL, taint TEXT NOT NULL,
                authorized_instruction INTEGER NOT NULL, token_cost INTEGER NOT NULL, utility REAL NOT NULL,
                l0 TEXT NOT NULL, l1 TEXT NOT NULL, l2 TEXT NOT NULL, l3 TEXT NOT NULL, l4 TEXT NOT NULL,
                body TEXT NOT NULL, metadata_json TEXT NOT NULL, source_hash TEXT NOT NULL,
                schema_valid INTEGER NOT NULL, indexed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS links (
                source_id TEXT NOT NULL, target TEXT NOT NULL, target_id TEXT NOT NULL DEFAULT '',
                relation TEXT NOT NULL, provenance TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(source_id,target,relation)
            );
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, note_id TEXT NOT NULL, retrieval_id TEXT NOT NULL,
                task_hash TEXT NOT NULL, retrieved_at TEXT NOT NULL, rank INTEGER NOT NULL,
                score REAL NOT NULL, tokens INTEGER NOT NULL, layer INTEGER NOT NULL, helpful INTEGER
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, retrieval_id TEXT NOT NULL, note_id TEXT NOT NULL,
                selected INTEGER NOT NULL, score REAL NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS validation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, errors INTEGER NOT NULL,
                warnings INTEGER NOT NULL, report_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_checks (
                evidence_id TEXT PRIMARY KEY, inode INTEGER NOT NULL, file_size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL, ctime_ns INTEGER NOT NULL, valid INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rank_examples (
                retrieval_id TEXT NOT NULL, note_id TEXT NOT NULL, feature_json TEXT NOT NULL,
                selected INTEGER NOT NULL, label REAL, created_at TEXT NOT NULL,
                PRIMARY KEY(retrieval_id,note_id)
            );
            CREATE INDEX IF NOT EXISTS idx_notes_scope ON notes(scope, repository_id, repo, project, module, branch);
            CREATE INDEX IF NOT EXISTS idx_notes_status ON notes(status);
            CREATE INDEX IF NOT EXISTS idx_notes_kind ON notes(kind,type);
            CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path);
            CREATE INDEX IF NOT EXISTS idx_usage_note ON usage(note_id,retrieved_at);
            CREATE INDEX IF NOT EXISTS idx_decisions_note ON decisions(note_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_id,target);
            """
        )
        note_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(notes)")}
        if "unsafe" not in note_columns:
            # Fail closed until the parser-format bump re-reads every note and stores its verdict.
            self.connection.execute("ALTER TABLE notes ADD COLUMN unsafe INTEGER NOT NULL DEFAULT 1")
        manifest_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(file_manifest)")}
        if "inode" not in manifest_columns:
            # Replacing a file (as atomic writers do) changes its inode even when the coarse
            # mtime and the size are unchanged; existing rows are re-verified once.
            self.connection.execute("ALTER TABLE file_manifest ADD COLUMN inode INTEGER NOT NULL DEFAULT 0")
        try:
            self.connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5("
                "note_id UNINDEXED, title, summary, l1, l2, l3, path, tokenize='porter unicode61')"
            )
            self.fts5 = True
        except sqlite3.OperationalError:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS notes_fts ("
                "note_id TEXT PRIMARY KEY,title TEXT,summary TEXT,l1 TEXT,l2 TEXT,l3 TEXT,path TEXT)"
            )
            self.fts5 = False
        self.connection.execute("PRAGMA user_version=2")
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES('fts5',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("1" if self.fts5 else "0",),
        )
        self.connection.commit()

    def _markdown_paths(self) -> Iterable[Path]:
        return vault_markdown_paths(self.config.vault)

    def rebuild(self) -> IndexStats:
        with self.connection:
            self.connection.execute("DELETE FROM links")
            self.connection.execute("DELETE FROM notes")
            self.connection.execute("DELETE FROM notes_fts")
            self.connection.execute("DELETE FROM file_manifest")
        return self.index_vault(force=True)

    def index_vault(self, force: bool = False) -> IndexStats:
        with vault_lock(self.config.vault):
            recover_transactions(self.config.vault)
            result = self._index_vault(force or self._requires_reparse)
            if self._requires_reparse:
                self.connection.execute(
                    "INSERT INTO meta(key,value) VALUES('parser_format',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (PARSER_FORMAT,),
                )
                self.connection.commit()
                self._requires_reparse = False
            return result

    def _index_vault(self, force: bool = False) -> IndexStats:
        stats = IndexStats(fts5=self.fts5)
        existing = {
            row["path"]: dict(row)
            for row in self.connection.execute("SELECT path,source_hash,id,declared_id FROM notes")
        }
        manifest = {
            row["path"]: dict(row)
            for row in self.connection.execute("SELECT path,mtime_ns,file_size,source_hash,inode FROM file_manifest")
        }
        seen_paths: set[str] = set()
        changed: dict[str, tuple[MemoryRecord, os.stat_result]] = {}
        declared_by_path: dict[str, str] = {}
        refreshed: list[tuple[str, os.stat_result, str]] = []
        for absolute in self._markdown_paths():
            relative = absolute.relative_to(self.config.vault).as_posix()
            seen_paths.add(relative)
            stats.scanned += 1
            stat = absolute.stat()
            current = existing.get(relative)
            cached = manifest.get(relative)
            if current and cached and not force and self._unchanged(cached, stat):
                stats.unchanged += 1
                declared_by_path[relative] = str(current.get("declared_id") or current["id"])
                continue
            record = self._parse(absolute, relative)
            if not record.schema_valid:
                stats.malformed += 1
            declared_by_path[relative] = record.id
            if current and current["source_hash"] == record.source_hash and not force:
                refreshed.append((relative, stat, record.source_hash))
                stats.unchanged += 1
                continue
            changed[relative] = (record, stat)
        desired = self._assign_identities(declared_by_path, existing)
        stats.duplicate_ids = sum(1 for path, identity in desired.items() if identity != declared_by_path[path])
        # An unchanged note whose row identity must change (a duplicate was added or its
        # owner removed) is re-read so no row is ever rewritten under another path.
        for relative, identity in desired.items():
            if relative not in changed and existing.get(relative) and existing[relative]["id"] != identity:
                absolute = self.config.vault / relative
                changed[relative] = (self._parse(absolute, relative), absolute.stat())
        removed = set(existing) - seen_paths
        with self.connection:
            for relative in removed | {path for path in changed if path in existing}:
                self._delete_row(existing[relative]["id"], relative)
            for relative in removed:
                self.connection.execute("DELETE FROM file_manifest WHERE path=?", (relative,))
                stats.deleted += 1
            for relative in sorted(changed):
                record, stat = changed[relative]
                declared = declared_by_path[relative]
                record.id = desired[relative]
                self._insert(record, declared)
                self._record_manifest(relative, stat, record.source_hash)
                if relative in existing:
                    stats.updated += 1
                else:
                    stats.indexed += 1
            for relative, stat, source_hash in refreshed:
                self._record_manifest(relative, stat, source_hash)
            if changed or removed:
                self._resolve_link_targets()
        self.events.emit("index.completed", **stats.to_dict())
        return stats

    @staticmethod
    def _unchanged(cached: dict[str, Any], stat: os.stat_result) -> bool:
        return (
            int(cached["mtime_ns"]) == stat.st_mtime_ns
            and int(cached["file_size"]) == stat.st_size
            and int(cached.get("inode") or 0) == stat.st_ino
        )

    @staticmethod
    def _parse(absolute: Path, relative: str) -> MemoryRecord:
        return MemoryRecord.from_text(relative, absolute.read_text(encoding="utf-8", errors="replace"))

    @staticmethod
    def _assign_identities(declared_by_path: dict[str, str], existing: dict[str, dict[str, Any]]) -> dict[str, str]:
        """Give every path a unique row identity without moving an identity between paths.

        The note already holding a declared identity keeps it; otherwise the first path in
        sorted order owns it. Every other declarer gets a path-derived duplicate identity,
        and the read gate excludes all declarers while more than one exists.
        """
        by_declared: dict[str, list[str]] = {}
        for relative in sorted(declared_by_path):
            by_declared.setdefault(declared_by_path[relative], []).append(relative)
        desired: dict[str, str] = {}
        for declared, paths in by_declared.items():
            owner = next((path for path in paths if existing.get(path, {}).get("id") == declared), paths[0])
            for path in paths:
                desired[path] = declared if path == owner else f"{declared}::duplicate::{sha256_text(path)[:8]}"
        return desired

    def _record_manifest(self, relative: str, stat: os.stat_result, source_hash: str) -> None:
        self.connection.execute(
            "INSERT INTO file_manifest(path,mtime_ns,file_size,source_hash,inode) VALUES(?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, file_size=excluded.file_size, "
            "source_hash=excluded.source_hash, inode=excluded.inode",
            (relative, stat.st_mtime_ns, stat.st_size, source_hash, stat.st_ino),
        )

    def _delete_row(self, note_id: str, relative: str) -> None:
        self.connection.execute("DELETE FROM links WHERE source_id=?", (note_id,))
        self.connection.execute("DELETE FROM notes_fts WHERE note_id=?", (note_id,))
        self.connection.execute("DELETE FROM notes WHERE path=?", (relative,))

    def _insert(self, record: MemoryRecord, declared_id: str) -> None:
        layers = {level: record.layers.get(level, "") for level in range(5)}
        values = (
            record.id,
            declared_id,
            record.path,
            record.schema_version,
            record.kind,
            record.title,
            record.type,
            record.scope,
            record.status,
            record.summary,
            record.confidence,
            record.authority,
            record.repo,
            record.repository_id,
            record.project,
            record.module,
            record.branch,
            record.created,
            record.updated,
            record.validated,
            record.freshness,
            record.valid_from,
            record.valid_to,
            record.as_of_commit,
            record.version_range,
            record.taint,
            int(record.authorized_instruction),
            record.token_cost,
            record.utility,
            layers[0],
            layers[1],
            layers[2],
            layers[3],
            layers[4],
            record.body,
            stable_json(record.metadata),
            record.source_hash,
            int(record.schema_valid),
            utc_now(),
            # Same text the read gate would scan: body plus the stored metadata.
            int(bool(scan_content(record.body + str(json.loads(stable_json(record.metadata)))))),
        )
        self.connection.execute(
            """INSERT INTO notes(
                id,declared_id,path,schema_version,kind,title,type,scope,status,summary,confidence,authority,
                repo,repository_id,project,module,branch,created,updated,validated,freshness,valid_from,valid_to,
                as_of_commit,version_range,taint,authorized_instruction,token_cost,utility,l0,l1,l2,l3,l4,body,
                metadata_json,source_hash,schema_valid,indexed_at,unsafe
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            values,
        )
        self.connection.execute(
            "INSERT INTO notes_fts(note_id,title,summary,l1,l2,l3,path) VALUES(?,?,?,?,?,?,?)",
            (record.id, record.title, record.summary, layers[1], layers[2], layers[3], record.path),
        )
        for target in record.links:
            self.connection.execute(
                "INSERT OR IGNORE INTO links(source_id,target,target_id,relation,provenance) VALUES(?,?,?,?,?)",
                (record.id, target, "", "related-to", "wikilink"),
            )
        relations = record.metadata.get("relations", {})
        if isinstance(relations, dict):
            for relation, targets in relations.items():
                if isinstance(targets, str):
                    targets = [targets]
                if isinstance(targets, list):
                    for target in targets:
                        self.connection.execute(
                            "INSERT OR IGNORE INTO links"
                            "(source_id,target,target_id,relation,provenance) VALUES(?,?,?,?,?)",
                            (record.id, str(target), "", str(relation).replace("_", "-"), "frontmatter"),
                        )

    def _resolve_link_targets(self) -> None:
        notes = list(
            self.connection.execute("SELECT id,declared_id,path,title FROM notes ORDER BY (id != declared_id), path")
        )
        by_key: dict[str, str] = {}
        for row in notes:
            keys = {
                row["id"],
                row["declared_id"],
                row["path"],
                Path(row["path"]).with_suffix("").as_posix(),
                row["title"],
                Path(row["path"]).stem,
            }
            for key in keys:
                if key and key not in by_key:
                    by_key[str(key)] = row["id"]
        for row in self.connection.execute("SELECT source_id,target,relation FROM links").fetchall():
            target = str(row["target"])
            target_id = by_key.get(target, by_key.get(Path(target).stem, ""))
            self.connection.execute(
                "UPDATE links SET target_id=? WHERE source_id=? AND target=? AND relation=?",
                (target_id, row["source_id"], row["target"], row["relation"]),
            )

    def get(self, memory_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT n.*,COALESCE(u.uses,0) AS uses,u.last_used FROM notes n "
            "LEFT JOIN (SELECT note_id,COUNT(*) uses,MAX(retrieved_at) last_used FROM usage GROUP BY note_id) u "
            "ON n.id=u.note_id WHERE n.id=? OR n.declared_id=? ORDER BY (n.id != ?), n.path LIMIT 1",
            (memory_id, memory_id, memory_id),
        ).fetchone()
        return self._row(row) if row else None

    def get_by_path(self, path: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM notes WHERE path=?", (path,)).fetchone()
        return self._row(row) if row else None

    def all_notes(self, statuses: set[str] | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT n.*,COALESCE(u.uses,0) AS uses,u.last_used FROM notes n "
            "LEFT JOIN (SELECT note_id,COUNT(*) uses,MAX(retrieved_at) last_used FROM usage GROUP BY note_id) u "
            "ON n.id=u.note_id"
        )
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.connection.execute(
                f"{query} WHERE n.status IN ({placeholders}) ORDER BY n.path", tuple(sorted(statuses))
            ).fetchall()
        else:
            rows = self.connection.execute(f"{query} ORDER BY n.path").fetchall()
        return [self._row(row) for row in rows]

    def set_eligible(self, identities: list[str] | None) -> None:
        """Per-request candidate gate; applies before lexical/exact/graph limits."""
        self._eligible = identities is not None
        self.connection.execute("CREATE TEMP TABLE IF NOT EXISTS retrieval_eligible(id TEXT PRIMARY KEY)")
        self.connection.execute("DELETE FROM retrieval_eligible")
        if identities:
            self.connection.executemany(
                "INSERT OR IGNORE INTO retrieval_eligible(id) VALUES(?)", ((identity,) for identity in identities)
            )

    def _eligible_sql(self, column: str = "id") -> str:
        return f" AND {column} IN (SELECT id FROM retrieval_eligible)" if getattr(self, "_eligible", False) else ""

    def candidate_notes(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM notes WHERE 1=1" + self._eligible_sql()).fetchall()
        return [self._row(row) for row in rows]

    def search_exact(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return []
        rows = self.connection.execute(
            "SELECT * FROM notes WHERE (LOWER(id)=? OR LOWER(declared_id)=? OR LOWER(path)=? OR LOWER(title)=? "
            "OR LOWER(path) LIKE ?)" + self._eligible_sql() + " ORDER BY LENGTH(path),path LIMIT ?",
            (q, q, q, q, f"%{q}%", limit),
        ).fetchall()
        result = [self._row(row) for row in rows]
        for position, item in enumerate(result):
            item["exact"] = (
                1.0
                if q in {item["id"].lower(), item["declared_id"].lower(), item["path"].lower(), item["title"].lower()}
                else 0.75
            )
            item["rank_exact"] = position + 1
        return result

    def search_lexical(self, query: str, limit: int = 200) -> list[dict[str, Any]]:
        query_terms = terms(query)
        if not query_terms:
            return []
        if self.fts5:
            fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in query_terms[:32])
            try:
                matches = self.connection.execute(
                    "SELECT note_id,bm25(notes_fts,0.0,3.0,2.6,2.3,1.7,1.0,0.7) AS bm25_rank "
                    "FROM notes_fts WHERE notes_fts MATCH ?"
                    + self._eligible_sql("note_id")
                    + " ORDER BY bm25_rank LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                matches = []
            result: list[dict[str, Any]] = []
            if matches:
                ranks = [float(row["bm25_rank"]) for row in matches]
                worst, best = max(ranks), min(ranks)
                spread = max(worst - best, 1e-9)
                for position, match in enumerate(matches):
                    row = self.connection.execute("SELECT * FROM notes WHERE id=?", (match["note_id"],)).fetchone()
                    if row:
                        item = self._row(row)
                        raw = float(match["bm25_rank"])
                        # FTS5 BM25 is lower-is-better; preserve raw and normalized magnitude.
                        item["bm25"] = raw
                        item["lexical"] = 1.0 if len(matches) == 1 else max(0.0, min(1.0, (worst - raw) / spread))
                        item["rank_lexical"] = position + 1
                        result.append(item)
                return result
        clauses = " OR ".join("LOWER(title || ' ' || summary || ' ' || l2 || ' ' || path) LIKE ?" for _ in query_terms)
        params = tuple(f"%{term.lower()}%" for term in query_terms) + (limit,)
        rows = self.connection.execute(
            f"SELECT * FROM notes WHERE ({clauses})" + self._eligible_sql() + " LIMIT ?", params
        ).fetchall()
        result = [self._row(row) for row in rows]
        for item in result:
            haystack = f"{item['title']} {item['summary']} {item['l2']} {item['path']}".lower()
            hits = sum(1 for term in query_terms if term in haystack)
            item["lexical"] = hits / max(1, len(query_terms))
        result.sort(key=lambda item: (-item["lexical"], item["path"]))
        for position, item in enumerate(result):
            item["rank_lexical"] = position + 1
        return result

    def search(self, query: str, limit: int = 200) -> list[dict[str, Any]]:
        # Backward-compatible lexical API.
        return self.search_lexical(query, limit)

    def neighbors(self, memory_ids: list[str], limit: int = 30, reverse: bool = True) -> list[dict[str, Any]]:
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self.connection.execute(
            f"SELECT source_id,target,target_id,relation FROM links WHERE source_id IN ({placeholders})"
            + self._eligible_sql("target_id")
            + " LIMIT ?",
            (*memory_ids, limit),
        ).fetchall()
        if reverse:
            rows += self.connection.execute(
                f"SELECT source_id,target,target_id,relation FROM links WHERE target_id IN ({placeholders})"
                + self._eligible_sql("source_id")
                + " LIMIT ?",
                (*memory_ids, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        seen = set(memory_ids)
        for link in rows:
            target_id = link["target_id"] if link["source_id"] in memory_ids else link["source_id"]
            if not target_id or target_id in seen:
                continue
            row = self.connection.execute("SELECT * FROM notes WHERE id=? LIMIT 1", (target_id,)).fetchone()
            if row:
                item = self._row(row)
                item["graph_relation"] = link["relation"]
                item["graph_source"] = link["source_id"]
                item["lexical"] = 0.0
                result.append(item)
                seen.add(target_id)
                if len(result) >= limit:
                    break
        return result

    def record_decisions(self, retrieval_id: str, decisions: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.connection.executemany(
            "INSERT INTO decisions(retrieval_id,note_id,selected,score,reason,created_at) VALUES(?,?,?,?,?,?)",
            [
                (
                    retrieval_id,
                    item["id"],
                    int(item.get("selected", False)),
                    float(item.get("score", 0.0)),
                    "; ".join(item.get("reasons", [])),
                    now,
                )
                for item in decisions
            ],
        )
        self.connection.commit()

    def record_rank_example(self, retrieval_id: str, note_id: str, features: dict[str, float], selected: bool) -> None:
        self.record_rank_examples(retrieval_id, [(note_id, features, selected)])

    def record_rank_examples(
        self, retrieval_id: str, examples: Iterable[tuple[str, dict[str, float], bool]]
    ) -> None:
        now = utc_now()
        self.connection.executemany(
            "INSERT INTO rank_examples"
            "(retrieval_id,note_id,feature_json,selected,label,created_at) VALUES(?,?,?,?,NULL,?) "
            "ON CONFLICT(retrieval_id,note_id) DO UPDATE SET "
            "feature_json=excluded.feature_json, selected=excluded.selected",
            [
                (retrieval_id, note_id, stable_json(features), int(selected), now)
                for note_id, features, selected in examples
            ],
        )
        self.connection.commit()

    def evidence_valid(self, store: EvidenceStore, identities: Iterable[str]) -> dict[str, bool]:
        """Digest-verify evidence objects, re-reading only files whose stat identity changed.

        The key includes ctime, which user space cannot set, so rewriting an evidence file
        always forces re-verification.
        """
        wanted = sorted({str(identity) for identity in identities})
        if not wanted:
            return {}
        cached: dict[str, sqlite3.Row] = {}
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            for row in self.connection.execute(
                f"SELECT * FROM evidence_checks WHERE evidence_id IN ({placeholders})", chunk
            ):
                cached[row["evidence_id"]] = row
        result: dict[str, bool] = {}
        updates = []
        for identity in wanted:
            path = store.path_for(identity)
            try:
                stat = path.stat() if path else None
            except OSError:
                stat = None
            if stat is None:
                result[identity] = False
                continue
            key = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            row = cached.get(identity)
            if row and (row["inode"], row["file_size"], row["mtime_ns"], row["ctime_ns"]) == key:
                result[identity] = bool(row["valid"])
                continue
            result[identity] = store.verify(identity)
            updates.append((identity, *key, int(result[identity])))
        if updates:
            self.connection.executemany(
                "INSERT INTO evidence_checks(evidence_id,inode,file_size,mtime_ns,ctime_ns,valid) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(evidence_id) DO UPDATE SET inode=excluded.inode, file_size=excluded.file_size, "
                "mtime_ns=excluded.mtime_ns, ctime_ns=excluded.ctime_ns, valid=excluded.valid",
                updates,
            )
            self.connection.commit()
        return result

    def label_rank_example(self, retrieval_id: str, note_id: str, label: float) -> None:
        self.connection.execute(
            "UPDATE rank_examples SET label=? WHERE retrieval_id=? AND note_id=?", (float(label), retrieval_id, note_id)
        )
        self.connection.commit()

    def record_usage(self, retrieval_id: str, task_hash: str, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.connection.executemany(
            "INSERT INTO usage"
            "(note_id,retrieval_id,task_hash,retrieved_at,rank,score,tokens,layer) "
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
            "WHERE note_id=? ORDER BY id DESC LIMIT 10",
            (note["id"] if note else memory_id,),
        ).fetchall()
        return {"memory": note, "recent_decisions": [dict(row) for row in rows]}

    def stats(self) -> dict[str, Any]:
        total = self.connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        by_status = {
            row[0]: row[1] for row in self.connection.execute("SELECT status,COUNT(*) FROM notes GROUP BY status")
        }
        by_scope = {
            row[0]: row[1] for row in self.connection.execute("SELECT scope,COUNT(*) FROM notes GROUP BY scope")
        }
        use = self.connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(tokens),0),COUNT(DISTINCT retrieval_id) FROM usage"
        ).fetchone()
        return {
            "notes": total,
            "by_status": by_status,
            "by_scope": by_scope,
            "retrieved_items": use[0],
            "retrieved_tokens": use[1],
            "retrievals": use[2],
            "fts5": self.fts5,
            "schema_version": SCHEMA_VERSION,
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
