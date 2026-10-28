"""Tests for the PDF export (Stage 5), using the sample papers."""
import unicodedata

import pymupdf
import pytest

import config
from app import create_app
from app.db import connect
from scripts import import_papers
from tests import sample_papers
from tests.test_import import workspace  # noqa: F401  (pytest fixture)


@pytest.fixture
def site(workspace):
    """Sample Geography (with insert) + CS papers imported, logged in."""
    geo = config.GEOGRAPHY_PAPERS_DIR
    sample_papers.build_aqa_qp(geo / "GEOG_P1_2099_June_QP.pdf")
    sample_papers.build_aqa_ms(geo / "GEOG_P1_2099_June_MS.pdf")
    sample_papers.build_aqa_insert(geo / "GEOG_P1_2099_June_INS.pdf")
    import_papers.main(["--no-tag"])
    app = create_app({"TESTING": True, "SECRET_KEY": "t", "WTF_CSRF_ENABLED": False,
                      "DATABASE_PATH": config.DATABASE_PATH})
    client = app.test_client()
    with client.session_transaction() as s:
        s["logged_in"] = True
    return client, connect(config.DATABASE_PATH)


def make_paper(conn, subject, title, numbers, series="June 2099"):
    with conn:
        pid = conn.execute("INSERT INTO built_papers (subject, title) VALUES (?, ?)", (subject, title)).lastrowid
        for pos, number in enumerate(numbers):
            conn.execute(
                """INSERT INTO built_paper_questions (built_paper_id, question_id, position)
                   SELECT ?, id, ? FROM questions WHERE subject = ? AND question_number = ? AND series = ?""",
                (pid, pos, subject, number, series))
    return pid


def pdf(client, url):
    r = client.get(url)
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert "attachment" in r.headers["Content-Disposition"]
    return pymupdf.open(stream=r.data)


def text_of(doc):
    # NFKC turns typographic ligatures like "ﬁ" back into "fi".
    # ...and collapse line breaks, so wrapped text like "Total\nmarks" still matches.
    return [" ".join(unicodedata.normalize("NFKC", page.get_text()).split()) for page in doc]


def test_paper_has_only_chosen_questions_in_original_style(site):
    client, conn = site
    pid = make_paper(conn, "geography", "Carbon practice", ["1.3", "1.1"])
    doc = pdf(client, f"/papers/{pid}/paper.pdf?cover=0")
    body = " ".join(text_of(doc))
    # Without the cover there are no added headings: page 1 starts with the first question.
    assert "Carbon practice" not in body and "Question 1" not in body
    # Original numbering and wording kept, in the order chosen.
    assert body.index("1 . 3") < body.index("1 . 1")
    assert "Assess the importance of the carbon cycle" in body
    # Questions not chosen are left out.
    assert "Analyse the changes shown in Figure 1" not in body and "2 . 1" not in body


def test_pages_have_the_board_frame_and_margin_note(site, tmp_path):
    client, conn = site
    pid = make_paper(conn, "geography", "Frame", ["1.1"])
    doc = pdf(client, f"/papers/{pid}/paper.pdf?cover=0")
    page = doc[0]
    frames = [d for d in page.get_drawings() if d["rect"].width > 400 and d["rect"].height > 600]
    assert frames, "the page frame should be drawn"
    assert "Do not write" in page.get_text()


def test_question_keeps_its_original_size_and_position(site):
    client, conn = site
    pid = make_paper(conn, "geography", "Size", ["1.1"])
    page = pdf(client, f"/papers/{pid}/paper.pdf?cover=0")[0]
    hits = page.search_for("Explain the concept of negative feedback")
    # In the sample paper this text starts at x=116 with 11pt font; it must not be rescaled.
    assert hits and abs(hits[0].x0 - 116) < 2 and 9 < hits[0].height < 16


def test_aqa_style_cover_made_from_the_selection(site):
    client, conn = site
    pid = make_paper(conn, "geography", "Carbon practice", ["1.3", "1.1"])
    doc = pdf(client, f"/papers/{pid}/paper.pdf")
    cover = text_of(doc)[0]
    for expected in ("Please write clearly in block capitals", "GEOGRAPHY", "Carbon practice",
                     "Time allowed: 20 minutes", "The total number of marks available for this paper is 13",
                     "For Examiner’s Use", "1.3 Jun 99", "1.1 Jun 99"):
        assert expected in cover, expected
    assert "the insert" not in cover  # these questions don't use one
    # The question pages are numbered from 2, after the cover.
    assert text_of(doc)[1].startswith("2") or " 2 " in f" {text_of(doc)[1]} "


def test_cover_lists_insert_when_needed(site):
    client, conn = site
    pid = make_paper(conn, "geography", "With figures", ["1.2"])
    assert "the insert (printed at the back" in text_of(pdf(client, f"/papers/{pid}/paper.pdf"))[0]


def test_ocr_style_cover_counts_pages(site):
    client, conn = site
    pid = make_paper(conn, "computer-science", "CS practice", ["1(a)", "2(a)"])
    doc = pdf(client, f"/papers/{pid}/paper.pdf")
    cover = text_of(doc)[0]
    assert "A Level Computer Science" in cover and "The total mark for this paper is 5" in cover
    assert f"This document has {doc.page_count} pages" in cover


def test_needed_insert_pages_are_included_at_the_back(site):
    client, conn = site
    pid = make_paper(conn, "geography", "Figure test", ["1.1", "1.2"])
    pages = text_of(pdf(client, f"/papers/{pid}/paper.pdf"))
    assert "Figure 1" in pages[-1] and "Changes in the water system" in pages[-1]


def test_missing_insert_is_reported_not_silently_dropped(site):
    client, conn = site
    (config.GEOGRAPHY_PAPERS_DIR / "GEOG_P1_2099_June_INS.pdf").unlink()
    with conn:
        conn.execute("UPDATE source_papers SET ins_filename = NULL")
        conn.execute("UPDATE questions SET insert_pages = '[]'")
    pid = make_paper(conn, "geography", "No insert", ["1.2"])
    pages = text_of(pdf(client, f"/papers/{pid}/paper.pdf"))
    assert "Missing insert pages" in pages[-1] and "Q01.2" in pages[-1]


def test_mark_scheme_pdf(site):
    client, conn = site
    pid = make_paper(conn, "geography", "MS test", ["1.2", "2.1"])
    body = " ".join(text_of(pdf(client, f"/papers/{pid}/markscheme.pdf")))
    assert body.index("AO3") < body.index("longshore drift")
    assert "Negative feedback" not in body  # 1.1 wasn't chosen


def test_cs_mark_scheme_is_landscape(site):
    client, conn = site
    pid = make_paper(conn, "computer-science", "CS", ["1(a)", "2(a)"])
    doc = pdf(client, f"/papers/{pid}/markscheme.pdf")
    assert doc[0].rect.width > doc[0].rect.height
    assert "Keyboard" in doc[0].get_text()


def test_export_falls_back_to_images_if_pdf_missing(site):
    client, conn = site
    pid = make_paper(conn, "computer-science", "Fallback", ["1(a)"])
    (config.CS_PAPERS_DIR / "CS_C01_2099_June_QP.pdf").unlink()
    doc = pdf(client, f"/papers/{pid}/paper.pdf?cover=0")
    assert doc[0].get_images()  # the PNG crop was used


def test_pdf_requires_login(site):
    client, conn = site
    pid = make_paper(conn, "computer-science", "Locked", ["1(a)"])
    with client.session_transaction() as s:
        s.clear()
    assert client.get(f"/papers/{pid}/paper.pdf").status_code == 302


def drawn_words(page, max_x):
    """Words in the left margin of a page (old labels sit under white
    patches, so both old and new appear here - we check the new ones exist)."""
    return [w[4] for w in page.get_text("words") if w[0] < max_x]


def test_renumbering_plan():
    from app.renumber import plan
    assert plan("computer-science", "3(d)(ii)", 2).label() == "2(a)(i)"
    assert plan("computer-science", "5", 4).label() == "4"
    assert plan("computer-science", "1(c)", 1).label() == "1(a)"
    assert plan("geography", "4.3", 1).label() == "01.1"
    assert plan("geography", "12.2", 10).label() == "10.1"


def test_ocr_questions_renumbered_in_order(site):
    client, conn = site
    # Picked out of order and from different questions: 2(a), 1(b)(ii), 1(a).
    pid = make_paper(conn, "computer-science", "Order", ["2(a)", "1(b)(ii)", "1(a)"])
    doc = pdf(client, f"/papers/{pid}/paper.pdf?cover=0")
    margin = [w for page in doc for w in drawn_words(page, 110)]
    for new in ("1", "2", "3"):
        assert new in margin, new
    assert "(i)" in margin  # 1(b)(ii) -> 2(a)(i)


def test_aqa_boxed_number_renumbered(site):
    client, conn = site
    pid = make_paper(conn, "geography", "Boxes", ["1.3", "2.1"])
    page = pdf(client, f"/papers/{pid}/paper.pdf?cover=0")[0]
    digits = [w for w in page.get_text("words") if w[0] < 110 and w[4] in "0123456789"]
    # "0 1 . 3" becomes "0 1 . 1": the new "1" is drawn where the "3" was.
    three = [w for w in digits if w[4] == "3"]
    assert three and any(w[4] == "1" and abs(w[0] - three[0][0]) < 3 for w in digits)
    # The little digit boxes are still there.
    boxes = [d for d in page.get_drawings() if d["rect"].x0 < 110 and d["rect"].height < 20]
    assert boxes


def test_mark_scheme_renumbered_to_match(site):
    client, conn = site
    pid = make_paper(conn, "computer-science", "MS order", ["2(a)", "1(a)"])
    doc = pdf(client, f"/papers/{pid}/markscheme.pdf")
    margin = [w for page in doc for w in drawn_words(page, 160)]
    assert "1" in margin and "2" in margin and "(a)" in margin


def test_cover_grid_uses_new_numbers(site):
    client, conn = site
    pid = make_paper(conn, "geography", "Grid", ["1.3", "2.1"])
    cover = text_of(pdf(client, f"/papers/{pid}/paper.pdf"))[0]
    assert "01.1 (01.3 Jun 99)" in cover and "02.1 (02.1 Jun 99)" in cover


def test_question_numbers_in_each_boards_style():
    from app.formatting import question_label
    assert question_label("geography", "3.1") == "03.1"
    assert question_label("geography", "12.4") == "12.4"
    assert question_label("geography", "5") == "05"
    assert question_label("computer-science", "2(b)(i)") == "2(b)(i)"


def test_question_images_drawn_from_the_pdf(site):
    client, conn = site
    q = conn.execute("SELECT id FROM questions WHERE question_number = '1.2'").fetchone()
    # Remove the stored PNGs: the image must now come from the PDF itself.
    for png in config.QUESTION_IMAGES_DIR.rglob("*.png"):
        png.unlink()
    r = client.get(f"/img/{q['id']}/question.png")
    assert r.status_code == 200 and r.data.startswith(b"\x89PNG")
    assert client.get(f"/img/{q['id']}/markscheme.png").status_code == 200
    assert client.get(f"/img/{q['id']}/secret.png").status_code == 404


def test_insert_page_image(site):
    client, conn = site
    sp = conn.execute("SELECT id FROM source_papers WHERE subject = 'geography'").fetchone()
    assert client.get(f"/img/insert/{sp['id']}/1.png").data.startswith(b"\x89PNG")
    assert client.get(f"/img/insert/{sp['id']}/99.png").status_code == 404


def test_images_need_login(site):
    client, conn = site
    with client.session_transaction() as s:
        s.clear()
    assert client.get("/img/1/question.png").status_code == 302
