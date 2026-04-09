"""Agentic Daisy -- Persistent memory system (JSON file-backed).

Memory records support optional wing/room/hall namespacing for EDA workflows
(wing=chip/design, room=block, hall=flow-stage or memory-type). A drawer is
a verbatim archival unit: the content is written to a file under
``.daisy/memory/drawers/`` and the record in ``memories.json`` only holds the
pointer + metadata.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:
    import fcntl  # POSIX advisory file locking
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover -- non-POSIX fallback
    _HAS_FCNTL = False

LOG = logging.getLogger("daisy")


# Closed enum of EDA-specific hall values. `wing` and `room` remain free-form
# strings (they name chips and blocks), but `hall` is tightly scoped so Claude
# and programmatic callers can't sprawl the taxonomy.
#
# Backward compatibility: existing records on disk may carry legacy free-form
# halls from before this closure -- they load unchanged. Only NEW writes are
# validated.
HALL_VALUES: Tuple[str, ...] = (
    "timing", "power", "drc", "floorplan", "cts",
    "synth", "constraint", "workaround", "facts",
)

_NAMESPACE_FIELDS = ("wing", "room", "hall")
_RECORD_EXTRA_FIELDS = ("source_file", "valid_from", "valid_until",
                        "importance", "drawer_path", "added_by")


def _validate_hall(hall: str) -> None:
    """Raise ValueError if *hall* is non-empty and not in HALL_VALUES."""
    if hall and hall not in HALL_VALUES:
        raise ValueError(
            "invalid hall %r; expected one of %s"
            % (hall, ", ".join(HALL_VALUES))
        )


class MemoryStore:
    """Key-value memory store persisted as a single JSON file.

    Records have the shape::

        {
            "key": str,
            "value": str,
            "tags": [str, ...],
            "wing": str, "room": str, "hall": str,        # optional
            "source_file": str,                            # optional
            "valid_from": str, "valid_until": str,         # ISO dates
            "importance": int,                             # 0..100, default 0
            "drawer_path": str,                            # set for drawers
            "added_by": str,
            "created": str, "updated": str,
        }
    """

    def __init__(self, memory_dir: str) -> None:
        self.memory_dir = memory_dir
        os.makedirs(memory_dir, mode=0o700, exist_ok=True)
        self.memory_file = os.path.join(memory_dir, "memories.json")
        self.lock_file = os.path.join(memory_dir, ".memories.lock")
        self.drawers_dir = os.path.join(memory_dir, "drawers")
        os.makedirs(self.drawers_dir, mode=0o700, exist_ok=True)
        self._memories: List[Dict[str, Any]] = []
        with self._locked():
            self._load()

    # ------------------------------------------------------------------ IO
    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Best-effort exclusive lock on the memory directory.

        Uses fcntl.flock on a dedicated lockfile so concurrent writers
        (two daisy sessions sharing a ``.daisy/memory/``) serialize their
        load/save cycles. Silently degrades to no locking on platforms
        without fcntl (Windows) or filesystems that refuse flock (some NFS).
        """
        if not _HAS_FCNTL:
            yield
            return
        try:
            lf = open(self.lock_file, "a+")
        except OSError as exc:
            LOG.debug("Lockfile open failed: %s -- proceeding unlocked", exc)
            yield
            return
        try:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                LOG.debug("flock failed: %s -- proceeding unlocked", exc)
            yield
        finally:
            try:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            lf.close()

    def _load(self) -> None:
        if not os.path.exists(self.memory_file):
            self._memories = []
            return
        try:
            with open(self.memory_file, "r") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            LOG.warning(
                "Corrupted memory file %s: %s -- starting with empty memories",
                self.memory_file, exc,
            )
            self._memories = []
            return
        # Shape check: require top-level list of dicts. Anything else is
        # treated as corrupt and quarantined (not silently destroyed).
        if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
            LOG.warning(
                "Memory file %s has unexpected shape (not a list of dicts) "
                "-- starting empty; original preserved as %s.bad",
                self.memory_file, self.memory_file,
            )
            try:
                os.replace(self.memory_file, self.memory_file + ".bad")
            except OSError:
                pass
            self._memories = []
            return
        # Every record must have a string "key"; drop any that don't.
        self._memories = [r for r in raw if isinstance(r.get("key"), str)]

    def _save(self) -> None:
        # Atomic write: write to temp file, then rename (safe on POSIX)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.memory_dir, suffix=".tmp", prefix=".memories-",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._memories, f, indent=2, default=str)
            os.replace(tmp_path, self.memory_file)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _as_of_match(mem: Dict[str, Any], as_of: str) -> bool:
        """Return True if *mem* was valid at *as_of* (ISO date string).

        A record with no temporal metadata is always considered valid.
        """
        vf = mem.get("valid_from") or ""
        vu = mem.get("valid_until") or ""
        if vf and as_of < vf:
            return False
        if vu and as_of > vu:
            return False
        return True

    @staticmethod
    def _namespace_match(mem: Dict[str, Any], wing: str, room: str,
                         hall: str) -> bool:
        """Return True if *mem* matches the provided namespace filters.

        An empty filter string means 'don't filter on this field'.
        """
        if wing and mem.get("wing", "") != wing:
            return False
        if room and mem.get("room", "") != room:
            return False
        if hall and mem.get("hall", "") != hall:
            return False
        return True

    # ---------------------------------------------------------- save / CRUD
    def save_memory(
        self,
        key: str,
        value: str,
        tags: Optional[List[str]] = None,
        wing: str = "",
        room: str = "",
        hall: str = "",
        source_file: str = "",
        valid_from: str = "",
        valid_until: str = "",
        importance: Optional[int] = None,
    ) -> str:
        """Save or update a memory. Returns confirmation JSON string."""
        if tags is None:
            tags = []

        try:
            _validate_hall(hall)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        with self._locked():
            # Reload under lock so concurrent writers don't clobber each other.
            self._load()
            return self._save_memory_locked(
                key, value, tags, wing, room, hall, source_file,
                valid_from, valid_until, importance,
            )

    def _save_memory_locked(
        self,
        key: str,
        value: str,
        tags: List[str],
        wing: str,
        room: str,
        hall: str,
        source_file: str,
        valid_from: str,
        valid_until: str,
        importance: Optional[int],
    ) -> str:
        # Check if key already exists (update in place).
        # Convention: on update, a non-empty string overrides the prior value
        # and an empty string preserves it. This matches how tool JSON schemas
        # work -- Claude can omit a field entirely to mean "don't change".
        # There is intentionally no way to clear a namespace field back to ""
        # via an update; delete and re-create the record if you need that.
        for i, mem in enumerate(self._memories):
            if mem["key"] == key:
                # Snapshot the original record so we can roll back on _save failure.
                original = dict(mem)
                mem["value"] = value
                mem["tags"] = tags
                if wing:
                    mem["wing"] = wing
                if room:
                    mem["room"] = room
                if hall:
                    mem["hall"] = hall
                if source_file:
                    mem["source_file"] = source_file
                if valid_from:
                    mem["valid_from"] = valid_from
                if valid_until:
                    mem["valid_until"] = valid_until
                if importance is not None:
                    mem["importance"] = importance
                mem["updated"] = self._now_iso()
                try:
                    self._save()
                except BaseException:
                    self._memories[i] = original
                    raise
                return json.dumps({"status": "updated", "key": key})

        # New entry
        record: Dict[str, Any] = {
            "key": key,
            "value": value,
            "tags": tags,
            "created": self._now_iso(),
            "updated": self._now_iso(),
        }
        if wing:
            record["wing"] = wing
        if room:
            record["room"] = room
        if hall:
            record["hall"] = hall
        if source_file:
            record["source_file"] = source_file
        if valid_from:
            record["valid_from"] = valid_from
        if valid_until:
            record["valid_until"] = valid_until
        if importance is not None:
            record["importance"] = importance

        self._memories.append(record)
        try:
            self._save()
        except BaseException:
            self._memories.pop()
            raise
        return json.dumps({"status": "created", "key": key})

    def search_memory(
        self,
        query: str = "",
        tag: str = "",
        wing: str = "",
        room: str = "",
        hall: str = "",
        as_of: str = "",
    ) -> str:
        """Search memories by keyword/tag/namespace/validity.

        An empty filter on any parameter means 'don't filter on this field'.
        Returns JSON ``{"matches": N, "results": [...]}``.
        """
        results = []
        for mem in self._memories:
            if tag and tag not in mem.get("tags", []):
                continue
            if not self._namespace_match(mem, wing, room, hall):
                continue
            if as_of and not self._as_of_match(mem, as_of):
                continue
            if query:
                q = query.lower()
                if q not in mem["key"].lower() and q not in mem["value"].lower():
                    continue
            results.append(mem)

        if not results:
            return json.dumps({"matches": 0, "results": []})
        return json.dumps({"matches": len(results), "results": results}, default=str)

    def delete_memory(self, key: str) -> str:
        """Delete a memory by key. If the record points at a drawer file,
        the file is removed as well. If the index save fails the in-memory
        record is restored and the drawer file is left alone.
        """
        with self._locked():
            self._load()
            for i, mem in enumerate(self._memories):
                if mem["key"] == key:
                    drawer_path = mem.get("drawer_path", "")
                    removed = self._memories.pop(i)
                    try:
                        self._save()
                    except BaseException:
                        self._memories.insert(i, removed)
                        raise
                    if drawer_path and os.path.exists(drawer_path):
                        try:
                            os.unlink(drawer_path)
                        except OSError as exc:
                            LOG.warning("Could not delete drawer file %s: %s",
                                        drawer_path, exc)
                    return json.dumps({"status": "deleted", "key": key})
        return json.dumps({"status": "not_found", "key": key})

    def list_memories(
        self,
        wing: str = "",
        room: str = "",
        hall: str = "",
    ) -> str:
        """List memory summaries, optionally filtered by namespace."""
        summary = []
        for mem in self._memories:
            if not self._namespace_match(mem, wing, room, hall):
                continue
            summary.append({
                "key": mem["key"],
                "tags": mem.get("tags", []),
                "wing": mem.get("wing", ""),
                "room": mem.get("room", ""),
                "hall": mem.get("hall", ""),
                "updated": mem.get("updated", mem.get("created", "")),
                "is_drawer": bool(mem.get("drawer_path", "")),
            })
        return json.dumps({"total": len(summary), "memories": summary})

    # ----------------------------------------------------------- taxonomies
    def list_wings(self) -> str:
        """Return ``{wing_name: count}`` of memories per wing."""
        counts: Dict[str, int] = {}
        for mem in self._memories:
            wing = mem.get("wing", "")
            if not wing:
                continue
            counts[wing] = counts.get(wing, 0) + 1
        return json.dumps({"wings": counts, "total": sum(counts.values())})

    def list_rooms(self, wing: str = "") -> str:
        """Return ``{room_name: count}``, optionally filtered by wing."""
        counts: Dict[str, int] = {}
        for mem in self._memories:
            if wing and mem.get("wing", "") != wing:
                continue
            room = mem.get("room", "")
            if not room:
                continue
            counts[room] = counts.get(room, 0) + 1
        return json.dumps({"wing": wing or None, "rooms": counts,
                           "total": sum(counts.values())})

    def get_taxonomy(self) -> str:
        """Return the full ``{wing: {room: {hall: count}}}`` tree."""
        tree: Dict[str, Dict[str, Dict[str, int]]] = {}
        for mem in self._memories:
            wing = mem.get("wing", "")
            room = mem.get("room", "")
            hall = mem.get("hall", "")
            if not wing:
                continue
            w = tree.setdefault(wing, {})
            r = w.setdefault(room or "_", {})
            r[hall or "_"] = r.get(hall or "_", 0) + 1
        return json.dumps({"taxonomy": tree})

    # --------------------------------------------------------------- drawers
    def add_drawer(
        self,
        wing: str,
        room: str,
        content: str = "",
        spill_file: str = "",
        hall: str = "",
        source_file: str = "",
        added_by: str = "agent",
        importance: Optional[int] = None,
    ) -> str:
        """Archive verbatim content under ``drawers/``.

        Either *content* is provided inline, or *spill_file* points at an
        existing file (typically an agent-loop spill) whose contents become
        the drawer. Returns ``{"status", "drawer_id", "path"}``. Dedup is
        content-hash based; a duplicate returns status ``"duplicate"``.
        """
        if not wing or not room:
            return json.dumps({
                "status": "error",
                "error": "wing and room are required",
            })

        try:
            _validate_hall(hall)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)})

        # Resolve source content
        body: str
        if spill_file:
            if not os.path.exists(spill_file):
                return json.dumps({
                    "status": "error",
                    "error": "spill_file not found: %s" % spill_file,
                })
            try:
                with open(spill_file, "r", errors="replace") as f:
                    body = f.read()
            except OSError as exc:
                return json.dumps({
                    "status": "error",
                    "error": "could not read spill_file: %s" % exc,
                })
            if not source_file:
                source_file = spill_file
        else:
            if not content:
                return json.dumps({
                    "status": "error",
                    "error": "content or spill_file is required",
                })
            body = content

        content_hash = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()

        with self._locked():
            # Reload under lock so two sessions can't both miss a duplicate.
            self._load()

            # Duplicate detection by content hash
            for mem in self._memories:
                if mem.get("content_sha256") == content_hash:
                    return json.dumps({
                        "status": "duplicate",
                        "drawer_id": mem["key"],
                        "path": mem.get("drawer_path", ""),
                    })

            # Build drawer id and path
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_wing = wing.replace("/", "_")
            safe_room = room.replace("/", "_")
            drawer_id = "drawer_%s_%s_%s_%s" % (safe_wing, safe_room, ts,
                                                content_hash[:8])
            drawer_path = os.path.join(self.drawers_dir, drawer_id + ".txt")

            # Atomic write of the drawer file
            fd, tmp_path = tempfile.mkstemp(
                dir=self.drawers_dir, suffix=".tmp", prefix=".drawer-",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(body)
                os.replace(tmp_path, drawer_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # Index pointer in memories.json
            record: Dict[str, Any] = {
                "key": drawer_id,
                "value": "[drawer] %d bytes at %s" % (len(body), drawer_path),
                "tags": ["drawer"],
                "wing": wing,
                "room": room,
                "drawer_path": drawer_path,
                "content_sha256": content_hash,
                "added_by": added_by,
                "created": self._now_iso(),
                "updated": self._now_iso(),
            }
            if hall:
                record["hall"] = hall
            if source_file:
                record["source_file"] = source_file
            if importance is not None:
                record["importance"] = importance

            # Index the drawer. If the index save fails, clean up the drawer
            # file so we don't leave an orphan on disk that no record points to.
            self._memories.append(record)
            try:
                self._save()
            except BaseException:
                self._memories.pop()
                try:
                    os.unlink(drawer_path)
                except OSError:
                    pass
                raise

        return json.dumps({
            "status": "created",
            "drawer_id": drawer_id,
            "path": drawer_path,
            "bytes": len(body),
        })

    def get_drawer(self, drawer_id: str) -> str:
        """Return the full content of a drawer, or an error JSON."""
        for mem in self._memories:
            if mem["key"] == drawer_id and mem.get("drawer_path"):
                path = mem["drawer_path"]
                if not os.path.exists(path):
                    return json.dumps({
                        "status": "error",
                        "error": "drawer file missing: %s" % path,
                    })
                try:
                    with open(path, "r", errors="replace") as f:
                        body = f.read()
                except OSError as exc:
                    return json.dumps({
                        "status": "error",
                        "error": "could not read drawer: %s" % exc,
                    })
                return json.dumps({
                    "status": "ok",
                    "drawer_id": drawer_id,
                    "wing": mem.get("wing", ""),
                    "room": mem.get("room", ""),
                    "hall": mem.get("hall", ""),
                    "content": body,
                })
        return json.dumps({"status": "not_found", "drawer_id": drawer_id})
