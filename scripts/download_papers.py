"""
Stage 2: download past question papers (QP) and mark schemes (MS).

Run from the project folder:
    .venv/bin/python -m scripts.download_papers                 # both subjects
    .venv/bin/python -m scripts.download_papers --subject cs    # just Computer Science
    .venv/bin/python -m scripts.download_papers --dry-run       # list what it would do

How it works
------------
1. Fetch the exam board's public resources page for the qualification.
2. Parse it to find the QP, MS and (Geography) insert links (ignoring examiner reports,
   modified large-print papers, etc.).
3. Download each file we don't already have, to a consistent filename.
4. Write papers/download_report.md listing what was downloaded, what was
   skipped, and which expected series couldn't be found (so you can get
   them manually).

Politeness rules (the exam boards' servers aren't ours):
- Check robots.txt before every request and skip anything it disallows.
- Wait at least 3 seconds between requests.
- Identify ourselves with a clear User-Agent.
- Only download from an allow-list of hosts, and only files that come back
  as real PDFs without logging in. Anything that redirects to a login page
  (or anywhere that isn't a PDF) is reported, not downloaded.

Making sure files are official:
- Links only ever come from the boards' own pages (aqa.org.uk / ocr.org.uk).
- Newer AQA papers (June 2024 onwards) are only on AQA's CDN (cdn.sanity.io).
  Its robots.txt forbids automated PDF downloads, so we don't fetch them;
  the report gives you AQA's direct links to download them in your browser.
- Every downloaded file is opened and its cover checked for the right exam
  code, year and document type (verify_pdf) before it's kept.

Files you download yourself:
- Drop them in papers/inbox/, either with the exam board's own filename
  (e.g. AQA-70371-QP-JUN24.PDF) or already named our way
  (e.g. CS_C01_2019_June_QP.pdf). Each run checks them with verify_pdf and
  moves them into place with the right name.
"""
import argparse
import datetime as dt
import re
import sys
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pymupdf
import requests
from bs4 import BeautifulSoup

# Allow "python -m scripts.download_papers" to find config.py in the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

USER_AGENT = (
    "ExamBuilder/1.0 (personal revision tool; downloads public past papers; "
    "max 1 request per 3 seconds)"
)
MIN_DELAY_SECONDS = 3.0

AQA_PAGE = "https://www.aqa.org.uk/subjects/geography/a-level/geography-7037/assessment-resources"
AQA_FILESTORE = "https://filestore.aqa.org.uk"
OCR_PAGE = "https://www.ocr.org.uk/qualifications/as-and-a-level/computer-science-h046-h446-from-2015/assessment/"

# We only ever request URLs on these hosts.
ALLOWED_HOSTS = {"www.aqa.org.uk", "filestore.aqa.org.uk", "www.ocr.org.uk"}

# Drop manually downloaded files here (see adopt_inbox).
INBOX_DIR = config.PAPERS_DIR / "inbox"

# Every exam series we'd expect to exist for each spec. Used for the
# "couldn't find" part of the report. Summer 2020/2021 were cancelled (COVID),
# and November series ran in 2020 and 2021 instead.
FIRST_JUNE = {"geography": 2018, "computer-science": 2017}
PAPERS = {"geography": ["P1", "P2"], "computer-science": ["C01", "C02"]}
PREFIX = {"geography": "GEOG", "computer-science": "CS"}
SOURCE_PAGE = {"geography": AQA_PAGE, "computer-science": OCR_PAGE}


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    """One file we want: where it comes from and what we'll call it."""

    subject: str          # 'geography' or 'computer-science'
    paper: str            # 'P1', 'P2', 'C01', 'C02'
    series: str           # '2022_June', '2020_November' or 'Specimen'
    kind: str             # 'QP', 'MS', 'INS' (Geography insert with the figures),
                          # or 'SAM' (OCR specimen: QP + MS in one PDF)
    url: str
    # AQA's CDN link for the same file. We never fetch it (robots.txt), but
    # the report shows it so you can download the file in your browser.
    manual_url: str | None = None

    @property
    def filename(self):
        """e.g. GEOG_P1_2022_June_QP.pdf"""
        return f"{PREFIX[self.subject]}_{self.paper}_{self.series}_{self.kind}.pdf"


@dataclass
class Report:
    downloaded: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed: list = field(default_factory=list)      # (filename, reason, url)
    missing: list = field(default_factory=list)     # (filename, reason)


def series_name(year, month):
    """Consistent series label used in filenames: '2022_June'."""
    return f"{year}_{month.capitalize()}"


def output_dir(subject):
    return config.GEOGRAPHY_PAPERS_DIR if subject == "geography" else config.CS_PAPERS_DIR


# --------------------------------------------------------------------------
# Polite HTTP client
# --------------------------------------------------------------------------

class PoliteClient:
    """Wraps requests.Session to enforce robots.txt, a delay and an allow-list."""

    def __init__(self, allowed_hosts):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.allowed_hosts = set(allowed_hosts)
        self._robots = {}          # host -> RobotFileParser (cached)
        self._last_request = 0.0

    def _wait(self):
        """Sleep so that requests are at least MIN_DELAY_SECONDS apart."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_DELAY_SECONDS:
            time.sleep(MIN_DELAY_SECONDS - elapsed)
        self._last_request = time.monotonic()

    def _robots_allows(self, url):
        parts = urlparse(url)
        host = parts.netloc
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            self._wait()
            try:
                resp = self.session.get(robots_url, timeout=30)
                # No robots.txt (404) means everything is allowed.
                parser.parse(resp.text.splitlines() if resp.ok else [])
            except requests.RequestException:
                # If we can't read robots.txt, be cautious and allow nothing.
                parser.parse(["User-agent: *", "Disallow: /"])
            self._robots[host] = parser
        return self._robots[host].can_fetch(USER_AGENT, url)

    def check(self, url):
        """Return None if we may fetch url, else a reason string."""
        host = urlparse(url).netloc
        if host not in self.allowed_hosts:
            return f"host {host} is not on the allow-list"
        if not self._robots_allows(url):
            return "disallowed by robots.txt"
        return None

    def get(self, url, **kwargs):
        reason = self.check(url)
        if reason:
            raise PermissionError(reason)
        self._wait()
        return self.session.get(url, timeout=60, **kwargs)


# --------------------------------------------------------------------------
# AQA (Geography 7037)
# --------------------------------------------------------------------------

# AQA filenames, e.g. AQA-70371-QP-JUN22.PDF, AQA-70372-W-MS-NOV20.PDF,
# AQA-70371-INS-JUN22-CR.PDF (insert), AQA-70371-SQP-CR.PDF (specimen).
# The paper number is the 5th digit (1 or 2).
AQA_FILE_RE = re.compile(
    r"^AQA-7037(?P<paper>[12])-(?P<kind>QP|MS|W-MS|INS|SQP|SMS)(?P<rest>[-A-Z0-9]*)\.PDF$",
    re.IGNORECASE,
)
MONTHS = {"JUN": "June", "NOV": "November"}


def parse_aqa_filename(original_filename):
    """Turn an AQA filename into (paper, series, kind), or None to skip it.

    >>> parse_aqa_filename("AQA-70371-QP-JUN22.PDF")
    ('P1', '2022_June', 'QP')
    """
    m = AQA_FILE_RE.match(original_filename)
    if not m:
        return None  # examiner report, NEA, etc.
    rest = m.group("rest").upper()
    if "MQP" in rest:
        return None  # modified (enlarged/large-print) version of a paper
    kind = {"QP": "QP", "SQP": "QP", "MS": "MS", "W-MS": "MS", "SMS": "MS", "INS": "INS"}[
        m.group("kind").upper()
    ]
    if m.group("kind").upper() in ("SQP", "SMS"):
        series = "Specimen"
    else:
        s = re.search(r"(JUN|NOV)(\d{2})", rest)
        if not s:
            return None
        series = series_name(2000 + int(s.group(2)), MONTHS[s.group(1)])
    return f"P{m.group('paper')}", series, kind


def aqa_filestore_url(original_filename, series):
    """Where filestore.aqa.org.uk keeps a file (works for 2023 and earlier)."""
    if series == "Specimen":
        return f"{AQA_FILESTORE}/resources/geography/{original_filename}"
    year, month = series.split("_")
    return f"{AQA_FILESTORE}/sample-papers-and-mark-schemes/{year}/{month.lower()}/{original_filename}"


def parse_aqa_page(html):
    """Find QP, MS and insert files in the AQA subject page.

    The page is built with Next.js, which embeds its data as JSON inside
    <script> tags (with the quotes escaped). Each file looks like:
        "originalFilename":"AQA-70371-QP-JUN22.PDF", ... "url":"https://cdn.sanity.io/...pdf"
    """
    text = html.replace('\\"', '"')
    # [^{}]*? keeps the match inside a single JSON object, so a filename
    # can't get paired with the URL of a different file.
    pattern = re.compile(
        r'"originalFilename":"(?P<name>[^"]+)"[^{}]*?"url":"(?P<url>https://cdn\.sanity\.io/[^"]+)"'
    )
    candidates = {}
    for m in pattern.finditer(text):
        parsed = parse_aqa_filename(m.group("name"))
        if not parsed:
            continue
        paper, series, kind = parsed
        c = Candidate(
            subject="geography", paper=paper, series=series, kind=kind,
            url=aqa_filestore_url(m.group("name"), series),
            manual_url=m.group("url"),
        )
        # The same file can appear more than once on the page; keep one.
        candidates.setdefault(c.filename, c)
    return list(candidates.values())


# --------------------------------------------------------------------------
# OCR (Computer Science H446)
# --------------------------------------------------------------------------

def parse_ocr_series(heading):
    """'2024 - June series' -> '2024_June'; 'Sample assessment materials' -> 'Specimen'."""
    if "sample assessment" in heading.lower():
        return "Specimen"
    m = re.search(r"(\d{4})\s*-\s*(\w+)\s+series", heading, re.IGNORECASE)
    return series_name(int(m.group(1)), m.group(2)) if m else None


def parse_ocr_component(unit_code, href):
    """'H446/01' -> 'C01'. Falls back to the filename for specimen links."""
    m = re.fullmatch(r"H446/0?([12])", unit_code.strip())
    if not m:
        m = re.search(r"unit-h446-([12])-", href)
    return f"C0{m.group(1)}" if m else None


def parse_ocr_page(html, base_url=OCR_PAGE):
    """Find H446 Component 01/02 QPs, MSs and specimen materials."""
    soup = BeautifulSoup(html, "html.parser")
    # The page has two tabs: A Level (H446) and AS Level (H046). Only use A Level.
    tab = soup.find(id="specification-tab-1")
    if tab is None:
        return []

    candidates = {}
    # Each exam series is an <h4> heading followed by a <div> of links. The
    # specimen section is an <h3>; other <h3>s have no series and are skipped.
    for heading in tab.find_all(["h3", "h4"]):
        series = parse_ocr_series(heading.get_text(" ", strip=True))
        content = heading.find_next_sibling("div")
        if not series or content is None:
            continue
        for item in content.find_all("li"):
            link = item.find("a", href=True)
            if not link or not link["href"].lower().endswith(".pdf"):
                continue
            label = link.get_text(" ", strip=True).lower()
            unit = item.find(class_="unit-code")
            paper = parse_ocr_component(unit.get_text() if unit else "", link["href"])
            if not paper:
                continue  # H446/03 programming project, or AS-level
            if series == "Specimen":
                kind = "SAM"
            elif label.startswith("question paper"):
                kind = "QP"
            elif label.startswith("mark scheme"):
                kind = "MS"
            else:
                continue  # examiner reports, etc.
            c = Candidate("computer-science", paper, series, kind, urljoin(base_url, link["href"]))
            candidates.setdefault(c.filename, c)
    return list(candidates.values())


def split_specimen(sam_path, qp_path, ms_path):
    """OCR specimen PDFs contain the QP followed by the mark scheme.

    The mark scheme starts on the first page containing 'SAMPLE MARK SCHEME'.
    Returns True if it found the split point.
    """
    with pymupdf.open(sam_path) as doc:
        split_at = next(
            (i for i, page in enumerate(doc) if i > 0 and "SAMPLE MARK SCHEME" in page.get_text().upper()),
            None,
        )
        if split_at is None:
            return False
        for path, first, last in ((qp_path, 0, split_at - 1), (ms_path, split_at, doc.page_count - 1)):
            part = pymupdf.open()
            part.insert_pdf(doc, from_page=first, to_page=last)
            part.save(path)
            part.close()
    return True


# --------------------------------------------------------------------------
# Checking a download is the genuine paper
# --------------------------------------------------------------------------

def verify_pdf(path, subject, paper, series, kind):
    """Check a PDF really is the official paper we expect.

    Opens the file and reads the text on its first few pages (the cover),
    which always states the exam code, the year and what the document is.
    Returns None if everything matches, otherwise a reason string.

    'kind' is QP, MS or INS. Note AQA's "November 2020/2021" papers are the
    cancelled June papers sat in the autumn, so their covers say 'Jun20' -
    that's why we check the year but not the month.
    """
    try:
        with pymupdf.open(path) as doc:
            cover = " ".join(page.get_text() for page in doc.pages(0, min(3, doc.page_count)))
    except Exception:
        return "file is damaged or isn't a real PDF"
    text = " ".join(cover.split()).upper()  # normalise spaces and case
    number = paper[-1]  # 'P1' -> '1', 'C02' -> '2'

    if subject == "geography":
        if "AQA" not in text and "GEOGRAPHY" not in text:
            return "cover doesn't mention AQA Geography"
        # Real papers print '7037/1'. AQA's specimens only say 'Paper 1'.
        if f"7037/{number}" not in text and not (series == "Specimen" and f"PAPER {number}" in text):
            return f"cover doesn't show the exam code 7037/{number}"
        if kind == "INS" and "INSERT" not in text:
            return "cover doesn't say it's an insert"
    else:
        if "OCR" not in text and "OXFORD CAMBRIDGE AND RSA" not in text:
            return "cover doesn't mention OCR"
        if f"H446/0{number}" not in text:
            return f"cover doesn't show the exam code H446/0{number}"

    if kind == "MS" and "MARK SCHEME" not in text:
        return "cover doesn't say it's a mark scheme"

    if series == "Specimen":
        if "SPECIMEN" not in text and "SAMPLE" not in text:
            return "cover doesn't say it's specimen/sample material"
    else:
        year = int(series[:4])
        # Accept '2022' or AQA's short form 'Jun22' / 'Nov 21'.
        if not re.search(rf"\b{year}\b|(JUN|NOV)\s?{year % 100:02d}\b", text):
            return f"cover doesn't mention {year}"
    return None


# --------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------

def download_pdf(client, url, dest, verify):
    """Download url to dest. Returns None on success, else a reason string.

    verify(path) is called on the downloaded file before it's kept; it
    returns None if the file is genuine, or a reason string if not.

    Writes to a temporary '.part' file first and renames it at the end, so an
    interrupted download never leaves a half-written PDF behind (which the
    'skip existing' check would otherwise treat as done).
    """
    try:
        resp = client.get(url, allow_redirects=False, stream=True)
    except PermissionError as e:
        return str(e)
    except requests.RequestException as e:
        return f"network error: {e}"

    with resp:
        if resp.is_redirect:
            # Public files are served directly. A redirect usually means a
            # login page or 'not found' page, so we stop and report it.
            return f"redirected to {resp.headers.get('Location', '?')} (not public, or moved)"
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(64 * 1024):
                f.write(chunk)

    # Check it's really a PDF (every PDF file starts with the bytes '%PDF').
    with open(tmp, "rb") as f:
        if f.read(4) != b"%PDF":
            tmp.unlink()
            return "response was not a PDF (probably a login or error page)"
    problem = verify(tmp)
    if problem:
        tmp.unlink()
        return f"failed the official-paper check: {problem}"
    tmp.rename(dest)
    return None


def already_have(path):
    if not path.exists():
        return False
    with open(path, "rb") as f:
        return f.read(4) == b"%PDF"


def process(client, candidate, report, dry_run):
    folder = output_dir(candidate.subject)
    c = candidate

    def verify(path):
        # Specimens are checked as question papers: the combined file starts with the QP.
        return verify_pdf(path, c.subject, c.paper, c.series, "QP" if c.kind == "SAM" else c.kind)

    # OCR specimen: one PDF that we split into a QP and an MS.
    if candidate.kind == "SAM":
        base = f"{PREFIX[candidate.subject]}_{candidate.paper}_Specimen"
        qp, ms = folder / f"{base}_QP.pdf", folder / f"{base}_MS.pdf"
        if already_have(qp) and already_have(ms):
            report.skipped += [qp.name, ms.name]
            return
        if dry_run:
            print(f"  would download {candidate.url} -> {qp.name} + {ms.name}")
            return
        combined = folder / f"{base}_SAM.pdf"
        error = download_pdf(client, candidate.url, combined, verify)
        if error:
            report.failed.append((f"{qp.name} / {ms.name}", error, candidate.url))
            return
        if split_specimen(combined, qp, ms):
            combined.unlink()
            report.downloaded += [qp.name, ms.name]
            print(f"  downloaded {qp.name} + {ms.name} (split from specimen)")
        else:
            report.failed.append((f"{qp.name} / {ms.name}",
                                  f"couldn't find the mark scheme start; combined file kept as {combined.name}",
                                  candidate.url))
        return

    dest = folder / candidate.filename
    if already_have(dest):
        report.skipped.append(dest.name)
        return
    if dry_run:
        print(f"  would download {candidate.url} -> {dest.name}")
        return

    error = download_pdf(client, candidate.url, dest, verify)
    if error and candidate.manual_url and error.startswith("redirected"):
        # Not on filestore.aqa.org.uk: AQA only hosts it on its CDN.
        report.failed.append((dest.name,
                              "only on AQA's CDN, which doesn't allow automated downloads. "
                              "Open the link, save the file into papers/inbox/ and re-run",
                              candidate.manual_url))
    elif error:
        report.failed.append((dest.name, error, candidate.url))
    else:
        report.downloaded.append(dest.name)
        print(f"  downloaded {dest.name}")


OUR_NAME_RE = re.compile(
    r"^(?P<prefix>GEOG|CS)_(?P<paper>P[12]|C0[12])_(?P<series>\d{4}_(?:June|November)|Specimen)_(?P<kind>QP|MS|INS)\.pdf$"
)


def identify_manual_file(name):
    """Work out (subject, paper, series, kind) from a manually downloaded filename."""
    parsed = parse_aqa_filename(name)
    if parsed:
        return ("geography", *parsed)
    m = OUR_NAME_RE.match(name)
    if m:
        subject = "geography" if m.group("prefix") == "GEOG" else "computer-science"
        if (subject == "geography") != m.group("paper").startswith("P"):
            return None  # e.g. GEOG_C01_... makes no sense
        return subject, m.group("paper"), m.group("series"), m.group("kind")
    return None


def adopt_inbox(report):
    """Check files in papers/inbox/ and move genuine ones into place."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INBOX_DIR.iterdir()):
        if path.suffix.lower() != ".pdf":
            continue
        info = identify_manual_file(path.name)
        if info is None:
            report.failed.append((f"inbox/{path.name}", "filename not recognised, so left in the inbox", ""))
            continue
        subject, paper, series, kind = info
        dest = output_dir(subject) / f"{PREFIX[subject]}_{paper}_{series}_{kind}.pdf"
        if already_have(dest):
            report.failed.append((f"inbox/{path.name}", f"{dest.name} already exists, so left in the inbox", ""))
            continue
        problem = verify_pdf(path, subject, paper, series, kind)
        if problem:
            report.failed.append((f"inbox/{path.name}", f"failed the official-paper check: {problem}", ""))
            continue
        path.rename(dest)
        report.downloaded.append(f"{dest.name} (from inbox/{path.name})")
        print(f"  moved inbox/{path.name} -> {dest.name}")


def expected_series(subject, today=None):
    """Every series that should exist for the spec, up to the latest June."""
    today = today or dt.date.today()
    # June results come out in August, so only expect this year's June papers
    # from September onwards (and even then the boards may not have published).
    last_june = today.year if today.month >= 9 else today.year - 1
    series = ["Specimen"]
    for year in range(FIRST_JUNE[subject], last_june + 1):
        if year in (2020, 2021):
            series.append(series_name(year, "November"))
        else:
            series.append(series_name(year, "June"))
    return series


def find_missing(subject, report):
    """List expected QP/MS/insert files that aren't on disk after this run."""
    folder = output_dir(subject)
    for series in expected_series(subject):
        kinds = ["QP", "MS"]
        if subject == "geography" and series != "Specimen":
            kinds.append("INS")  # figures live in a separate insert booklet
        for paper in PAPERS[subject]:
            for kind in kinds:
                name = f"{PREFIX[subject]}_{paper}_{series}_{kind}.pdf"
                if already_have(folder / name):
                    continue
                if any(name in f[0] for f in report.failed):
                    continue  # already explained in the 'failed' list
                report.missing.append((name, f"not listed publicly on {SOURCE_PAGE[subject]}"))


def write_report(report, path):
    lines = [
        "# Paper download report",
        "",
        f"Run at {dt.datetime.now():%Y-%m-%d %H:%M}.",
        "",
        f"## Downloaded this run ({len(report.downloaded)})",
        *[f"- {name}" for name in report.downloaded],
        "",
        f"## Already had ({len(report.skipped)})",
        *[f"- {name}" for name in report.skipped],
        "",
        f"## Found but couldn't download ({len(report.failed)})",
        *[f"- **{name}**: {reason}" + (f"  \n  {url}" if url else "") for name, reason, url in report.failed],
        "",
        f"## Not found publicly ({len(report.missing)})",
        "Usually older series that are now only on the boards' secure sites",
        "(AQA Centre Services / OCR Teach Cambridge), or series not published yet.",
        "If you get any of these, save them in papers/inbox/ with these names and re-run:",
        "",
        *[f"- {name}: {reason}" for name, reason in report.missing],
        "",
    ]
    path.write_text("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subject", choices=["geography", "cs", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="list files without downloading")
    args = parser.parse_args(argv)

    config.ensure_data_dirs()
    client = PoliteClient(ALLOWED_HOSTS)
    report = Report()

    # Files you downloaded yourself go first, so the rest of the run
    # knows we already have them.
    if not args.dry_run:
        adopt_inbox(report)

    sources = []
    if args.subject in ("geography", "all"):
        sources.append(("geography", AQA_PAGE, parse_aqa_page))
    if args.subject in ("cs", "all"):
        sources.append(("computer-science", OCR_PAGE, parse_ocr_page))

    for subject, page_url, parse in sources:
        print(f"\n{subject}: reading {page_url}")
        try:
            resp = client.get(page_url)
            resp.raise_for_status()
        except (requests.RequestException, PermissionError) as e:
            report.failed.append((f"{subject} resources page", str(e), page_url))
            continue
        candidates = parse(resp.text)
        print(f"  found {len(candidates)} files")
        for candidate in sorted(candidates, key=lambda c: c.filename):
            process(client, candidate, report, args.dry_run)
        if not args.dry_run:
            find_missing(subject, report)

    if args.dry_run:
        return
    report_path = config.PAPERS_DIR / "download_report.md"
    write_report(report, report_path)
    print(f"\nDownloaded {len(report.downloaded)}, already had {len(report.skipped)}, "
          f"failed {len(report.failed)}, not found {len(report.missing)}.")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
