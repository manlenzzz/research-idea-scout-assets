# Data Location

The authoritative asset store is outside this git repo on the shared dataset
volume. The two spellings below refer to the same CPFS directory:

- 1018: `/vePFS-Mindverse/share/dataset/research-idea-scout-assets`
- 6688: `/mnt/data/share/dataset/research-idea-scout-assets`
- MLP/local: unavailable; generated corpora and portal databases are forbidden
  on this host.

Current canonical asset library:

- Store root: `$IDEASCOUT_ASSET_STORE`
- Portal DB: `$IDEASCOUT_ASSET_STORE/portal.db`
- Store manifest: `$IDEASCOUT_ASSET_STORE/MANIFEST.json`

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

Archived repo-local generated data is retained under
`$IDEASCOUT_ASSET_STORE/archive/repo-local-2026-06-17`.
