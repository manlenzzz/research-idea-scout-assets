# Data Location

The authoritative asset store is outside this git repo:

`/vePFS-Mindverse/user/intern/zhouch/asset_store`

Current canonical asset library:

- Store root: `/vePFS-Mindverse/user/intern/zhouch/asset_store`
- Portal DB: `/vePFS-Mindverse/user/intern/zhouch/asset_store/portal.db`
- Store manifest: `/vePFS-Mindverse/user/intern/zhouch/asset_store/MANIFEST.json`

The canonical library is the union of these reviewed batches:

- `bestpaper/assets.jsonl`
- `high_impact_ml/assets.jsonl`
- `high_impact_cvf/assets.jsonl`
- `high_impact_acl/assets.jsonl`
- `high_impact_expansion_20260624/assets.jsonl`
- `high_impact_codefirst_expansion_20260624/assets.jsonl`

As of 2026-06-24, the portal database contains 982 assets under this canonical
union. `bestpaper/assets.jsonl` alone contains 125 assets and should be treated
as one batch, not the full library.

Archived repo-local generated data was moved to:

`/vePFS-Mindverse/user/intern/zhouch/asset_store/archive/repo-local-2026-06-17`
