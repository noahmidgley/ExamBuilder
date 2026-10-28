"""
Build the zip file you upload to PythonAnywhere.

    .venv/bin/python -m scripts.make_deploy_zip            # first time: code + papers + database
    .venv/bin/python -m scripts.make_deploy_zip --update   # later: code + papers, NOT the database

The zip goes to deploy/ExamBuilder.zip.

What's included:
  - the code (app/, importer/, scripts/, config.py, wsgi.py, requirements...)
  - papers/  - the PDFs (the website draws question images from these)
  - instance/exambuilder.db - only on the first upload. With --update it's
    left out, so uploading new code never overwrites your saved papers,
    review decisions or sign-ups on the server.

What's never included:
  - .env (secrets - you create it on the server with scripts/setup_env.py)
  - .venv (installed packages - installed on the server instead)
  - storage/ (the PNG images - the server draws them from the PDFs)
"""
import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "deploy" / "ExamBuilder.zip"
LIMIT_MB = 100  # PythonAnywhere's upload limit per file

INCLUDE = ["app", "importer", "scripts", "config.py", "wsgi.py", "requirements.txt",
           "requirements-server.txt", ".env.example", ".flaskenv", "README.md", "DEPLOY.md", "papers"]
SKIP_PARTS = {"__pycache__", ".DS_Store", "inbox"}


def files_to_add(with_db):
    for name in INCLUDE:
        path = ROOT / name
        if path.is_file():
            yield path
        elif path.is_dir():
            for f in sorted(path.rglob("*")):
                if f.is_file() and not SKIP_PARTS.intersection(f.parts) and f.suffix not in {".part", ".pyc"}:
                    yield f
    if with_db:
        yield ROOT / "instance" / "exambuilder.db"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true", help="leave the database out (for updates)")
    args = parser.parse_args(argv)

    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files_to_add(with_db=not args.update):
            z.write(f, Path("ExamBuilder") / f.relative_to(ROOT))
    size = OUT.stat().st_size / 1e6
    print(f"Created {OUT} ({size:.0f} MB){' - without the database' if args.update else ''}")
    if size > LIMIT_MB:
        sys.exit(f"Too big for PythonAnywhere's {LIMIT_MB} MB upload limit - tell Claude!")


if __name__ == "__main__":
    main()
