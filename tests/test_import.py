"""End-to-end test of the importer on the sample papers (no Claude calls)."""
import json

import pytest

import config
from app.db import connect, init_db
from scripts import import_papers
from tests import sample_papers


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point every data folder at a temporary directory."""
    papers = tmp_path / "papers"
    for name, value in {
        "PAPERS_DIR": papers,
        "GEOGRAPHY_PAPERS_DIR": papers / "geography",
        "CS_PAPERS_DIR": papers / "computer-science",
        "QUESTION_IMAGES_DIR": tmp_path / "storage/questions",
        "MARKSCHEME_IMAGES_DIR": tmp_path / "storage/markschemes",
        "INSERT_IMAGES_DIR": tmp_path / "storage/inserts",
        "DATABASE_PATH": tmp_path / "instance/test.db",
    }.items():
        monkeypatch.setattr(config, name, value)
    config.ensure_data_dirs()
    sample_papers.build_ocr_qp(config.CS_PAPERS_DIR / "CS_C01_2099_June_QP.pdf")
    sample_papers.build_ocr_ms(config.CS_PAPERS_DIR / "CS_C01_2099_June_MS.pdf")
    init_db(config.DATABASE_PATH)
    return tmp_path


def test_import_stores_questions_and_images(workspace):
    import_papers.main(["--no-tag"])
    conn = connect(config.DATABASE_PATH)
    rows = conn.execute("SELECT * FROM questions ORDER BY sort_order").fetchall()
    assert [r["question_number"] for r in rows] == ["1(a)", "1(b)(i)", "1(b)(ii)", "2(a)"]
    assert [r["marks"] for r in rows] == [1, 2, 3, 4]
    first = rows[0]
    assert first["series"] == "June 2099" and first["paper"] == "C01"
    assert (config.QUESTION_IMAGES_DIR / first["question_image"]).exists()
    assert (config.MARKSCHEME_IMAGES_DIR / first["markscheme_image"]).exists()
    assert json.loads(first["question_regions"])  # kept for vector PDF export
    # Untagged questions wait for review, with the reason recorded.
    assert {r["status"] for r in rows} == {"needs_review"}
    assert rows[0]["review_reason"] == import_papers.NOT_TAGGED


def test_reimport_skips_papers_already_done(workspace, capsys):
    import_papers.main(["--no-tag"])
    import_papers.main(["--no-tag"])
    assert "1 papers already imported, 0 new." in capsys.readouterr().out
    conn = connect(config.DATABASE_PATH)
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 4


def test_redo_replaces_a_paper(workspace):
    import_papers.main(["--no-tag"])
    import_papers.main(["--no-tag", "--redo", "CS_C01_2099_June"])
    conn = connect(config.DATABASE_PATH)
    assert conn.execute("SELECT COUNT(*) FROM source_papers").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 4


def test_tag_problems():
    from importer.tagging import TagResult
    assert import_papers.tag_problems(None) == [import_papers.NOT_TAGGED]
    assert import_papers.tag_problems(TagResult("t", "s", "high", "ok")) == []
    assert "medium" in import_papers.tag_problems(TagResult("t", "s", "medium", "spans two"))[0]


def test_series_and_slug():
    assert import_papers.describe_series("2022_June") == ("June 2022", 2022)
    assert import_papers.describe_series("Specimen") == ("Specimen", None)
    assert import_papers.slug("2(b)(ii)") == "2-b-ii"


def test_refresh_keeps_topics_status_and_saved_papers(workspace):
    import_papers.main(["--no-tag"])
    conn = connect(config.DATABASE_PATH)
    with conn:
        conn.execute("""UPDATE questions SET topic = 'Mine', subtopic = 'Mine too', marks = 7,
                            status = 'approved', question_regions = '[]' WHERE question_number = '1(a)'""")
        pid = conn.execute("INSERT INTO built_papers (subject, title) VALUES ('computer-science', 'T')").lastrowid
        conn.execute("INSERT INTO built_paper_questions SELECT ?, id, 0 FROM questions WHERE question_number = '1(a)'", (pid,))
    import_papers.main(["--refresh"])
    row = conn.execute("SELECT * FROM questions WHERE question_number = '1(a)'").fetchone()
    assert (row["topic"], row["subtopic"], row["marks"], row["status"]) == ("Mine", "Mine too", 7, "approved")
    assert json.loads(row["question_regions"])  # crop recomputed
    assert conn.execute("SELECT COUNT(*) FROM built_paper_questions").fetchone()[0] == 1
