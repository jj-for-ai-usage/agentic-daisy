"""Agentic Daisy -- Temporal knowledge graph (SQLite-backed, stdlib only).

Lifted and adapted from ``milla-jovovich/mempalace``'s ``knowledge_graph.py``
(MIT licensed). Renamed ``obj``/``object`` -> ``object_`` in the Python API
to avoid shadowing the builtin; the SQL column name stays ``object``.

Stores typed triples ``subject -> predicate -> object`` with temporal
validity windows (``valid_from`` / ``valid_to``). ``valid_to IS NULL`` means
"currently true". Invalidation never deletes -- it just closes the window,
so the timeline stays intact.

All methods return JSON strings so they can be handed directly to the
agent loop as tool results.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("daisy")


class KGStore:
    """SQLite-backed temporal knowledge graph."""

    def __init__(self, kg_dir: str) -> None:
        self.kg_dir = kg_dir
        os.makedirs(kg_dir, mode=0o700, exist_ok=True)
        self.db_path = os.path.join(kg_dir, "triples.sqlite3")
        self._init_db()

    # ------------------------------------------------------------------ IO
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT DEFAULT 'unknown',
                    properties TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS triples (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    confidence REAL DEFAULT 1.0,
                    source TEXT,
                    source_file TEXT,
                    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (subject) REFERENCES entities(id),
                    FOREIGN KEY (object) REFERENCES entities(id)
                );

                CREATE INDEX IF NOT EXISTS idx_triples_subject
                    ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_object
                    ON triples(object);
                CREATE INDEX IF NOT EXISTS idx_triples_predicate
                    ON triples(predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_valid
                    ON triples(valid_from, valid_to);
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _entity_id(name: str) -> str:
        return name.lower().replace(" ", "_").replace("'", "")

    @staticmethod
    def _normalize_predicate(predicate: str) -> str:
        return predicate.lower().replace(" ", "_")

    # ---------------------------------------------------------- write ops
    def add_entity(
        self,
        name: str,
        entity_type: str = "unknown",
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Add or update an entity node. Returns JSON confirmation."""
        eid = self._entity_id(name)
        props = json.dumps(properties or {})
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO entities (id, name, type, properties) "
                "VALUES (?, ?, ?, ?)",
                (eid, name, entity_type, props),
            )
            conn.commit()
        finally:
            conn.close()
        return json.dumps({"status": "ok", "entity_id": eid, "name": name})

    def add_triple(
        self,
        subject: str,
        predicate: str,
        object_: str,
        valid_from: str = "",
        valid_to: str = "",
        confidence: float = 1.0,
        source: str = "",
        source_file: str = "",
    ) -> str:
        """Add a ``subject -> predicate -> object`` triple.

        Idempotent: an identical currently-valid triple (matching subject,
        predicate, object, and ``valid_to IS NULL``) returns the existing id
        without creating a duplicate.
        """
        if not subject or not predicate or not object_:
            return json.dumps({
                "status": "error",
                "error": "subject, predicate and object are all required",
            })

        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(object_)
        pred = self._normalize_predicate(predicate)

        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                (sub_id, subject),
            )
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                (obj_id, object_),
            )

            existing = conn.execute(
                "SELECT id FROM triples WHERE subject=? AND predicate=? "
                "AND object=? AND valid_to IS NULL",
                (sub_id, pred, obj_id),
            ).fetchone()
            if existing:
                return json.dumps({
                    "status": "exists",
                    "triple_id": existing[0],
                })

            now = datetime.now(timezone.utc).isoformat()
            digest = hashlib.md5(
                ("%s%s" % (valid_from, now)).encode()
            ).hexdigest()[:8]
            triple_id = "t_%s_%s_%s_%s" % (sub_id, pred, obj_id, digest)

            conn.execute(
                """INSERT INTO triples (id, subject, predicate, object,
                       valid_from, valid_to, confidence, source, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    triple_id,
                    sub_id,
                    pred,
                    obj_id,
                    valid_from or None,
                    valid_to or None,
                    confidence,
                    source or None,
                    source_file or None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return json.dumps({"status": "created", "triple_id": triple_id})

    def invalidate(
        self,
        subject: str,
        predicate: str,
        object_: str,
        ended: str = "",
    ) -> str:
        """Close the validity window of a currently-valid triple."""
        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(object_)
        pred = self._normalize_predicate(predicate)
        ended = ended or date.today().isoformat()

        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE triples SET valid_to=? WHERE subject=? AND "
                "predicate=? AND object=? AND valid_to IS NULL",
                (ended, sub_id, pred, obj_id),
            )
            conn.commit()
            count = cur.rowcount
        finally:
            conn.close()
        return json.dumps({
            "status": "ok",
            "invalidated": count,
            "ended": ended,
        })

    # ---------------------------------------------------------- query ops
    def query_entity(
        self,
        name: str,
        as_of: str = "",
        direction: str = "outgoing",
    ) -> str:
        """Return triples touching ``name``.

        ``direction`` is one of ``outgoing``/``incoming``/``both``.
        ``as_of`` (ISO date) filters to facts valid at that point in time.
        """
        if direction not in ("outgoing", "incoming", "both"):
            return json.dumps({
                "status": "error",
                "error": "direction must be outgoing|incoming|both",
            })

        eid = self._entity_id(name)
        conn = self._conn()
        results: List[Dict[str, Any]] = []
        try:
            if direction in ("outgoing", "both"):
                q = ("SELECT t.predicate, e.name, t.valid_from, t.valid_to, "
                     "t.confidence, t.source FROM triples t "
                     "JOIN entities e ON t.object = e.id "
                     "WHERE t.subject = ?")
                params: List[Any] = [eid]
                if as_of:
                    q += (" AND (t.valid_from IS NULL OR t.valid_from <= ?) "
                          "AND (t.valid_to IS NULL OR t.valid_to >= ?)")
                    params.extend([as_of, as_of])
                for row in conn.execute(q, params).fetchall():
                    results.append({
                        "direction": "outgoing",
                        "subject": name,
                        "predicate": row[0],
                        "object": row[1],
                        "valid_from": row[2],
                        "valid_to": row[3],
                        "confidence": row[4],
                        "source": row[5],
                        "current": row[3] is None,
                    })

            if direction in ("incoming", "both"):
                q = ("SELECT t.predicate, e.name, t.valid_from, t.valid_to, "
                     "t.confidence, t.source FROM triples t "
                     "JOIN entities e ON t.subject = e.id "
                     "WHERE t.object = ?")
                params = [eid]
                if as_of:
                    q += (" AND (t.valid_from IS NULL OR t.valid_from <= ?) "
                          "AND (t.valid_to IS NULL OR t.valid_to >= ?)")
                    params.extend([as_of, as_of])
                for row in conn.execute(q, params).fetchall():
                    results.append({
                        "direction": "incoming",
                        "subject": row[1],
                        "predicate": row[0],
                        "object": name,
                        "valid_from": row[2],
                        "valid_to": row[3],
                        "confidence": row[4],
                        "source": row[5],
                        "current": row[3] is None,
                    })
        finally:
            conn.close()
        return json.dumps({
            "entity": name,
            "as_of": as_of or None,
            "direction": direction,
            "matches": len(results),
            "results": results,
        })

    def timeline(self, entity: str = "") -> str:
        """Return up to 100 facts in chronological order.

        If *entity* is given, only facts touching that entity are returned.
        """
        conn = self._conn()
        try:
            if entity:
                eid = self._entity_id(entity)
                rows = conn.execute(
                    """SELECT t.predicate, s.name, o.name, t.valid_from, t.valid_to
                         FROM triples t
                         JOIN entities s ON t.subject = s.id
                         JOIN entities o ON t.object = o.id
                        WHERE t.subject = ? OR t.object = ?
                        ORDER BY COALESCE(t.valid_from, t.extracted_at) ASC
                        LIMIT 100""",
                    (eid, eid),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT t.predicate, s.name, o.name, t.valid_from, t.valid_to
                         FROM triples t
                         JOIN entities s ON t.subject = s.id
                         JOIN entities o ON t.object = o.id
                        ORDER BY COALESCE(t.valid_from, t.extracted_at) ASC
                        LIMIT 100"""
                ).fetchall()
        finally:
            conn.close()

        results = [
            {
                "subject": r[1],
                "predicate": r[0],
                "object": r[2],
                "valid_from": r[3],
                "valid_to": r[4],
                "current": r[4] is None,
            }
            for r in rows
        ]
        return json.dumps({
            "entity": entity or None,
            "count": len(results),
            "results": results,
        })

    def stats(self) -> str:
        """Return entity/triple counts and the set of predicate types."""
        conn = self._conn()
        try:
            entities = conn.execute(
                "SELECT COUNT(*) FROM entities"
            ).fetchone()[0]
            triples = conn.execute(
                "SELECT COUNT(*) FROM triples"
            ).fetchone()[0]
            current = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE valid_to IS NULL"
            ).fetchone()[0]
            predicates = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT predicate FROM triples ORDER BY predicate"
                ).fetchall()
            ]
        finally:
            conn.close()
        return json.dumps({
            "entities": entities,
            "triples": triples,
            "current_facts": current,
            "expired_facts": triples - current,
            "relationship_types": predicates,
        })
