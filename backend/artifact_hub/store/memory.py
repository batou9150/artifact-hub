"""In-memory backend: for unit tests and quick local runs without the emulator.
Thread-safe (one lock); state is lost on restart and not shared across processes."""
from __future__ import annotations

import copy
import threading

from .base import ArtifactNotFound, ArtifactStore


class MemoryArtifactStore(ArtifactStore):
    def __init__(self, embedder=None):
        super().__init__(embedder)
        self._lock = threading.RLock()
        self._arts: dict[str, dict] = {}
        self._versions: dict[str, dict[int, dict]] = {}
        self._users: dict[str, dict] = {}

    def _get(self, doc_id):
        with self._lock:
            art = self._arts.get(doc_id)
            return copy.deepcopy(art) if art is not None else None

    def _create(self, doc_id, meta, version):
        with self._lock:
            self._arts[doc_id] = copy.deepcopy(meta)
            self._versions[doc_id] = {1: copy.deepcopy(version)}

    def _append_version(self, doc_id, body, size, author, ts):
        with self._lock:
            art = self._arts.get(doc_id)
            if art is None:
                raise ArtifactNotFound(doc_id)
            n = int(art["current_version"]) + 1
            self._versions[doc_id][n] = {"n": n, "body": body, "size": size, "author": author, "created_at": ts}
            art.update({"current_version": n, "size": size, "updated_at": ts})

    def _patch(self, doc_id, fields):
        with self._lock:
            art = self._arts.get(doc_id)
            if art is None:
                raise ArtifactNotFound(doc_id)
            art.update(copy.deepcopy(fields))

    def _add_viewer(self, doc_id, viewer, ts):
        with self._lock:
            art = self._arts.get(doc_id)
            if art is None:
                raise ArtifactNotFound(doc_id)
            if viewer not in art["viewers"]:
                art["viewers"].append(viewer)
            art["view_count"] = int(art.get("view_count") or 0) + 1
            art["last_viewed_at"] = ts

    def _delete(self, doc_id):
        with self._lock:
            self._arts.pop(doc_id, None)
            self._versions.pop(doc_id, None)

    def _get_version(self, doc_id, n):
        with self._lock:
            v = self._versions.get(doc_id, {}).get(n)
            return copy.deepcopy(v) if v else None

    def _list_versions(self, doc_id):
        with self._lock:
            return [copy.deepcopy(v) for v in self._versions.get(doc_id, {}).values()]

    def _query(self, field, op, value):
        with self._lock:
            out = []
            for doc_id, art in self._arts.items():
                v = art.get(field)
                if (op == "==" and v == value) or (op == "array_contains" and value in (v or [])):
                    out.append({"id": doc_id, **copy.deepcopy(art)})
            return out

    def _iter_all(self):
        with self._lock:
            return [{"id": k, **copy.deepcopy(v)} for k, v in self._arts.items()]

    def _upsert_user(self, email, name, ts):
        with self._lock:
            prev = self._users.get(email, {})
            self._users[email] = {"email": email, "name": name or prev.get("name", ""), "last_seen_at": ts}

    def _list_users(self):
        with self._lock:
            return list(copy.deepcopy(self._users).values())
