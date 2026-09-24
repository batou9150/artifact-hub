# Firestore configuration

* `firestore.rules`: deny-all for client SDKs (the backend is the only reader and writer).
* `firestore.indexes.json`: every query the store runs is a single-field equality or
  `array-contains` (`owner ==`, `visibility ==`, `shared_with array-contains`), served by
  Firestore's automatic single-field indexes, so no composite index is needed. The
  overrides disable indexing of version bodies (large strings, never queried) and of the
  search vector (hundreds of floats that would each become an index entry).
