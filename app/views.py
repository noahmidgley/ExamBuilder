"""
The website's pages (everything except login/logout).

Pages:
  /                  subject picker
  /bank              question bank with filters
  /review            questions the importer flagged
  /builder           the paper you're currently building
  /papers            your saved papers
  /settings          change password, download a database backup
  /media/...         question / mark scheme / insert images (login required)

The paper being built lives in the session (a signed cookie) as a list of
question ids, one list per subject. That makes "Add to paper" instant.
Pressing Save writes it to the built_papers tables.
"""
import io
import json
import math
import re
import zlib
from datetime import date
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash

import pymupdf

import config
from app import auth, pdf_export
from app.formatting import question_label
from app.db import get_db
from importer import splitter
from importer.topics import all_topics, topics_for

bp = Blueprint("main", __name__)

SUBJECTS = {
    "geography": {
        "name": "Geography", "board": "AQA 7037",
        "papers": {"P1": "Paper 1", "P2": "Paper 2"},
        # Real papers: 120 marks in 2h30 -> 1.25 minutes per mark.
        "minutes_per_mark": 150 / 120,
    },
    "computer-science": {
        "name": "Computer Science", "board": "OCR H446",
        "papers": {"C01": "Component 01", "C02": "Component 02"},
        # Real papers: 140 marks in 2h30 -> about 1.07 minutes per mark.
        "minutes_per_mark": 150 / 140,
    },
}
PER_PAGE = 30


# --------------------------------------------------------------------------
# Helpers shared by several pages
# --------------------------------------------------------------------------

def current_subject():
    return session.get("subject") if session.get("subject") in SUBJECTS else None


def need_subject():
    """Most pages need a subject chosen; send you to the picker if not."""
    subject = current_subject()
    if subject is None:
        abort(redirect(url_for("main.home")))
    return subject


def estimated_minutes(subject, marks):
    """Time at the real exam's pace, rounded up to 5 minutes."""
    if not marks:
        return 0
    return max(5, math.ceil(marks * SUBJECTS[subject]["minutes_per_mark"] / 5) * 5)


def format_minutes(minutes):
    hours, mins = divmod(minutes, 60)
    return f"{hours} h {mins:02d} min" if hours else f"{mins} min"


def exam_time(minutes):
    """How exam covers write times: '45 minutes', '1 hour', '2 hours 30 minutes'."""
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if mins or not hours:
        parts.append(f"{mins} minutes")
    return " ".join(parts)


def source_label(q):
    """'Paper 1, June 2022, Q3.4'"""
    paper = SUBJECTS[q["subject"]]["papers"].get(q["paper"], q["paper"])
    return f"{paper}, {q['series']}, Q{question_label(q['subject'], q['question_number'])}"


def insert_images(q):
    """URLs of the insert pages a Geography question uses."""
    return [url_for("main.insert_img", source_id=q["source_paper_id"], page=p)
            for p in json.loads(q["insert_pages"] or "[]")]


# Columns every page that shows questions needs (joined with the source paper).
QUESTION_COLUMNS = """
    q.*, sp.qp_filename, sp.ms_filename, sp.ins_filename, sp.year
    FROM questions q JOIN source_papers sp ON sp.id = q.source_paper_id
"""


def fetch_questions(ids):
    """Questions by id, in the order given (missing/deleted ids are skipped)."""
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = get_db().execute(f"SELECT {QUESTION_COLUMNS} WHERE q.id IN ({marks})", ids).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


# --------------------------------------------------------------------------
# The paper being built (kept in the session)
# --------------------------------------------------------------------------

def basket(subject):
    baskets = session.setdefault("baskets", {})
    return baskets.setdefault(subject, {"paper_id": None, "title": "", "ids": []})


def save_basket(subject, data):
    session["baskets"][subject] = data
    session.modified = True  # tell Flask the cookie needs re-sending


def basket_summary(subject):
    questions = fetch_questions(basket(subject)["ids"])
    marks = sum(q["marks"] or 0 for q in questions)
    return {"count": len(questions), "marks": marks,
            "time": format_minutes(estimated_minutes(subject, marks))}


@bp.app_template_filter("with_args")
def with_args(args, **changes):
    """Copy of the current URL's query arguments with some changed (None removes one).

    Used for pagination links that keep all the ticked filters.
    """
    merged = dict(args)
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def static_url(filename):
    """URL for a static file with ?v=<last modified time> on the end.

    When the file changes, the URL changes, so browsers fetch the new copy
    instead of using an old cached one ("cache busting").
    """
    path = Path(current_app.static_folder) / filename
    version = int(path.stat().st_mtime) if path.exists() else 0
    return url_for("static", filename=filename, v=version)


@bp.app_context_processor
def template_globals():
    """Variables available in every template (used by the top bar)."""
    subject = current_subject() if session.get("logged_in") else None
    return {
        "SUBJECTS": SUBJECTS,
        "subject": subject,
        "basket_info": basket_summary(subject) if subject else None,
        "source_label": source_label,
        "insert_images": insert_images,
        "static_url": static_url,
        "question_img_url": question_img_url,
    }


# --------------------------------------------------------------------------
# Subject picker
# --------------------------------------------------------------------------

@bp.route("/")
def home():
    db = get_db()
    counts = {s: db.execute("SELECT COUNT(*) FROM questions WHERE subject = ?", (s,)).fetchone()[0]
              for s in SUBJECTS}
    return render_template("home.html", counts=counts)


@bp.route("/subject/<name>")
def choose_subject(name):
    if name not in SUBJECTS:
        abort(404)
    session["subject"] = name
    # safe_next only allows our own pages, so this link can't send you to another website.
    target = request.args.get("next")
    return redirect(auth.safe_next(target) if target else url_for("main.bank"))


# --------------------------------------------------------------------------
# Question bank
# --------------------------------------------------------------------------

def bank_filters():
    """Read the filter checkboxes from the URL (?topic=...&marks=6&...)."""
    args = request.args
    return {
        "topic": args.getlist("topic"),
        "subtopic": args.getlist("subtopic"),
        "paper": args.getlist("paper"),
        "series": args.getlist("series"),
        "marks": [int(m) for m in args.getlist("marks") if m.isdigit()],
        "text": args.get("text", "").strip(),
        "include_review": args.get("include_review") == "1",
    }


def bank_query(subject, f):
    """Build the WHERE clause from the filters.

    Each filter adds a condition; the values go in `params` and are passed
    separately to SQLite (the ? placeholders), never pasted into the SQL -
    that's what prevents SQL injection.
    """
    where, params = ["q.subject = ?"], [subject]
    if not f["include_review"]:
        where.append("q.status = 'approved'")
    for column, values in (("q.topic", f["topic"]), ("q.subtopic", f["subtopic"]),
                           ("q.paper", f["paper"]), ("q.series", f["series"]), ("q.marks", f["marks"])):
        if values:
            where.append(f"{column} IN ({','.join('?' * len(values))})")
            params += values
    if f["text"]:
        where.append("(q.question_text LIKE ? OR q.markscheme_text LIKE ?)")
        params += [f"%{f['text']}%"] * 2
    return " AND ".join(where), params


@bp.route("/bank")
def bank():
    subject = need_subject()
    f = bank_filters()
    where, params = bank_query(subject, f)
    db = get_db()

    total = db.execute(f"SELECT COUNT(*) FROM questions q WHERE {where}", params).fetchone()[0]
    page = max(1, request.args.get("page", 1, type=int))
    questions = db.execute(
        f"""SELECT {QUESTION_COLUMNS} WHERE {where}
            ORDER BY q.paper, sp.year IS NULL, sp.year DESC, sp.series_key, q.sort_order
            LIMIT ? OFFSET ?""",
        params + [PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()

    # Options for the filter sidebar, with how many questions each has.
    status_sql = "" if f["include_review"] else "AND status = 'approved'"
    def counts(column):
        return {r[0]: r[1] for r in db.execute(
            f"SELECT {column}, COUNT(*) FROM questions WHERE subject = ? {status_sql} GROUP BY {column}",
            (subject,))}
    options = {
        "topics": all_topics(subject),
        "topic_counts": counts("topic"),
        "subtopic_counts": counts("subtopic"),
        "paper_counts": counts("paper"),
        "series": [r[0] for r in db.execute(
            """SELECT DISTINCT series FROM source_papers WHERE subject = ?
               ORDER BY year IS NULL, year DESC, series""", (subject,))],
        "marks": [r[0] for r in db.execute(
            "SELECT DISTINCT marks FROM questions WHERE subject = ? AND marks IS NOT NULL ORDER BY marks",
            (subject,))],
    }
    waiting = db.execute("SELECT COUNT(*) FROM questions WHERE subject = ? AND status = 'needs_review'",
                         (subject,)).fetchone()[0]
    return render_template(
        "bank.html", questions=questions, f=f, options=options, total=total, page=page,
        pages=max(1, math.ceil(total / PER_PAGE)), waiting=waiting,
        in_basket=set(basket(subject)["ids"]),
    )


# --------------------------------------------------------------------------
# Review
# --------------------------------------------------------------------------

@bp.route("/review")
def review():
    subject = need_subject()
    paper = request.args.get("paper", "")
    page = max(1, request.args.get("page", 1, type=int))
    where, params = "q.subject = ? AND q.status = 'needs_review'", [subject]
    if paper in SUBJECTS[subject]["papers"]:
        where += " AND q.paper = ?"
        params.append(paper)
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM questions q WHERE {where}", params).fetchone()[0]
    questions = db.execute(
        f"""SELECT {QUESTION_COLUMNS} WHERE {where}
            ORDER BY q.paper, sp.year IS NULL, sp.year DESC, q.sort_order LIMIT ? OFFSET ?""",
        params + [PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()
    return render_template("review.html", questions=questions, total=total, page=page, paper=paper,
                           pages=max(1, math.ceil(total / PER_PAGE)), topics_for=topics_for)


def get_question(qid):
    q = get_db().execute(f"SELECT {QUESTION_COLUMNS} WHERE q.id = ?", (qid,)).fetchone()
    if q is None:
        abort(404)
    return q


@bp.route("/question/<int:qid>/update", methods=["POST"])
def update_question(qid):
    q = get_question(qid)
    subtopic = request.form.get("subtopic", "")
    marks = request.form.get("marks", "")
    approve = request.form.get("action") == "approve"

    # The sub-topic dropdown is grouped by topic, so the topic follows from it.
    topic = next((t for t, subs in topics_for(q["subject"], q["paper"]).items() if subtopic in subs), None)
    if subtopic and topic is None:
        flash("That sub-topic isn't on this paper's specification.", "error")
        return redirect(request.referrer or url_for("main.review"))
    if not (marks.isdigit() and 0 < int(marks) <= 50):
        flash("Marks must be a whole number from 1 to 50.", "error")
        return redirect(request.referrer or url_for("main.review"))
    if approve and not subtopic:
        flash("Choose a sub-topic before approving, so the question can be filtered.", "error")
        return redirect(request.referrer or url_for("main.review"))

    with get_db() as db:
        db.execute(
            """UPDATE questions SET topic = ?, subtopic = ?, marks = ?,
                   tag_confidence = CASE WHEN ? THEN 'manual' ELSE tag_confidence END,
                   status = CASE WHEN ? THEN 'approved' ELSE status END
               WHERE id = ?""",
            (topic or q["topic"], subtopic or q["subtopic"], int(marks),
             bool(subtopic and subtopic != q["subtopic"]), approve, qid),
        )
    flash(f"Q{q['question_number']} ({q['series']}) {'approved' if approve else 'saved'}.", "success")
    return redirect(request.referrer or url_for("main.review"))


@bp.route("/question/<int:qid>/delete", methods=["POST"])
def delete_question(qid):
    q = get_question(qid)
    with get_db() as db:
        db.execute("DELETE FROM questions WHERE id = ?", (qid,))
    # Image files are left on disk; they're small and re-importing reuses them.
    flash(f"Deleted Q{q['question_number']} ({q['series']}).", "success")
    return redirect(request.referrer or url_for("main.review"))


# --------------------------------------------------------------------------
# Paper builder
# --------------------------------------------------------------------------

def wants_json():
    return request.is_json or request.accept_mimetypes.best == "application/json"


def builder_response(subject, message):
    """Reply to the browser's JavaScript with JSON, or redirect if JS is off."""
    if wants_json():
        return jsonify(basket_summary(subject) | {"message": message})
    flash(message, "success")
    return redirect(request.referrer or url_for("main.builder"))


def posted_id():
    data = request.get_json(silent=True) or request.form
    try:
        return int(data.get("id"))
    except (TypeError, ValueError):
        abort(400)


@bp.route("/builder")
def builder():
    subject = need_subject()
    b = basket(subject)
    questions = fetch_questions(b["ids"])
    if len(questions) != len(b["ids"]):  # some were deleted since being added
        b["ids"] = [q["id"] for q in questions]
        save_basket(subject, b)
    return render_template("builder.html", b=b, questions=questions)


@bp.route("/builder/add", methods=["POST"])
def builder_add():
    subject = need_subject()
    qid = posted_id()
    q = get_question(qid)
    if q["subject"] != subject:
        abort(400)
    b = basket(subject)
    if qid not in b["ids"]:
        b["ids"].append(qid)
        save_basket(subject, b)
    return builder_response(subject, f"Added Q{q['question_number']} ({q['series']}).")


@bp.route("/builder/remove", methods=["POST"])
def builder_remove():
    subject = need_subject()
    qid = posted_id()
    b = basket(subject)
    if qid in b["ids"]:
        b["ids"].remove(qid)
        save_basket(subject, b)
    return builder_response(subject, "Removed from paper.")


@bp.route("/builder/reorder", methods=["POST"])
def builder_reorder():
    """Called by the drag-and-drop JavaScript with the new order of ids."""
    subject = need_subject()
    data = request.get_json(silent=True) or {}
    new_order = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    b = basket(subject)
    if sorted(new_order) != sorted(b["ids"]):
        abort(400)  # must be the same questions, just reordered
    b["ids"] = new_order
    save_basket(subject, b)
    return builder_response(subject, "Order saved.")


@bp.route("/builder/move", methods=["POST"])
def builder_move():
    """Up/down buttons: a keyboard-friendly alternative to dragging."""
    subject = need_subject()
    qid, step = posted_id(), (-1 if request.form.get("direction") == "up" else 1)
    b = basket(subject)
    if qid in b["ids"]:
        i = b["ids"].index(qid)
        j = i + step
        if 0 <= j < len(b["ids"]):
            b["ids"][i], b["ids"][j] = b["ids"][j], b["ids"][i]
            save_basket(subject, b)
    return redirect(url_for("main.builder"))


@bp.route("/builder/save", methods=["POST"])
def builder_save():
    subject = need_subject()
    b = basket(subject)
    b["title"] = request.form.get("title", "").strip()[:120] or "Untitled paper"
    if not b["ids"]:
        flash("Add some questions before saving.", "error")
        return redirect(url_for("main.builder"))
    with get_db() as db:
        if b["paper_id"] and db.execute("SELECT 1 FROM built_papers WHERE id = ?", (b["paper_id"],)).fetchone():
            db.execute("UPDATE built_papers SET title = ?, updated_at = datetime('now') WHERE id = ?",
                       (b["title"], b["paper_id"]))
            db.execute("DELETE FROM built_paper_questions WHERE built_paper_id = ?", (b["paper_id"],))
        else:
            b["paper_id"] = db.execute("INSERT INTO built_papers (subject, title) VALUES (?, ?)",
                                       (subject, b["title"])).lastrowid
        db.executemany(
            "INSERT INTO built_paper_questions (built_paper_id, question_id, position) VALUES (?, ?, ?)",
            [(b["paper_id"], qid, pos) for pos, qid in enumerate(b["ids"])],
        )
    save_basket(subject, b)
    flash(f"Saved “{b['title']}”.", "success")
    return redirect(url_for("main.builder"))


@bp.route("/builder/new", methods=["POST"])
def builder_new():
    subject = need_subject()
    save_basket(subject, {"paper_id": None, "title": "", "ids": []})
    return redirect(url_for("main.bank"))


# --------------------------------------------------------------------------
# My papers
# --------------------------------------------------------------------------

def get_built_paper(pid):
    paper = get_db().execute("SELECT * FROM built_papers WHERE id = ?", (pid,)).fetchone()
    if paper is None:
        abort(404)
    return paper


def built_paper_ids(pid):
    return [r[0] for r in get_db().execute(
        "SELECT question_id FROM built_paper_questions WHERE built_paper_id = ? ORDER BY position", (pid,))]


@bp.route("/papers")
def papers():
    subject = need_subject()
    rows = get_db().execute(
        """SELECT bp.*, COUNT(q.id) AS n, COALESCE(SUM(q.marks), 0) AS marks
           FROM built_papers bp
           LEFT JOIN built_paper_questions bpq ON bpq.built_paper_id = bp.id
           LEFT JOIN questions q ON q.id = bpq.question_id
           WHERE bp.subject = ? GROUP BY bp.id ORDER BY bp.updated_at DESC""",
        (subject,),
    ).fetchall()
    return render_template("papers.html", papers=rows,
                           minutes=lambda m: format_minutes(estimated_minutes(subject, m)))


@bp.route("/papers/<int:pid>")
def paper_view(pid):
    paper = get_built_paper(pid)
    if paper["subject"] != current_subject():
        session["subject"] = paper["subject"]
    questions = fetch_questions(built_paper_ids(pid))
    marks = sum(q["marks"] or 0 for q in questions)
    return render_template("paper_view.html", paper=paper, questions=questions, marks=marks,
                           time=format_minutes(estimated_minutes(paper["subject"], marks)))


@bp.route("/papers/<int:pid>/edit", methods=["POST"])
def paper_edit(pid):
    paper = get_built_paper(pid)
    session["subject"] = paper["subject"]
    save_basket(paper["subject"], {"paper_id": pid, "title": paper["title"], "ids": built_paper_ids(pid)})
    return redirect(url_for("main.builder"))


@bp.route("/papers/<int:pid>/duplicate", methods=["POST"])
def paper_duplicate(pid):
    paper = get_built_paper(pid)
    with get_db() as db:
        new_id = db.execute("INSERT INTO built_papers (subject, title) VALUES (?, ?)",
                            (paper["subject"], f"{paper['title']} (copy)")).lastrowid
        db.execute(
            """INSERT INTO built_paper_questions (built_paper_id, question_id, position)
               SELECT ?, question_id, position FROM built_paper_questions WHERE built_paper_id = ?""",
            (new_id, pid),
        )
    flash(f"Made a copy of “{paper['title']}”.", "success")
    return redirect(url_for("main.papers"))


@bp.route("/papers/<int:pid>/delete", methods=["POST"])
def paper_delete(pid):
    paper = get_built_paper(pid)
    with get_db() as db:
        db.execute("DELETE FROM built_papers WHERE id = ?", (pid,))
    b = basket(paper["subject"])
    if b["paper_id"] == pid:  # it was open in the builder: keep the questions as a new draft
        b["paper_id"] = None
        save_basket(paper["subject"], b)
    flash(f"Deleted “{paper['title']}”.", "success")
    return redirect(url_for("main.papers"))


def pdf_response(data, title, suffix):
    filename = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "paper"
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{filename}{suffix}.pdf")


@bp.route("/papers/<int:pid>/paper.pdf")
def paper_pdf(pid):
    paper = get_built_paper(pid)
    questions = fetch_questions(built_paper_ids(pid))
    marks = sum(q["marks"] or 0 for q in questions)
    cover = request.args.get("cover") != "0"
    data = pdf_export.build_question_paper(
        paper, questions, source_label,
        time_text=exam_time(estimated_minutes(paper["subject"], marks)),
        paper_names=SUBJECTS[paper["subject"]]["papers"], cover=cover,
    )
    return pdf_response(data, paper["title"], "" if cover else "-no-cover")


@bp.route("/papers/<int:pid>/markscheme.pdf")
def markscheme_pdf(pid):
    paper = get_built_paper(pid)
    questions = fetch_questions(built_paper_ids(pid))
    data = pdf_export.build_mark_scheme(paper, questions, source_label)
    return pdf_response(data, paper["title"], "-mark-scheme")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@bp.route("/settings")
def settings():
    signups = get_db().execute("SELECT * FROM interest ORDER BY created_at DESC LIMIT 100").fetchall()
    email_ready = bool(current_app.config.get("RESEND_API_KEY") and current_app.config.get("NOTIFY_EMAIL"))
    return render_template("settings.html", signups=signups, email_ready=email_ready)


@bp.route("/settings/password", methods=["POST"])
def change_password():
    current = request.form.get("current", "")
    new = request.form.get("new", "")
    if not check_password_hash(auth.password_hash() or "", current):
        flash("Your current password was wrong.", "error")
    elif len(new) < 10:
        flash("The new password must be at least 10 characters.", "error")
    elif new != request.form.get("confirm", ""):
        flash("The new passwords didn't match.", "error")
    else:
        auth.set_password(new)
        flash("Password changed.", "success")
    return redirect(url_for("main.settings"))


@bp.route("/settings/backup")
def backup():
    """Download a consistent copy of the database.

    serialize() asks SQLite for a snapshot of the whole database as bytes,
    which is safe even while the site is being used.
    """
    data = get_db().serialize()
    return send_file(io.BytesIO(data), mimetype="application/vnd.sqlite3", as_attachment=True,
                     download_name=f"exambuilder-backup-{date.today().isoformat()}.db")


# --------------------------------------------------------------------------
# Images (only reachable when logged in - see auth.require_login)
# --------------------------------------------------------------------------
#
# Question and mark scheme images are drawn on demand from the original PDFs,
# using the crop positions stored in the database. That means the server
# doesn't need to store ~250 MB of PNGs (important on a free host with a
# 512 MB limit). If the PDF isn't there, the stored PNG is used instead.

WEB_ZOOM = 2.0  # 144 dpi: the same sharpness as the images made by the importer


def papers_folder(subject):
    return config.GEOGRAPHY_PAPERS_DIR if subject == "geography" else config.CS_PAPERS_DIR


def image_response(png, fallback_dir=None, fallback_name=None):
    if png is not None:
        response = send_file(io.BytesIO(png), mimetype="image/png", max_age=86400)
    elif fallback_name and (fallback_dir / fallback_name).exists():
        response = send_from_directory(fallback_dir, fallback_name, max_age=86400)
    else:
        abort(404)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@bp.route("/img/<int:qid>/<kind>.png")
def question_img(qid, kind):
    if kind not in ("question", "markscheme"):
        abort(404)
    q = get_question(qid)
    regions = json.loads(q[f"{kind}_regions"] or "[]")
    pdf_path = papers_folder(q["subject"]) / (q["qp_filename"] if kind == "question" else (q["ms_filename"] or ""))
    png = None
    if regions and pdf_path.is_file():
        with pymupdf.open(pdf_path) as doc:
            png = splitter.render_png(doc, regions, zoom=WEB_ZOOM)
    folder = config.QUESTION_IMAGES_DIR if kind == "question" else config.MARKSCHEME_IMAGES_DIR
    return image_response(png, folder, q[f"{'question' if kind == 'question' else 'markscheme'}_image"])


@bp.route("/img/insert/<int:source_id>/<int:page>.png")
def insert_img(source_id, page):
    sp = get_db().execute("SELECT * FROM source_papers WHERE id = ?", (source_id,)).fetchone()
    if sp is None or not sp["ins_filename"]:
        abort(404)
    pdf_path = papers_folder(sp["subject"]) / sp["ins_filename"]
    png = None
    if pdf_path.is_file():
        with pymupdf.open(pdf_path) as doc:
            if 0 <= page < doc.page_count:
                png = doc[page].get_pixmap(matrix=pymupdf.Matrix(WEB_ZOOM, WEB_ZOOM), alpha=False).tobytes("png")
    stem = sp["qp_filename"][: -len("_QP.pdf")]
    return image_response(png, config.INSERT_IMAGES_DIR, f"{sp['subject']}/{stem}/page-{page + 1}.png")


def question_img_url(q, kind):
    """URL for a question/mark scheme image. The ?v= part changes whenever the
    crop changes (e.g. after --refresh), so browsers fetch the new image."""
    regions = q[f"{kind}_regions"] or ""
    return url_for("main.question_img", qid=q["id"], kind=kind, v=f"{zlib.crc32(regions.encode()):08x}")

MEDIA_DIRS = {
    "questions": lambda: config.QUESTION_IMAGES_DIR,
    "markschemes": lambda: config.MARKSCHEME_IMAGES_DIR,
    "inserts": lambda: config.INSERT_IMAGES_DIR,
}


@bp.route("/media/<kind>/<path:filename>")
def media(kind, filename):
    if kind not in MEDIA_DIRS:
        abort(404)
    # send_from_directory refuses paths like "../../.env", so files outside
    # the storage folder can never be requested.
    response = send_from_directory(MEDIA_DIRS[kind](), filename, max_age=86400)
    response.headers["Cache-Control"] = "private, max-age=86400"  # never cached by shared proxies
    return response
