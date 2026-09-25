"""Short-lived authorization-server state, shared by every instance of the service.

Kinds:
  tx      pending authorization request (browser at the IdP or on the consent page)
  code    authorization code, single use
  rt      refresh token, single use (rotated on every refresh)
  family  one sign-in of one user into one client; every code and refresh token
          points at it, so deleting it revokes the whole chain

Keys (codes, refresh tokens) are stored as SHA-256 hashes, never in clear.
`use()` marks an entry used atomically and tells the caller whether this was the
first use: a second use of a code or refresh token is a replay, and the caller
revokes the family (OAuth 2.1 section 4.3.1 and 6.1).

Firestore deletes expired documents through a TTL policy on `expires_at`; that
cleanup lags, so every read checks the expiry itself.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone

COLLECTION = "oauth_grants"


def _doc_id(kind: str, key: str) -> str:
    return f"{kind}:{hashlib.sha256(key.encode()).hexdigest()}"


class GrantStore:
    def put(self, kind: str, key: str, data: dict, ttl: int) -> None: ...
    def get(self, kind: str, key: str) -> dict | None: ...
    def use(self, kind: str, key: str) -> tuple[dict, bool] | None: ...
    def delete(self, kind: str, key: str) -> None: ...


class MemoryGrantStore(GrantStore):
    def __init__(self):
        self._items: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def put(self, kind, key, data, ttl):
        with self._lock:
            now = time.time()
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            self._items[_doc_id(kind, key)] = (now + ttl, {**data, "used": False})

    def get(self, kind, key):
        with self._lock:
            item = self._items.get(_doc_id(kind, key))
            return dict(item[1]) if item and item[0] > time.time() else None

    def use(self, kind, key):
        with self._lock:
            item = self._items.get(_doc_id(kind, key))
            if not item or item[0] <= time.time():
                return None
            first = not item[1]["used"]
            item[1]["used"] = True
            return dict(item[1]), first

    def delete(self, kind, key):
        with self._lock:
            self._items.pop(_doc_id(kind, key), None)


class FirestoreGrantStore(GrantStore):
    def __init__(self, client, prefix: str = ""):
        self.db = client
        self._name = prefix + COLLECTION

    def _ref(self, kind, key):
        return self.db.collection(self._name).document(_doc_id(kind, key))

    @staticmethod
    def _live(snap) -> dict | None:
        if not snap.exists:
            return None
        doc = snap.to_dict()
        if doc["expires_at"] <= datetime.now(timezone.utc):
            return None
        return doc

    def put(self, kind, key, data, ttl):
        expires = datetime.fromtimestamp(time.time() + ttl, tz=timezone.utc)
        self._ref(kind, key).set({"data": data, "used": False, "expires_at": expires})

    def get(self, kind, key):
        doc = self._live(self._ref(kind, key).get())
        return {**doc["data"], "used": doc["used"]} if doc else None

    def use(self, kind, key):
        from google.cloud import firestore

        ref = self._ref(kind, key)

        @firestore.transactional
        def txn(transaction):
            doc = self._live(ref.get(transaction=transaction))
            if doc is None:
                return None
            if not doc["used"]:
                transaction.update(ref, {"used": True})
            return {**doc["data"], "used": True}, not doc["used"]

        return txn(self.db.transaction(max_attempts=5))

    def delete(self, kind, key):
        self._ref(kind, key).delete()


def build_grant_store(settings) -> GrantStore:
    if settings.store_backend == "firestore":
        from google.cloud import firestore
        return FirestoreGrantStore(firestore.Client(project=settings.gcp_project,
                                                    database=settings.firestore_database))
    return MemoryGrantStore()
