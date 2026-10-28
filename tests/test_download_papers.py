"""Tests for the downloader's parsing logic (no network access needed)."""
import datetime as dt

import pymupdf

from scripts.download_papers import (
    expected_series,
    identify_manual_file,
    parse_aqa_filename,
    parse_aqa_page,
    parse_ocr_page,
    parse_ocr_series,
    verify_pdf,
)


# ---- AQA -----------------------------------------------------------------

def test_aqa_filenames_map_to_our_names():
    assert parse_aqa_filename("AQA-70371-QP-JUN22.PDF") == ("P1", "2022_June", "QP")
    assert parse_aqa_filename("AQA-70372-W-MS-NOV20.PDF") == ("P2", "2020_November", "MS")
    assert parse_aqa_filename("AQA-70372-QP-NOV21-CR.PDF") == ("P2", "2021_November", "QP")
    assert parse_aqa_filename("AQA-70371-SQP-CR.PDF") == ("P1", "Specimen", "QP")
    assert parse_aqa_filename("AQA-70372-SMS.PDF") == ("P2", "Specimen", "MS")
    assert parse_aqa_filename("AQA-70371-INS-JUN22-CR.PDF") == ("P1", "2022_June", "INS")


def test_aqa_skips_reports_and_modified_papers():
    for name in [
        "AQA-70371-INS-MQP36A3-JUN22-CR.PDF",  # modified insert
        "AQA-70371-WRE-JUN22.PDF",          # examiner report
        "AQA-70371-QP-MQP18A4-JUN22.PDF",   # modified large-print
        "AQA-70371-QP-JUN24-MQP36A3.PDF",   # modified, other order
        "AQA-70373-NEA-C.PDF",              # NEA
        "AQA-70371-AC.pdf",                 # answers and commentary
    ]:
        assert parse_aqa_filename(name) is None, name


def test_aqa_page_json_is_parsed():
    # Shape of the escaped JSON embedded in the real page.
    html = (
        r'{\"asset\":{\"originalFilename\":\"AQA-70371-QP-JUN22.PDF\",\"size\":1,'
        r'\"url\":\"https://cdn.sanity.io/files/x/green/abc.pdf\"}}'
        r'{\"asset\":{\"originalFilename\":\"AQA-70371-WRE-JUN22.PDF\",'
        r'\"url\":\"https://cdn.sanity.io/files/x/green/def.pdf\"}}'
    )
    [c] = parse_aqa_page(html)
    assert c.filename == "GEOG_P1_2022_June_QP.pdf"
    assert c.url == "https://filestore.aqa.org.uk/sample-papers-and-mark-schemes/2022/june/AQA-70371-QP-JUN22.PDF"
    assert c.manual_url == "https://cdn.sanity.io/files/x/green/abc.pdf"


# ---- OCR -----------------------------------------------------------------

OCR_HTML = """
<div id="specification-tab-1">
  <h3><span>Question papers, mark schemes and reports</span></h3>
  <div>
    <h4><span>2024 - June series</span></h4>
    <div><ul>
      <li><a href="/Images/1-question-paper-computer-systems.pdf">Question paper - Computer systems</a>
          <span class="unit-code">H446/01</span></li>
      <li><a href="/Images/2-mark-scheme-algorithms.pdf">Mark scheme - Algorithms and programming</a>
          <span class="unit-code">H446/02</span></li>
      <li><a href="/Images/3-examiners-report.pdf">Examiners' report - Computer systems</a>
          <span class="unit-code">H446/01</span></li>
      <li><a href="/Images/4-modified-papers.zip">Modified papers</a>
          <span class="unit-code">H446/01/02</span></li>
    </ul></div>
  </div>
  <h3><span>Sample assessment materials</span></h3>
  <div><ul>
    <li><a href="/Images/170852-unit-h446-1-computer-systems-sample-assessment-materials.pdf">Computer systems</a>
        <span class="unit-code">H446/01</span></li>
    <li><a href="/Images/170854-unit-h446-3-programming-project-sample-assessment-materials.pdf">Programming project</a>
        <span class="unit-code">H446/03</span></li>
  </ul></div>
</div>
<div id="specification-tab-2">
  <h4><span>2024 - June series</span></h4>
  <div><ul>
    <li><a href="/Images/9-question-paper-computer-principles.pdf">Question paper - Computer principles</a>
        <span class="unit-code">H046/01</span></li>
  </ul></div>
</div>
"""


def test_ocr_page_finds_only_h446_papers_and_mark_schemes():
    found = {c.filename: c.url for c in parse_ocr_page(OCR_HTML)}
    assert found == {
        "CS_C01_2024_June_QP.pdf": "https://www.ocr.org.uk/Images/1-question-paper-computer-systems.pdf",
        "CS_C02_2024_June_MS.pdf": "https://www.ocr.org.uk/Images/2-mark-scheme-algorithms.pdf",
        "CS_C01_Specimen_SAM.pdf": "https://www.ocr.org.uk/Images/170852-unit-h446-1-computer-systems-sample-assessment-materials.pdf",
    }


def test_ocr_series_headings():
    assert parse_ocr_series("2024 - June series") == "2024_June"
    assert parse_ocr_series("2021 - November series") == "2021_November"
    assert parse_ocr_series("Sample assessment materials") == "Specimen"
    assert parse_ocr_series("Question papers, mark schemes and reports") is None


# ---- Expected series -----------------------------------------------------

def test_expected_series_skips_cancelled_covid_summers():
    series = expected_series("geography", today=dt.date(2025, 10, 1))
    assert series == [
        "Specimen", "2018_June", "2019_June", "2020_November", "2021_November",
        "2022_June", "2023_June", "2024_June", "2025_June",
    ]
    # Before September, this year's June papers aren't expected yet.
    assert expected_series("geography", today=dt.date(2025, 7, 1))[-1] == "2024_June"


# ---- Official-paper check ------------------------------------------------

def make_pdf(path, cover_text):
    """Create a one-page PDF with the given text, standing in for a real cover."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), cover_text)
    doc.save(path)
    return path


def test_verify_accepts_genuine_covers(tmp_path):
    aqa_qp = make_pdf(tmp_path / "a.pdf", "7037/1 G/KL/Jun22/E5 A-level GEOGRAPHY Paper 1")
    assert verify_pdf(aqa_qp, "geography", "P1", "2022_June", "QP") is None
    # AQA's November 2020 paper is the cancelled June 2020 paper: still accepted.
    aqa_nov = make_pdf(tmp_path / "b.pdf", "7037/2 G/KL/Jun20/E4 A-level GEOGRAPHY Paper 2")
    assert verify_pdf(aqa_nov, "geography", "P2", "2020_November", "QP") is None
    ocr_ms = make_pdf(tmp_path / "c.pdf", "OCR Computer Science H446/02 Mark Scheme for June 2024")
    assert verify_pdf(ocr_ms, "computer-science", "C02", "2024_June", "MS") is None


def test_verify_rejects_wrong_or_fake_files(tmp_path):
    cover = make_pdf(tmp_path / "a.pdf", "7037/1 G/KL/Jun22/E5 A-level GEOGRAPHY Paper 1")
    assert "7037/2" in verify_pdf(cover, "geography", "P2", "2022_June", "QP")
    assert "2023" in verify_pdf(cover, "geography", "P1", "2023_June", "QP")
    assert "mark scheme" in verify_pdf(cover, "geography", "P1", "2022_June", "MS")
    assert "insert" in verify_pdf(cover, "geography", "P1", "2022_June", "INS")
    junk = make_pdf(tmp_path / "b.pdf", "Please log in to continue")
    assert verify_pdf(junk, "computer-science", "C01", "2024_June", "QP") is not None
    not_pdf = tmp_path / "c.pdf"
    not_pdf.write_text("<html>login</html>")
    assert verify_pdf(not_pdf, "computer-science", "C01", "2024_June", "QP") is not None


# ---- Manually downloaded files (papers/inbox) ----------------------------

def test_inbox_recognises_board_and_our_filenames():
    assert identify_manual_file("AQA-70371-QP-JUN24.PDF") == ("geography", "P1", "2024_June", "QP")
    assert identify_manual_file("AQA-70372-INS-JUN25-CR.PDF") == ("geography", "P2", "2025_June", "INS")
    assert identify_manual_file("CS_C01_2019_June_MS.pdf") == ("computer-science", "C01", "2019_June", "MS")
    assert identify_manual_file("GEOG_P2_2018_June_INS.pdf") == ("geography", "P2", "2018_June", "INS")
    assert identify_manual_file("GEOG_C01_2018_June_QP.pdf") is None  # subject/paper mismatch
    assert identify_manual_file("my paper.pdf") is None
