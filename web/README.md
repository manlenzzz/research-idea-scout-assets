# IdeaScout Web Portal

IdeaScout includes a lightweight FastAPI web portal for browsing the canonical
research asset library. It is optional. The command-line tools can be used
without the portal.

## 1. Install web dependencies

```bash
pip install fastapi uvicorn jinja2
```

## 2. Rebuild the canonical portal database

```bash
python scripts/build_portal_from_store.py \
  --store /vePFS-Mindverse/user/intern/zhouch/asset_store
```

## 3. Run the portal

```bash
python -m uvicorn web.app.main:app \
  --host 127.0.0.1 \
  --port 8080
```

By default, the portal reads:

```text
/vePFS-Mindverse/user/intern/zhouch/asset_store/portal.db
```

For a temporary local experiment, import JSONL into a separate database and set
`IDEASCOUT_PORTAL_DB` when launching the server.

Open:

```text
http://127.0.0.1:8080
```

If you run the portal on a remote server, use SSH port forwarding:

```bash
ssh -N -L 8080:127.0.0.1:8080 user@server
```

Then open the same local URL in your browser.
