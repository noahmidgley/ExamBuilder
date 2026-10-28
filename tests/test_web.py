"""Tests for the website (Stage 4): login, pages, paper builder, review."""
import json

import pytest
from werkzeug.security import generate_password_hash

import config
from app import create_app
from app.db import connect

PASSWORD = "correct horse battery"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "QUESTION_IMAGES_DIR", tmp_path / "questions")
    monkeypatch.setattr(config, "MARKSCHEME_IMAGES_DIR", tmp_path / "markschemes")
    monkeypatch.setattr(config, "INSERT_IMAGES_DIR", tmp_path / "inserts")
    (tmp_path / "questions").mkdir()
    (tmp_path / "questions" / "q.png").write_bytes(b"\x89PNG fake image")
    (tmp_path / "secret.txt").write_text("not for you")
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE_PATH": tmp_path / "test.db",
        "LOGIN_USERNAME": "noah",
        "LOGIN_PASSWORD_HASH": generate_password_hash(PASSWORD),
        "WTF_CSRF_ENABLED": False,  # tested separately below
    })
    seed(app.config["DATABASE_PATH"])
    return app


def seed(db_path):
    """Two Geography questions (one approved) and one CS question."""
    conn = connect(db_path)
    with conn:
        sp = conn.execute(
            """INSERT INTO source_papers (subject, paper, series_key, series, year, qp_filename)
               VALUES ('geography', 'P1', '2022_June', 'June 2022', 2022, 'GEOG_P1_2022_June_QP.pdf')"""
        ).lastrowid
        cs = conn.execute(
            """INSERT INTO source_papers (subject, paper, series_key, series, year, qp_filename)
               VALUES ('computer-science', 'C01', '2022_June', 'June 2022', 2022, 'CS_C01_2022_June_QP.pdf')"""
        ).lastrowid
        rows = [
            (sp, "geography", "P1", "3.1", 1, 4, "Coastal systems and landscapes", "3.1.3.4 Coastal management",
             "approved", "Outline coastal management"),
            (sp, "geography", "P1", "3.2", 2, 6, "Coastal systems and landscapes", None,
             "needs_review", "Analyse Figure 5 spits"),
            (cs, "computer-science", "C01", "1(a)", 1, 2, None, None, "needs_review", "Describe a CPU"),
        ]
        conn.executemany(
            """INSERT INTO questions (source_paper_id, subject, paper, series, question_number, sort_order,
                   marks, topic, subtopic, status, question_text, question_image, review_reason)
               VALUES (?, ?, ?, 'June 2022', ?, ?, ?, ?, ?, ?, ?, 'q.png', 'Topic: not tagged yet')""",
            rows,
        )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def logged_in(client):
    client.post("/login", data={"username": "noah", "password": PASSWORD})
    client.get("/subject/geography")
    return client


def ids(app, sql, *args):
    return [r[0] for r in connect(app.config["DATABASE_PATH"]).execute(sql, args)]


# ---- Login ---------------------------------------------------------------

@pytest.mark.parametrize("url", ["/", "/bank", "/builder", "/papers", "/review", "/settings",
                                 "/settings/backup", "/media/questions/q.png"])
def test_everything_requires_login(client, url):
    response = client.get(url)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_login_and_logout(client):
    assert client.post("/login", data={"username": "noah", "password": PASSWORD}).status_code == 302
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/").status_code == 302


def test_wrong_password_and_lockout_after_five_failures(client):
    for _ in range(5):
        r = client.post("/login", data={"username": "noah", "password": "wrong"})
        assert b"Wrong username or password" in r.data
    # Now even the right password is refused.
    r = client.post("/login", data={"username": "noah", "password": PASSWORD})
    assert b"Too many failed attempts" in r.data
    assert client.get("/").status_code == 302


def test_login_redirect_only_to_own_site(client):
    r = client.post("/login?next=https://evil.example", data={"username": "noah", "password": PASSWORD})
    assert r.headers["Location"] == "/"


def test_subject_switch_only_redirects_to_own_site(logged_in):
    assert logged_in.get("/subject/geography?next=https://evil.example").headers["Location"] == "/"
    assert logged_in.get("/subject/geography?next=//evil.example").headers["Location"] == "/"
    assert logged_in.get("/subject/geography?next=/review").headers["Location"] == "/review"


def test_csrf_token_required(app, tmp_path):
    app.config["WTF_CSRF_ENABLED"] = True
    r = app.test_client().post("/login", data={"username": "noah", "password": PASSWORD})
    assert r.status_code == 400


def test_security_headers(client):
    headers = client.get("/login").headers
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


# ---- Images --------------------------------------------------------------

def test_media_served_when_logged_in_but_not_outside_storage(logged_in):
    r = logged_in.get("/media/questions/q.png")
    assert r.status_code == 200 and r.data.startswith(b"\x89PNG")
    assert "private" in r.headers["Cache-Control"]
    assert logged_in.get("/media/questions/../secret.txt").status_code == 404
    assert logged_in.get("/media/other/q.png").status_code == 404


# ---- Question bank -------------------------------------------------------

def test_bank_shows_approved_only_by_default(logged_in):
    page = logged_in.get("/bank").get_data(as_text=True)
    assert "Paper 1, June 2022, Q03.1" in page
    assert "Paper 1, June 2022, Q03.2" not in page
    assert "1 question is waiting for review" in page


def test_bank_filters(logged_in):
    page = logged_in.get("/bank?include_review=1").get_data(as_text=True)
    assert "Q03.1" in page and "Q03.2" in page and "Q1(a)" not in page  # other subject hidden
    page = logged_in.get("/bank?include_review=1&marks=6").get_data(as_text=True)
    assert "Q03.2" in page and "Q03.1" not in page
    page = logged_in.get("/bank?subtopic=3.1.3.4+Coastal+management").get_data(as_text=True)
    assert "Q03.1" in page
    page = logged_in.get("/bank?include_review=1&text=spits").get_data(as_text=True)
    assert "Q03.2" in page and "Q03.1" not in page


# ---- Paper builder ---------------------------------------------------------

def test_build_save_edit_duplicate_delete(app, logged_in):
    q1, q2 = ids(app, "SELECT id FROM questions WHERE subject = 'geography' ORDER BY id")

    r = logged_in.post("/builder/add", json={"id": q1})
    assert r.get_json()["count"] == 1 and r.get_json()["marks"] == 4
    r = logged_in.post("/builder/add", json={"id": q2})
    assert r.get_json() | {"message": ""} == {"count": 2, "marks": 10, "time": "15 min", "message": ""}

    logged_in.post("/builder/reorder", json={"ids": [q2, q1]})
    logged_in.post("/builder/save", data={"title": "Coasts practice"})
    [pid] = ids(app, "SELECT id FROM built_papers")
    assert ids(app, "SELECT question_id FROM built_paper_questions ORDER BY position") == [q2, q1]
    assert "Coasts practice" in logged_in.get("/papers").get_data(as_text=True)

    # Saving again updates the same paper rather than creating a new one.
    logged_in.post("/builder/remove", json={"id": q1})
    logged_in.post("/builder/save", data={"title": "Coasts practice v2"})
    assert ids(app, "SELECT COUNT(*) FROM built_papers") == [1]
    assert ids(app, "SELECT question_id FROM built_paper_questions") == [q2]

    logged_in.post(f"/papers/{pid}/duplicate")
    assert ids(app, "SELECT title FROM built_papers ORDER BY id")[1] == "Coasts practice v2 (copy)"
    logged_in.post(f"/papers/{pid}/delete")
    assert ids(app, "SELECT COUNT(*) FROM built_papers") == [1]


def test_reorder_rejects_different_questions(app, logged_in):
    [q1] = ids(app, "SELECT id FROM questions WHERE question_number = '3.1'")
    logged_in.post("/builder/add", json={"id": q1})
    assert logged_in.post("/builder/reorder", json={"ids": [q1, 999]}).status_code == 400


def test_cannot_add_other_subjects_question(app, logged_in):
    [cs] = ids(app, "SELECT id FROM questions WHERE subject = 'computer-science'")
    assert logged_in.post("/builder/add", json={"id": cs}).status_code == 400


# ---- Review --------------------------------------------------------------

def test_review_approve_needs_subtopic(app, logged_in):
    [q2] = ids(app, "SELECT id FROM questions WHERE question_number = '3.2'")
    logged_in.post(f"/question/{q2}/update", data={"subtopic": "", "marks": "6", "action": "approve"})
    assert ids(app, "SELECT status FROM questions WHERE id = ?", q2) == ["needs_review"]

    logged_in.post(f"/question/{q2}/update",
                   data={"subtopic": "3.1.3.3 Coastal landscape development", "marks": "8", "action": "approve"})
    row = connect(app.config["DATABASE_PATH"]).execute("SELECT * FROM questions WHERE id = ?", (q2,)).fetchone()
    assert (row["status"], row["marks"], row["topic"]) == ("approved", 8, "Coastal systems and landscapes")


def test_review_rejects_subtopic_from_another_paper(app, logged_in):
    [q2] = ids(app, "SELECT id FROM questions WHERE question_number = '3.2'")
    logged_in.post(f"/question/{q2}/update", data={"subtopic": "3.2.2.4 Place studies", "marks": "6", "action": "approve"})
    assert ids(app, "SELECT status FROM questions WHERE id = ?", q2) == ["needs_review"]


def test_delete_question(app, logged_in):
    [q2] = ids(app, "SELECT id FROM questions WHERE question_number = '3.2'")
    logged_in.post(f"/question/{q2}/delete")
    assert ids(app, "SELECT COUNT(*) FROM questions WHERE id = ?", q2) == [0]


# ---- Settings ------------------------------------------------------------

def test_change_password(client, logged_in):
    logged_in.post("/settings/password", data={"current": PASSWORD, "new": "a brand new pass", "confirm": "a brand new pass"})
    logged_in.post("/logout")
    assert b"Wrong" in client.post("/login", data={"username": "noah", "password": PASSWORD}).data
    assert client.post("/login", data={"username": "noah", "password": "a brand new pass"}).status_code == 302


def test_change_password_needs_current_password(client, logged_in):
    r = logged_in.post("/settings/password", data={"current": "nope", "new": "x" * 12, "confirm": "x" * 12},
                       follow_redirects=True)
    assert b"current password was wrong" in r.data


def test_backup_download(logged_in):
    r = logged_in.get("/settings/backup")
    assert r.status_code == 200 and r.data.startswith(b"SQLite format 3")
    assert "attachment" in r.headers["Content-Disposition"]
