"""Firestore backend (production, and local development through the emulator:
set FIRESTORE_EMULATOR_HOST). Uses the server client library with the service's
own identity; browsers never talk to Firestore directly (see firestore.rules)."""
from __future__ import annotations

from google.cloud import firestore

from .base import ArtifactConflict, ArtifactNotFound, ArtifactStore

ARTIFACTS = "artifacts"
VERSIONS = "versions"
USERS = "artifact_users"


class FirestoreArtifactStore(ArtifactStore):
    def __init__(self, client: firestore.Client, embedder=None, prefix: str = ""):
        super().__init__(embedder)
        self.db = client
        self._arts_name = prefix + ARTIFACTS
        self._users_name = prefix + USERS

    @classmethod
    def from_settings(cls, settings, embedder=None):
        client = firestore.Client(project=settings.gcp_project, database=settings.firestore_database)
        return cls(client, embedder)

    def _col(self):
        return self.db.collection(self._arts_name)

    def _ref(self, doc_id):
        return self._col().document(doc_id)

    def _get(self, doc_id):
        snap = self._ref(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def _create(self, doc_id, meta, version):
        ref = self._ref(doc_id)
        batch = self.db.batch()  # a reader never sees current_version pointing at nothing
        batch.create(ref, meta)
        batch.set(ref.collection(VERSIONS).document("1"), version)
        batch.commit()

    def _append_version(self, doc_id, body, size, author, ts):
        ref = self._ref(doc_id)

        @firestore.transactional
        def txn(transaction):
            # Read-then-write in one transaction: two concurrent updates must not both
            # write version N+1 and silently drop a retained version.
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                raise ArtifactNotFound(doc_id)
            n = int(snap.get("current_version")) + 1
            transaction.set(ref.collection(VERSIONS).document(str(n)),
                            {"n": n, "body": body, "size": size, "author": author, "created_at": ts})
            transaction.update(ref, {"current_version": n, "size": size, "updated_at": ts})

        try:
            txn(self.db.transaction(max_attempts=10))
        except ArtifactNotFound:
            raise
        except ValueError as exc:  # "Failed to commit transaction in N attempts"
            raise ArtifactConflict("the artifact is being updated concurrently; retry") from exc

    def _patch(self, doc_id, fields):
        ref = self._ref(doc_id)
        if not ref.get().exists:
            raise ArtifactNotFound(doc_id)
        ref.update(fields)

    def _add_viewer(self, doc_id, viewer, ts):
        self._ref(doc_id).update({
            "viewers": firestore.ArrayUnion([viewer]),
            "view_count": firestore.Increment(1),
            "last_viewed_at": ts,
        })

    def _delete(self, doc_id):
        ref = self._ref(doc_id)
        for v in ref.collection(VERSIONS).stream():  # no orphaned subcollection
            v.reference.delete()
        ref.delete()

    def _get_version(self, doc_id, n):
        snap = self._ref(doc_id).collection(VERSIONS).document(str(n)).get()
        return snap.to_dict() if snap.exists else None

    def _list_versions(self, doc_id):
        return [d.to_dict() for d in self._ref(doc_id).collection(VERSIONS).stream()]

    def _query(self, field, op, value):
        fs_op = {"==": "==", "array_contains": "array_contains"}[op]
        q = self._col().where(filter=firestore.FieldFilter(field, fs_op, value))
        return [{"id": d.id, **d.to_dict()} for d in q.stream()]

    def _iter_all(self):
        return [{"id": d.id, **d.to_dict()} for d in self._col().stream()]

    def _upsert_user(self, email, name, ts):
        data = {"email": email, "last_seen_at": ts}
        if name:
            data["name"] = name
        self.db.collection(self._users_name).document(email).set(data, merge=True)

    def _list_users(self):
        # Scan: fine for an organisation directory of a few thousand people. Past
        # that, swap for a prefix index (email_lower range query) or the IdP's
        # directory API (Microsoft Graph / Okta Users API).
        return [d.to_dict() for d in self.db.collection(self._users_name).limit(5000).stream()]
