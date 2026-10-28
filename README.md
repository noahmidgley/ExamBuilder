# ExamBuilder

Private revision site for building custom exam papers from past questions
(AQA A-level Geography 7037, OCR A-level Computer Science H446).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 scripts/setup_env.py      # creates .env (secret key, login, API key)
flask run --debug                 # http://localhost:5050
```

## Getting papers into the question bank

```bash
.venv/bin/python -m scripts.download_papers      # Stage 2: download QPs, MSs, inserts
.venv/bin/python -m scripts.import_papers        # Stage 3: split, crop, tag new papers
.venv/bin/python -m scripts.import_papers --retag --limit 10   # tag a few untagged questions
.venv/bin/python -m pytest                       # run the tests
```

Files you download by hand go in `papers/inbox/` (see `papers/download_report.md`).

## Layout

```
app/                 Flask app (routes, templates, static CSS, schema.sql)
config.py            Settings and folder paths (read from .env)
wsgi.py              Entry point (flask / gunicorn)
importer/            Splitting papers into questions (splitter.py), topic lists, Claude tagging
scripts/             Command-line tools (setup, downloader, importer)
tests/               pytest tests
papers/              Downloaded QP/MS PDFs          (not in git)
storage/             Cropped question/MS images     (not in git, never served directly)
instance/            SQLite database                (not in git)
```
