"""
Stage 3: import every QP/MS pair in papers/ into the question bank.

Run from the project folder:
    .venv/bin/python -m scripts.import_papers               # import new papers (and tag with Claude)
    .venv/bin/python -m scripts.import_papers --no-tag      # split only; tag later
    .venv/bin/python -m scripts.import_papers --retag       # tag questions that aren't tagged yet
    .venv/bin/python -m scripts.import_papers --retag --limit 10   # ...just 10 of them, as a trial
    .venv/bin/python -m scripts.import_papers --redo GEOG_P1_2022_June   # delete and re-import one paper
    .venv/bin/python -m scripts.import_papers --refresh     # re-cut crops after a splitter fix, keeping your work

For each new paper it:
  1. Splits the question paper into parts (importer/splitter.py).
  2. Splits the mark scheme and links each part to its mark scheme.
  3. For Geography, works out which insert pages hold the figures each
     question mentions.
  4. Renders each question and mark scheme crop to a PNG in storage/.
  5. Asks Claude for a topic and sub-topic (importer/tagging.py).
  6. Saves everything to the database in one transaction - so a crash part
     way through leaves nothing half-imported, and re-running just retries.

A question is 'approved' automatically only when nothing looked wrong:
marks found, mark scheme matched, figures found and Claude confident about
the topic. Anything else goes to 'needs review' with the reasons listed.
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from importer import splitter  # noqa: E402
from importer.topics import topics_for  # noqa: E402

# Matches our download naming scheme, e.g. GEOG_P1_2022_June_QP.pdf
QP_NAME_RE = re.compile(
    r"^(?P<prefix>GEOG|CS)_(?P<paper>P[12]|C0[12])_(?P<series>\d{4}_(?:June|November)|Specimen)_QP\.pdf$"
)
TAG_WORKERS = 4  # questions tagged in parallel (the API is the slow part)
NOT_TAGGED = "Topic: not tagged yet"


def describe_series(series_key):
    """'2022_June' -> ('June 2022', 2022); 'Specimen' -> ('Specimen', None)."""
    if series_key == "Specimen":
        return "Specimen", None
    year, month = series_key.split("_")
    return f"{month} {year}", int(year)


def slug(number):
    """'2(b)(i)' -> '2-b-i', safe for a filename."""
    return re.sub(r"[^a-z0-9]+", "-", number.lower()).strip("-")


def find_papers():
    """Every question paper in papers/, with its mark scheme and insert if present."""
    found = []
    for folder in (config.GEOGRAPHY_PAPERS_DIR, config.CS_PAPERS_DIR):
        for qp in sorted(folder.glob("*_QP.pdf")):
            m = QP_NAME_RE.match(qp.name)
            if not m:
                continue
            ms = qp.with_name(qp.name.replace("_QP.pdf", "_MS.pdf"))
            ins = qp.with_name(qp.name.replace("_QP.pdf", "_INS.pdf"))
            found.append({
                "stem": qp.name[:-len("_QP.pdf")],
                "subject": "geography" if m.group("prefix") == "GEOG" else "computer-science",
                "paper": m.group("paper"),
                "series_key": m.group("series"),
                "qp": qp,
                "ms": ms if ms.exists() else None,
                "ins": ins if ins.exists() else None,
            })
    return found


# --------------------------------------------------------------------------
# Tagging helpers
# --------------------------------------------------------------------------

def make_client():
    """A Claude client, or None if there's no API key configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def tag_problems(result):
    """Review reasons that come from tagging (prefixed 'Topic:')."""
    if result is None:
        return [NOT_TAGGED]
    if result.confidence == "error":
        return [f"Topic: tagging failed ({result.reason})"]
    if result.confidence != "high":
        return [f"Topic: Claude was only {result.confidence}ly confident ({result.reason})"]
    return []


def tag_all(client, subject, paper, jobs):
    """Tag several questions in parallel. jobs = list of dicts for tag_question."""
    from importer.tagging import tag_question
    if client is None:
        return [None] * len(jobs)

    def run(job):
        return tag_question(client, subject, paper, **job)

    with ThreadPoolExecutor(max_workers=TAG_WORKERS) as pool:
        return list(pool.map(run, jobs))


# --------------------------------------------------------------------------
# Importing one paper
# --------------------------------------------------------------------------

def cut_paper(info, images=True):
    """Split one paper into questions and render their images.

    Shared by a normal import and --refresh. Returns (rows, qp_doc), or
    (None, None) if no questions were found.
    """
    subject, paper, stem = info["subject"], info["paper"], info["stem"]
    board = "aqa" if subject == "geography" else "ocr"
    qp_doc = pymupdf.open(info["qp"])
    parts, errors = splitter.split_question_paper(qp_doc, board, topics_for(subject, paper).keys())
    if errors or not parts:
        print(f"  skipped: {'; '.join(errors) or 'no questions found'}")
        return None, None

    ms_doc = pymupdf.open(info["ms"]) if info["ms"] else None
    ms_items = splitter.split_mark_scheme(ms_doc, board) if ms_doc else {}
    ins_doc = pymupdf.open(info["ins"]) if info["ins"] else None
    figure_pages = splitter.insert_figure_pages(ins_doc) if ins_doc else {}

    # Output folders: storage/<kind>/<subject>/<paper stem>/
    q_dir = config.QUESTION_IMAGES_DIR / subject / stem
    ms_dir = config.MARKSCHEME_IMAGES_DIR / subject / stem
    for d in (q_dir, ms_dir):
        d.mkdir(parents=True, exist_ok=True)

    rows = []
    for part in parts:
        if ms_doc:
            ms_regions, ms_text = splitter.match_mark_scheme(part, ms_items)
        else:
            ms_regions, ms_text = [], ""
            part.problems.append("no mark scheme file for this paper")
        insert_pages = splitter.figures_for(part, figure_pages) if ins_doc else []
        if subject == "geography" and not ins_doc and re.search(r"in the insert", part.text, re.I):
            part.problems.append("uses a figure from the insert, but the insert hasn't been downloaded")

        q_file = f"{subject}/{stem}/q{slug(part.number)}.png"
        ms_file = f"{subject}/{stem}/q{slug(part.number)}.png" if ms_regions else None
        if images:  # the website can also draw these from the PDFs (e.g. on the server)
            splitter.render_regions(qp_doc, part.all_regions, config.QUESTION_IMAGES_DIR / q_file)
            if ms_file:
                splitter.render_regions(ms_doc, ms_regions, config.MARKSCHEME_IMAGES_DIR / ms_file)

        rows.append({"part": part, "q_file": q_file, "ms_file": ms_file, "ms_regions": ms_regions,
                     "ms_text": ms_text, "insert_pages": insert_pages})

    # Render the insert pages any question uses (once per page).
    if ins_doc and images:
        ins_dir = config.INSERT_IMAGES_DIR / subject / stem
        ins_dir.mkdir(parents=True, exist_ok=True)
        for pno in sorted({p for r in rows for p in r["insert_pages"]}):
            ins_doc[pno].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(ins_dir / f"page-{pno + 1}.png")
    return rows, qp_doc


def import_paper(conn, info, client):
    subject, paper, stem = info["subject"], info["paper"], info["stem"]
    board = "aqa" if subject == "geography" else "ocr"
    series, year = describe_series(info["series_key"])
    print(f"\n{stem}")
    rows, qp_doc = cut_paper(info)
    if rows is None:
        return 0

    # Ask Claude for topics (in parallel).
    if client:
        print(f"  tagging {len(rows)} questions with Claude...")
    results = tag_all(client, subject, paper, [
        {"number": r["part"].number, "marks": r["part"].marks, "text": r["part"].text,
         "ms_text": r["ms_text"], "heading": r["part"].heading}
        for r in rows
    ])

    total = splitter.paper_total(qp_doc, board)
    found = sum(r["part"].marks or 0 for r in rows)
    with conn:  # one transaction: all of this paper, or none of it
        cur = conn.execute(
            """INSERT INTO source_papers (subject, paper, series_key, series, year,
                   qp_filename, ms_filename, ins_filename, total_marks)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (subject, paper, info["series_key"], series, year, info["qp"].name,
             info["ms"].name if info["ms"] else None, info["ins"].name if info["ins"] else None, total),
        )
        source_id = cur.lastrowid
        for r, result in zip(rows, results):
            part = r["part"]
            problems = part.problems + tag_problems(result)
            conn.execute(
                """INSERT INTO questions (source_paper_id, subject, paper, series, question_number,
                       sort_order, marks, topic, subtopic, tag_confidence, question_image,
                       markscheme_image, question_regions, markscheme_regions, insert_pages,
                       question_text, markscheme_text, status, review_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, subject, paper, series, part.number, part.sort_order, part.marks,
                 result.topic if result else part.heading,
                 result.subtopic if result else None,
                 result.confidence if result else None,
                 r["q_file"], r["ms_file"], json.dumps(part.all_regions), json.dumps(r["ms_regions"]),
                 json.dumps(r["insert_pages"]), part.text, r["ms_text"],
                 "approved" if not problems else "needs_review",
                 "; ".join(problems) or None),
            )

    flagged = sum(1 for r, res in zip(rows, results) if r["part"].problems or tag_problems(res))
    check = "" if board == "aqa" or total is None or total == found else f"  (cover says {total}!)"
    print(f"  {len(rows)} questions, {found} marks{check}, {flagged} need review")
    return len(rows)


def retag(conn, client, limit=None):
    """Tag questions that were imported without a topic (or where tagging failed)."""
    todo = conn.execute(
        """SELECT * FROM questions WHERE tag_confidence IS NULL OR tag_confidence = 'error'
           ORDER BY subject, paper, source_paper_id, sort_order LIMIT ?""",
        (limit if limit else -1,),  # LIMIT -1 means no limit in SQLite
    ).fetchall()
    print(f"Tagging {len(todo)} questions...")
    groups = {}
    for q in todo:
        groups.setdefault((q["subject"], q["paper"]), []).append(q)
    for (subject, paper), questions in groups.items():
        topics = topics_for(subject, paper)
        results = tag_all(client, subject, paper, [
            {"number": q["question_number"], "marks": q["marks"], "text": q["question_text"],
             "ms_text": q["markscheme_text"] or "",
             # Geography: the importer stored the section heading as the topic.
             "heading": q["topic"] if q["topic"] in topics else None}
            for q in questions
        ])
        with conn:
            for q, result in zip(questions, results):
                # Keep non-topic problems, replace the topic ones.
                old = [p for p in (q["review_reason"] or "").split("; ") if p and not p.startswith("Topic:")]
                problems = old + tag_problems(result)
                conn.execute(
                    """UPDATE questions SET topic = ?, subtopic = ?, tag_confidence = ?,
                           status = ?, review_reason = ? WHERE id = ?""",
                    (result.topic or q["topic"], result.subtopic, result.confidence,
                     "approved" if not problems else "needs_review", "; ".join(problems) or None, q["id"]),
                )
        print(f"  {subject} {paper}: done")


def refresh(conn, images=True):
    """Re-cut every imported paper with the current splitter, keeping your work.

    Updates each question's crop, images, text, mark scheme link and insert
    pages. Leaves alone: topic, sub-topic, marks, status and saved papers.
    Questions are matched by paper + question number.
    """
    imported = {(r["subject"], r["paper"], r["series_key"]): r["id"]
                for r in conn.execute("SELECT * FROM source_papers")}
    for info in find_papers():
        source_id = imported.get((info["subject"], info["paper"], info["series_key"]))
        if source_id is None:
            continue
        print(f"{info['stem']}: ", end="")
        rows, _ = cut_paper(info, images=images)
        if rows is None:
            continue
        existing = {r["question_number"] for r in conn.execute(
            "SELECT question_number FROM questions WHERE source_paper_id = ?", (source_id,))}
        updated = 0
        with conn:
            for r in rows:
                part = r["part"]
                if part.number not in existing:
                    continue
                conn.execute(
                    """UPDATE questions SET question_regions = ?, markscheme_regions = ?, insert_pages = ?,
                           question_text = ?, markscheme_text = ?, question_image = ?, markscheme_image = ?,
                           marks = COALESCE(marks, ?)
                       WHERE source_paper_id = ? AND question_number = ?""",
                    (json.dumps(part.all_regions), json.dumps(r["ms_regions"]), json.dumps(r["insert_pages"]),
                     part.text, r["ms_text"], r["q_file"], r["ms_file"], part.marks,
                     source_id, part.number),
                )
                updated += 1
        new = sorted({r["part"].number for r in rows} - existing)
        gone = sorted(existing - {r["part"].number for r in rows})
        print(f"{updated} updated" + (f", not in database: {new}" if new else "")
              + (f", no longer found: {gone}" if gone else ""))


def delete_paper(conn, stem):
    """Remove one imported paper (and its questions) so it can be re-imported."""
    for info in find_papers():
        if info["stem"] == stem:
            with conn:
                conn.execute("DELETE FROM source_papers WHERE subject = ? AND paper = ? AND series_key = ?",
                             (info["subject"], info["paper"], info["series_key"]))
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-tag", action="store_true", help="split papers but don't call Claude")
    parser.add_argument("--retag", action="store_true", help="tag questions that have no topic yet")
    parser.add_argument("--refresh", action="store_true",
                        help="re-cut already-imported papers (keeps topics, review decisions and saved papers)")
    parser.add_argument("--no-images", action="store_true",
                        help="with --refresh: don't save PNG images (the server draws them from the PDFs)")
    parser.add_argument("--limit", type=int, help="with --retag: only tag this many questions")
    parser.add_argument("--redo", metavar="STEM", help="delete one paper (e.g. GEOG_P1_2022_June) and re-import it")
    args = parser.parse_args(argv)

    config.ensure_data_dirs()
    init_db(config.DATABASE_PATH)
    conn = connect(config.DATABASE_PATH)

    if args.refresh:
        refresh(conn, images=not args.no_images)
        return

    client = None if args.no_tag else make_client()
    if not args.no_tag and client is None:
        print("No ANTHROPIC_API_KEY in .env - importing without topics. Run with --retag later.")

    if args.retag:
        if client is None:
            sys.exit("Tagging needs ANTHROPIC_API_KEY in .env.")
        retag(conn, client, args.limit)
        return

    if args.redo and not delete_paper(conn, args.redo):
        sys.exit(f"No paper called {args.redo} in papers/")

    done = {(r["subject"], r["paper"], r["series_key"])
            for r in conn.execute("SELECT subject, paper, series_key FROM source_papers")}
    new = [p for p in find_papers() if (p["subject"], p["paper"], p["series_key"]) not in done]
    print(f"{len(done)} papers already imported, {len(new)} new.")
    total = sum(import_paper(conn, info, client) for info in new)

    waiting = conn.execute("SELECT COUNT(*) FROM questions WHERE status = 'needs_review'").fetchone()[0]
    print(f"\nImported {total} questions. {waiting} in total are waiting for review.")


if __name__ == "__main__":
    main()
