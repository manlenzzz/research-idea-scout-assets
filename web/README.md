# IdeaScout Web Portal

IdeaScout includes a lightweight FastAPI web portal for browsing scored papers.
It is optional. The command-line tools can be used without the portal.

## 1. Install web dependencies

```bash
pip install fastapi uvicorn jinja2
```

## 2. Import scored JSONL

```bash
python web/import_jsonl.py \
  --input data/idea_scores.jsonl \
  --db web/ideascout_portal.db
```

## 3. Run the portal

```bash
python -m uvicorn web.app.main:app \
  --host 127.0.0.1 \
  --port 8080
```

Open:

```text
http://127.0.0.1:8080
```

If you run the portal on a remote server, use SSH port forwarding:

```bash
ssh -N -L 8080:127.0.0.1:8080 user@server
```

Then open the same local URL in your browser.
