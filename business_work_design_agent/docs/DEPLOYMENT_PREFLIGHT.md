# Deployment preflight

Run this once before a shared F10 deployment, and again when the MongoDB
cluster or catalog embedding model changes. It checks the prerequisites that
cannot safely be inferred from a Canvas connection alone.

```powershell
$env:MONGODB_URI = '<your MongoDB connection string>'
python scripts/bootstrap_mongodb_prerequisites.py --tenant-id default
```

The first command is read-only. It requires F10 transaction support (MongoDB
Atlas, a replica set, or `mongos`) and reports all normal MongoDB indexes that
are missing. Create only the listed normal indexes after reviewing the result:

```powershell
python scripts/bootstrap_mongodb_prerequisites.py --apply
```

It creates these indexes and nothing else:

- unique `work_definitions(tenant_id, work_definition_id)`
- unique `clarification_batches(tenant_id, batch_id)` and TTL `expires_at`
- audit lookup indexes for work and runtime events
- unique `work_runtime_states(tenant_id, work_definition_id, session_id)`
- `catalog_assets` exact-title and exact-alias indexes scoped by tenant and snapshot
- `catalog_asset_chunks(tenant_id, snapshot_id, asset_type)` for the portable keyword fallback

The script intentionally does not create Atlas Search indexes. Create
`catalog_lexical` and `catalog_vector` on `catalog_asset_chunks` in Atlas
after F00 has completed a live ingest, using the active pointer's reported
embedding dimension for the vector index. They enable the full Atlas
keyword-plus-vector lane. F20/F90 still keep a bounded, ACL-scoped portable
keyword lane when an Atlas operator or index is unavailable; that fallback
does not claim semantic/vector evidence in its retrieval trace.

For `catalog_lexical`, include `title`, `aliases_normalized`, `description`,
`lexical_text_redacted`, and `category` as searchable text paths. For
`catalog_vector`, set `embedding.vector` as the vector path and register these
pre-filter paths: `tenant_id`, `snapshot_id`, `asset_type`,
`acl.visibility`, `acl.groups`, and `acl.subjects`. The vector dimension must
match the active pointer's `embedding_contract.dimension` exactly.

When F20 returns no exact, lexical, or vector candidates, read
`retrieval_trace.scope_diagnostics` before interpreting it as “no reusable
asset”. `authorized_chunk_exists=true` together with
`interpretation=SEARCH_INCONCLUSIVE_OR_INDEX_FILTERED` means the active,
authorized catalog has data but an Atlas index or pre-filter should be checked.
F20 then uses only a same-tenant/snapshot/ACL metadata lexical fallback; its
trace marks `semantic_match_verified=false`. If the fallback is also empty,
there was no authorized metadata text match and unrelated popular assets are
not recommended.

Before live publishing, also confirm all of the following:

1. Langflow Desktop/server is exactly `1.11.1` / `langflow-base 0.11.5` /
   `lfx 1.11.5`.
2. Langflow has one Secret Global Variable named `MONGO_URL`; every Flow uses
   that value, not a plaintext per-node URI.
3. F00/F20/F90 use the same approved embedding model and credentials.
4. Production F10 selects Component 45 `trusted_gateway` and connects only
   the gateway's authenticated subject/group outputs. The Canvas `employee_id`
   remains audit metadata only; `local_demo_fixture` is explicitly unverified.
5. The native HITL expiry sweeper and the F30 Report API are running before
   enabling actual report publication.
