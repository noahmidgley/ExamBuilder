"""
Tests for the question-splitting logic (importer/splitter.py).

Part 1 uses the sample papers from sample_papers.py, so it always runs.
Part 2 checks your real downloaded papers if they're in papers/ (and is
skipped otherwise) - it catches a change that fixes one paper but breaks another.
"""
from pathlib import Path

import pymupdf
import pytest

from importer import splitter
from importer.splitter import Key, Line
from importer.topics import TOPICS
from tests import sample_papers

GEOG_P1_TOPICS = TOPICS["geography"]["P1"].keys()


# --------------------------------------------------------------------------
# Fixtures: build the sample papers once per test session
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def aqa(tmp_path_factory):
    folder = tmp_path_factory.mktemp("aqa")
    qp = pymupdf.open(sample_papers.build_aqa_qp(folder / "qp.pdf"))
    ms = pymupdf.open(sample_papers.build_aqa_ms(folder / "ms.pdf"))
    parts, errors = splitter.split_question_paper(qp, "aqa", GEOG_P1_TOPICS)
    assert errors == []
    return {"qp": qp, "ms": ms, "parts": {p.number: p for p in parts},
            "items": splitter.split_mark_scheme(ms, "aqa")}


@pytest.fixture(scope="session")
def ocr(tmp_path_factory):
    folder = tmp_path_factory.mktemp("ocr")
    qp = pymupdf.open(sample_papers.build_ocr_qp(folder / "qp.pdf"))
    ms = pymupdf.open(sample_papers.build_ocr_ms(folder / "ms.pdf"))
    parts, errors = splitter.split_question_paper(qp, "ocr")
    assert errors == []
    return {"qp": qp, "ms": ms, "parts": {p.number: p for p in parts},
            "items": splitter.split_mark_scheme(ms, "ocr")}


def pages(part):
    return [r[0] for r in part.regions]


# --------------------------------------------------------------------------
# AQA question paper
# --------------------------------------------------------------------------

def test_aqa_finds_every_question_with_its_marks(aqa):
    assert {n: p.marks for n, p in aqa["parts"].items()} == {"1.1": 4, "1.2": 6, "1.3": 9, "2.1": 4}
    assert all(p.problems == [] for p in aqa["parts"].values())


def test_aqa_includes_context_printed_above_the_label(aqa):
    assert "Figure 1 is in the insert" in aqa["parts"]["1.2"].text
    # ...and that context is NOT left on the end of the previous question.
    assert "Figure 1 is in the insert" not in aqa["parts"]["1.1"].text


def test_aqa_question_continues_over_page_break_but_skips_blank_page(aqa):
    # 1.3 starts on page 2, continues on page 3; page 4 is blank; 2.1 is on page 5.
    assert pages(aqa["parts"]["1.3"]) == [2, 3]
    assert pages(aqa["parts"]["2.1"]) == [5]


def test_aqa_lowercase_either_does_not_cut_a_question_short(aqa):
    assert "either way, use examples" in aqa["parts"]["1.3"].text


def test_aqa_section_heading_gives_topic_and_is_not_in_the_crop(aqa):
    parts = aqa["parts"]
    assert [parts[n].heading for n in ("1.1", "1.2", "1.3")] == ["Water and carbon cycles"] * 3
    assert parts["2.1"].heading == "Coastal systems and landscapes"
    assert "Coastal systems" not in parts["2.1"].text


def test_aqa_crops_leave_out_header_footer_and_margin(aqa):
    text = " ".join(p.text for p in aqa["parts"].values())
    for unwanted in ("Turn over", "Do not write", "(02)", "END OF QUESTIONS"):
        assert unwanted not in text


def test_aqa_cover_total(aqa):
    assert splitter.paper_total(aqa["qp"], "aqa") == 120


# --------------------------------------------------------------------------
# AQA mark scheme
# --------------------------------------------------------------------------

def test_aqa_mark_scheme_items_and_page_numbers_ignored(aqa):
    # The page number "2" in the footer must not become question 2.
    assert set(aqa["items"]) == {(1, "1", None), (1, "2", None), (1, "3", None), (2, "1", None)}
    # Headings above an item aren't part of its mark scheme.
    assert "Section B" not in aqa["items"][(2, "1", None)][1]


def test_aqa_mark_scheme_links_to_questions(aqa):
    for part in aqa["parts"].values():
        regions, text = splitter.match_mark_scheme(part, aqa["items"])
        assert regions, part.number
    _, text_13 = splitter.match_mark_scheme(aqa["parts"]["1.3"], aqa["items"])
    # 01.3 continues onto the next page but stops before "Section B".
    assert "Level 1 (1-3 marks)" in text_13 and "Section B" not in text_13


# --------------------------------------------------------------------------
# OCR question paper
# --------------------------------------------------------------------------

def test_ocr_finds_leaf_parts_with_marks(ocr):
    assert {n: p.marks for n, p in ocr["parts"].items()} == {
        "1(a)": 1, "1(b)(i)": 2, "1(b)(ii)": 3, "2(a)": 4,
    }


def test_ocr_code_with_brackets_is_not_marks(ocr):
    # "array stock[10]" is inside 1(b)'s stem; it must not add 10 marks.
    assert sum(p.marks for p in ocr["parts"].values()) == 10


def test_ocr_parts_include_their_shared_stems(ocr):
    part = ocr["parts"]["1(b)(i)"]
    assert "A shop sells computer parts" in part.text          # stem of 1
    assert "array stock[10]" in part.text                     # stem of 1(b)
    assert len(part.context_regions) == 2


def test_ocr_marks_low_on_continuation_page_are_found(ocr):
    part = ocr["parts"]["1(b)(ii)"]
    assert pages(part) == [1, 2]
    assert part.marks == 3


def test_ocr_label_near_top_of_page_is_found(ocr):
    assert "2(a)" in ocr["parts"]


# --------------------------------------------------------------------------
# OCR mark scheme
# --------------------------------------------------------------------------

def test_ocr_mark_scheme_fills_in_carried_over_numbers(ocr):
    # The row "1 (ii)" means 1(b)(ii).
    assert set(ocr["items"]) == {(1, "a", None), (1, "b", "i"), (1, "b", "ii"), (2, "a", None)}


def test_ocr_compact_centred_labels(tmp_path):
    ms = pymupdf.open(sample_papers.build_ocr_ms(tmp_path / "ms.pdf", compact=True))
    items = splitter.split_mark_scheme(ms, "ocr")
    assert set(items) == {(1, "a", None), (1, "b", "i"), (1, "b", "ii"), (2, "a", None)}
    # Each item starts at its cell's top border, so its own answer is included
    # even though the label is printed halfway down the cell.
    assert "Linear search" in items[(1, "b", "ii")][1]
    assert "Linear search" not in items[(1, "b", "i")][1]


def test_ocr_every_part_matches_a_mark_scheme(ocr):
    for part in ocr["parts"].values():
        regions, _ = splitter.match_mark_scheme(part, ocr["items"])
        assert regions and part.problems == [], part.number


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def line(text, x0=100, x1=540):
    return Line(0, x0, 100, x1, 112, text, False)


def test_answer_line_detection():
    assert splitter.is_answer_line(line("_" * 60))
    assert splitter.is_answer_line(line("Extra space  ________"))
    assert splitter.is_answer_line(line("Type 1  " + "." * 60))
    assert not splitter.is_answer_line(line("Explain the purpose of a hydrograph."))


def test_marks_must_be_right_aligned_at_end_of_line():
    on = lambda t, x1=545: splitter.marks_on(line(t, x1=x1), splitter.OCR_MARKS_RE, 595)
    assert on("." * 60 + " [3]") == [3]
    assert on("array stock[10]", x1=200) == []            # code, not right-aligned
    assert on("linkedList[2].data returns green") == []   # not at end of line
    aqa = splitter.marks_on(line("[20 marks]"), splitter.AQA_MARKS_RE, 595)
    assert aqa == [20]


def test_barrier_headings_are_case_sensitive():
    assert splitter.BARRIER_RE.search("Section B")
    assert splitter.BARRIER_RE.search("Question 4")
    assert splitter.BARRIER_RE.search("END OF QUESTIONS")
    assert not splitter.BARRIER_RE.search("either encryption or hashing.")


def test_key_display_and_ancestors():
    assert Key(3, "4").display("aqa") == "3.4"
    assert Key(2, "b", "ii").display("ocr") == "2(b)(ii)"
    assert [k.as_tuple() for k in Key(2, "b", "ii").ancestors()] == [(2, None, None), (2, "b", None)]
    assert Key(2).is_ancestor_of((2, "b", "ii"))
    assert not Key(2, "b", "i").is_ancestor_of((2, "b", "ii"))   # siblings aren't ancestors


def test_figures_map_to_insert_pages():
    part = splitter.Part(Key(2, "2"), "2.2", 0, [], text="Figures 3a and 3b are in the insert. Using Figure 3 ...")
    pages_ = splitter.figures_for(part, {"3a": [2], "3b": [3], "4": [4]})
    assert pages_ == [2, 3] and part.problems == []
    missing = splitter.Part(Key(1, "1"), "1.1", 0, [], text="Figure 9 is in the insert.")
    assert splitter.figures_for(missing, {"1": [0]}) == []
    assert "Figure 9" in missing.problems[0]


# --------------------------------------------------------------------------
# Part 2: real papers (skipped if they haven't been downloaded)
# --------------------------------------------------------------------------

PAPERS = Path(__file__).resolve().parent.parent / "papers"
REAL = sorted(PAPERS.glob("*/*_QP.pdf"))


@pytest.mark.skipif(not REAL, reason="no papers downloaded")
@pytest.mark.parametrize("qp_path", REAL, ids=lambda p: p.name[:-7])
def test_real_paper_totals(qp_path):
    """Every CS paper's parts add up to the cover total; Geography papers add
    up to the full total including every optional question."""
    name = qp_path.name
    board = "aqa" if name.startswith("GEOG") else "ocr"
    subject = "geography" if board == "aqa" else "computer-science"
    paper = name.split("_")[1]
    qp = pymupdf.open(qp_path)
    parts, errors = splitter.split_question_paper(qp, board, TOPICS[subject][paper].keys())
    total = sum(p.marks or 0 for p in parts)
    expected = {"P1": 240, "P2": 216}.get(paper) or splitter.paper_total(qp, board)
    assert errors == [] and total == expected


# --------------------------------------------------------------------------
# Pictures that belong to the question below them
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def figures(tmp_path_factory):
    path = sample_papers.build_aqa_figures_qp(tmp_path_factory.mktemp("fig") / "qp.pdf")
    parts, _ = splitter.split_question_paper(pymupdf.open(path), "aqa", GEOG_P1_TOPICS)
    return {p.number: p for p in parts}


def test_photo_with_text_on_it_stays_with_its_question(figures):
    assert "Figure 2 is a photograph of a spit" in figures["1.2"].text
    assert "Figure 2" not in figures["1.1"].text
    # 1.2 starts above the caption, not at the "N" printed on the photo.
    assert figures["1.2"].regions[0][2] < 360


def test_figure_on_previous_page_moves_with_its_question(figures):
    # The caption and photo for 1.3 are at the bottom of page 2; its number is on page 3.
    assert pages(figures["1.3"]) == [2, 3]
    assert "Figure 3 is a photograph of a salt marsh" in figures["1.3"].text
    assert "salt marsh" not in figures["1.2"].text
    assert {n: p.marks for n, p in figures.items()} == {"1.1": 4, "1.2": 6, "1.3": 9}


def real_parts(name, subject, paper):
    path = PAPERS / ("geography" if subject == "geography" else "computer-science") / f"{name}_QP.pdf"
    if not path.exists():
        pytest.skip(f"{name} not downloaded")
    parts, _ = splitter.split_question_paper(pymupdf.open(path), "aqa", TOPICS[subject][paper].keys())
    return {p.number: p for p in parts}


def test_real_figure_after_answer_space_belongs_to_next_question():
    parts = real_parts("GEOG_P1_2023_June", "geography", "P1")
    assert parts["2.2"].text.startswith("Figure 3 shows the changing size")
    assert "Figure 3" not in parts["2.1"].text


def test_real_heading_without_the_still_matches_topic():
    parts = real_parts("GEOG_P2_2021_November", "geography", "P2")
    assert parts["4.1"].heading == "Population and the environment"


def test_figure_numbers():
    assert splitter.figure_numbers("Using Figures 8, 9 and 10, compare") == {"8", "9", "10"}
    assert splitter.figure_numbers("Figure 3a and Figure 3b") == {"3"}
    assert splitter.figure_numbers("Outline weathering processes") == set()



# --------------------------------------------------------------------------
# Crops never cut through text
# --------------------------------------------------------------------------

def test_touching_slices_are_merged():
    regions = [[3, 20, 60, 575, 92.3], [3, 20, 92.3, 575, 218], [4, 20, 60, 575, 300]]
    assert splitter.merge_touching(regions) == [[3, 20, 60, 575, 218], [4, 20, 60, 575, 300]]


def test_safe_cut_moves_into_the_gap_between_lines():
    # Two lines whose text boxes overlap (62-77 and 75-90), baselines 74 and 87, 11pt.
    upper = Line(0, 50, 62, 70, 77, "(d)", True, 74, 11)
    lower = Line(0, 50, 75, 300, 90, "(i) A sorting algorithm", True, 87, 11)
    y = splitter.safe_cut(75, 0, [upper, lower])
    assert upper.ink_bottom <= y <= lower.ink_top


def test_safe_cut_ignores_watermarks_and_page_numbers():
    watermark = Line(0, 140, 267, 160, 575, "SPECIMEN", False, 570, 30)
    page_no = Line(0, 290, 43, 300, 58, "20", True, 54, 11)
    assert splitter.safe_cut(300, 0, [watermark]) == 300
    assert splitter.safe_cut(50, 0, [page_no]) == 50


def test_real_first_part_does_not_take_the_shared_introduction():
    path = PAPERS / "computer-science" / "CS_C01_2022_June_QP.pdf"
    if not path.exists():
        pytest.skip("paper not downloaded")
    parts = {p.number: p for p in splitter.split_question_paper(pymupdf.open(path), "ocr")[0]}
    # 1(c)(ii) must show all of (c)'s introduction, including the Fig. 1 code.
    text = parts["1(c)(ii)"].text
    assert "Depending on the amount" in text and "BRP bonus" in text
