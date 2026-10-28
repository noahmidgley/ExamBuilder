"""
Split exam PDFs (question papers and mark schemes) into individual questions.

The big idea
------------
PyMuPDF tells us every line of text on a page and where it is (x, y). Exam
papers put question numbers in the left margin in bold, so we:

1. Read every text line with its position and whether it's bold.
2. Find the question-number labels in the left margin
   (AQA: "0 1 . 1"; OCR: "1", "(a)", "(i)").
3. Treat the paper as one long scroll. Each question runs from its label
   down to where the next question starts. If that crosses a page break, the
   question gets a slice of each page (a "region") - we skip the header and
   footer of each page, and skip blank pages.
4. Pull the marks out of each question ("[6 marks]" or "[6]").

Positions
---------
A position in the document is (page_number, y). Python compares tuples
element by element, so (3, 100) < (4, 20) is True - "earlier in the paper".
That makes "everything between label A and label B" easy to express.

A region is (page, x0, y0, x1, y1): a rectangle on one page, in PDF points
(1/72 inch). Stage 5 reuses these rectangles to copy questions into the
exported PDF as crisp vector graphics rather than blurry images.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

# --------------------------------------------------------------------------
# Reading text lines
# --------------------------------------------------------------------------


@dataclass
class Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    bold: bool
    baseline: float | None = None  # y of the text's baseline
    size: float | None = None      # font size

    @property
    def pos(self):
        return (self.page, self.y0)

    @property
    def ink_top(self):
        """Roughly where the tallest letters start (text boxes include extra space)."""
        return self.baseline - 0.8 * self.size if self.baseline else self.y0

    @property
    def ink_bottom(self):
        """Roughly where descenders (g, y, brackets) end."""
        return self.baseline + 0.25 * self.size if self.baseline else self.y1

    @property
    def y_mid(self):
        return (self.y0 + self.y1) / 2


def extract_lines(doc):
    """Every non-empty text line in the document, in reading order."""
    lines = []
    for pno, page in enumerate(doc):
        page_lines = []
        for block in page.get_text("dict")["blocks"]:
            for raw in block.get("lines", []):
                spans = [s for s in raw["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"] for s in raw["spans"]).strip()
                # flags bit 16 means bold; some PDFs only say so in the font name.
                bold = bool(spans[0]["flags"] & 16) or "bold" in spans[0]["font"].lower()
                x0, y0, x1, y1 = raw["bbox"]
                page_lines.append(Line(pno, x0, y0, x1, y1, text, bold,
                                       spans[0]["origin"][1], spans[0]["size"]))
        page_lines.sort(key=lambda l: (round(l.y0), l.x0))
        lines.extend(page_lines)
    return lines


# --------------------------------------------------------------------------
# Recognising kinds of line
# --------------------------------------------------------------------------

# "Question 1 continues on the next page", "Turn over for the next question"...
PAGE_END_RE = re.compile(
    r"(?i)continues on the (next|following) page|turn over for (the )?next question|^turn over\b"
)
# Text that marks a page as having no question content.
BLANK_PAGE_RE = re.compile(
    r"(?i)no questions printed on this page|^blank page$|do not write on this page|"
    r"answer in the spaces provided|turn over for|^additional page|additional (answer )?space"
)
# Headings that separate questions; a question never extends past one.
# Case-sensitive on purpose: "Either" starts a heading, but "either" in the
# middle of a sentence that wraps onto a new line must not.
BARRIER_RE = re.compile(
    r"^(Section [A-D]\b|SECTION [A-D]\b|Question \d+$|Answer (all|ALL|ONE|one|either|EITHER) |"
    r"Either$|EITHER$|END OF QUESTION|End of [Qq]uestion|There are no questions|"
    # mark scheme headings / back matter
    r"Qu$|Question$|Assessment [Oo]bjectives?\b|AO [Gg]rid|Summary of updates|Need to get in touch|"
    r"Copyright [Ii]nformation|Oxford Cambridge and RSA Examinations is a Company)"
)
# Running headers: page numbers, paper codes, "MARK SCHEME – ... JUNE 2022".
HEADER_RE = re.compile(
    r"^\d{1,3}$|(?i:mark scheme)|H446/0\d|7037/\d|(?i:(june|november|autumn) 20\d\d)|^PMT$"
)
# Copyright / acknowledgement pages at the end of papers.
BACK_MATTER_RE = re.compile(r"(?i)copyright information|acknowledgement|copyright © 20\d\d aqa")


def is_answer_line(line):
    """Dotted or underscored writing lines, e.g. '______' or 'Type 1 ......'."""
    text = line.text.replace(" ", "")
    if text.lower().startswith("extraspace"):
        return True
    filler = sum(ch in "._…" for ch in text)
    return filler >= 15 and filler >= 0.5 * len(text)


# --------------------------------------------------------------------------
# Page geometry
# --------------------------------------------------------------------------


@dataclass
class PageBox:
    """The part of a page that holds question content (no header/footer)."""

    x0: float
    y0: float
    x1: float
    y1: float
    blank: bool = False


def page_frame(page):
    """The "Do not write outside the box" frame AQA draws around each page.

    It's one thin, unfilled rectangle covering most of the page. Returns its
    Rect, or None (OCR papers have no frame).
    """
    w, h = page.rect.width, page.rect.height
    for d in page.get_drawings():
        r = d["rect"]
        if r.width > 0.6 * w and r.height > 0.6 * h and not d.get("fill") and d.get("color"):
            return r
    return None


def page_boxes(doc, lines):
    boxes = []
    for pno, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        here = [l for l in lines if l.page == pno]
        top, bottom, left, right = 0.03 * h, 0.97 * h, 20, w - 20

        # Header: page numbers and running titles near the top of the page.
        # We recognise them by what they say, because some questions start
        # very close to the top too.
        in_margin = [l for l in here if l.x0 > 0.9 * w]
        header = [l for l in here if l.y1 < 0.08 * h and l not in in_margin and HEADER_RE.search(l.text)]
        if header:
            top = max(l.y1 for l in header) + 2
        # Never cut off the first real line of content.
        body = [l for l in here if l.y0 > 0.02 * h and l not in header and l not in in_margin]
        if body:
            top = min(top, min(l.y0 for l in body) - 1)
        # Footer: things like "Turn over", page numbers, "© OCR 2023" and paper
        # codes in the bottom 10% of the page - recognised by what they say,
        # because mark schemes often have real content that low down too.
        footer = [l for l in here if l.y0 > 0.90 * h and not re.search(r"\[\s*\d", l.text)  # "[4]" is a mark
                  and (FOOTER_TEXT_RE.search(l.text.strip()) or HEADER_RE.search(l.text.strip())
                       or len(l.text.strip()) <= 3)]
        if footer:
            bottom = min(l.y0 for l in footer) - 2
        # "Question 1 continues on the next page" also ends the useful area.
        enders = [l for l in here if PAGE_END_RE.search(l.text) and l.y0 > top]
        if enders:
            bottom = min(bottom, min(l.y0 for l in enders) - 2)
        # A "[7]" printed level with "Turn over" belongs to the question:
        # extend the area to include it (the footer text is masked when copying).
        # Content printed level with the footer (a last answer line, or "[7]")
        # belongs to the page: extend the area to include it. Footer text that
        # overlaps it is masked when copying.
        low = [l for l in here if l.y0 < bottom < l.y1 and l not in footer
               and not FOOTER_TEXT_RE.search(l.text.strip()) and l.y1 - l.y0 < 30]
        if low:
            bottom = max(l.y1 for l in low) + 1
        # AQA's right margin says "Do not write outside the box" - crop it off.
        margin = [l for l in here if l.x0 > 0.9 * w and re.search(r"(?i)do not write|outside the|^box$", l.text)]
        if margin:
            right = min(l.x0 for l in margin) - 2

        # Answer lines count as content: a page of extra answer space that
        # ends "........ [9]" is a continuation, not a blank page.
        # Stay just inside the page frame, so its lines never appear in a crop.
        frame = page_frame(page)
        if frame:
            left, right = max(left, frame.x0 + 1.5), min(right, frame.x1 - 1.5)
            top, bottom = max(top, frame.y0 + 1.5), min(bottom, frame.y1 - 1.5)

        content = [l for l in here if top <= l.y0 < bottom and l.x0 < right]
        blank = not content or all(BLANK_PAGE_RE.search(l.text) for l in content)
        boxes.append(PageBox(left, top, right, bottom, blank))
    return boxes


def regions_between(start, end, boxes, min_height=8):
    """Slices of each page between two document positions.

    start/end are (page, y). Blank pages in between are skipped. Returns a
    list of (page, x0, y0, x1, y1).
    """
    (p0, y0), (p1, y1) = start, end
    regions = []
    for p in range(p0, p1 + 1):
        box = boxes[p]
        if box.blank and p != p0:
            continue
        top = max(y0 if p == p0 else box.y0, box.y0)
        bottom = min(y1 if p == p1 else box.y1, box.y1)
        if bottom - top >= min_height:
            regions.append((p, round(box.x0, 1), round(top, 1), round(box.x1, 1), round(bottom, 1)))
    return regions


def lines_in(regions, lines):
    """Lines that start inside any of the regions."""
    found = []
    for line in lines:
        for p, x0, y0, x1, y1 in regions:
            if line.page == p and y0 - 2 <= line.y0 < y1 and line.x0 < x1:
                found.append(line)
                break
    return found


# --------------------------------------------------------------------------
# Finding question-number labels
# --------------------------------------------------------------------------


@dataclass
class Label:
    page: int
    y0: float
    y1: float
    x0: float
    text: str  # the label line(s) joined left-to-right, e.g. "0 1 . 1" or "1 (a) Explain..."

    @property
    def pos(self):
        return (self.page, self.y0)


def left_margin_groups(lines, max_x, need_bold=True):
    """Group left-margin lines that sit on the same row.

    A label like AQA's "0 1 . 1" is often stored as two separate lines ("0"
    and "1 . 1") with slightly different y values. Grouping lines whose
    vertical middles are within 6pt rebuilds the whole label.
    """
    candidates = [l for l in lines if l.x0 < max_x and (l.bold or not need_bold)]
    groups = []
    for line in candidates:
        g = groups[-1] if groups else None
        if g and g[0].page == line.page and abs(g[0].y_mid - line.y_mid) < 6:
            g.append(line)
        else:
            groups.append([line])
    labels = []
    for g in groups:
        g.sort(key=lambda l: l.x0)
        labels.append(Label(g[0].page, min(l.y0 for l in g), max(l.y1 for l in g),
                            g[0].x0, " ".join(l.text for l in g)))
    return labels


# AQA question paper: "0 1 . 1" -> ("01", "1"); "0 5" -> ("05", None).
# Some papers store the question text on the same line as the label
# ("0 1 . 1 Outline the..."), so we match at the start and require a
# space or the end of the line straight after the label.
AQA_QP_LABEL = re.compile(r"^(\d)\s*(\d)\s*(?:\.\s*(\d{1,2}))?(?=\s|$)")
# AQA mark scheme columns: "01 1" -> ("01", "1"), possibly followed by the
# bold question text on the same row.
AQA_MS_LABEL = re.compile(r"^(\d{2})(?:\s+(\d{1,2}))?(?=\s|$)")
# OCR: optional question number, then up to two bracketed parts: "1 (a) (i)"
OCR_LABEL = re.compile(r"^(?:(\d{1,2})\*?(?:\s+|$|(?=\()))?((?:\([a-z]{1,4}\)\*?\s*){0,2})")
# Some OCR mark schemes use a compact form: "1ai", "1bii", "2c", "7*".
OCR_COMPACT_LABEL = re.compile(r"^(\d{1,2})([a-h])?(i{1,3}|iv|vi{0,3}|ix|x)?\*?(?=\s|$)")
ROMANS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
LETTERS = "abcdefghijklmn"


@dataclass
class Key:
    """A question number: AQA (q, sub) or OCR (q, letter, roman)."""

    q: int
    letter: str | None = None  # AQA: the part number as a string, e.g. "1"
    roman: str | None = None

    def display(self, board):
        if board == "aqa":
            return f"{self.q}.{int(self.letter)}" if self.letter else str(self.q)
        out = str(self.q)
        if self.letter:
            out += f"({self.letter})"
        if self.roman:
            out += f"({self.roman})"
        return out

    def ancestors(self):
        """OCR: 1(a)(i) -> [1, 1(a)]; used to attach shared stems."""
        result = []
        if self.letter or self.roman:
            result.append(Key(self.q))
        if self.roman and self.letter:
            result.append(Key(self.q, self.letter))
        return result

    def as_tuple(self):
        return (self.q, self.letter, self.roman)

    def is_ancestor_of(self, other):
        """True if `other` (a tuple) sits underneath this key, e.g. 1 -> 1(a)(i)."""
        q, letter, roman = other
        if q != self.q or self.roman:
            return False
        if self.letter:
            return letter == self.letter and roman is not None
        return letter is not None


def in_content(label, boxes):
    """False for labels in a page's header/footer (e.g. page numbers)."""
    box = boxes[label.page]
    return box.y0 - 2 <= label.y0 and label.y1 <= box.y1 + 2


def parse_aqa_labels(lines, doc_type, boxes):
    """AQA labels for a question paper ('qp') or mark scheme ('ms')."""
    if doc_type == "qp":
        # Older papers (the specimens) print the part number as far right as x=118.
        groups, pattern = left_margin_groups(lines, max_x=135), AQA_QP_LABEL
    else:
        # Mark scheme labels aren't bold, so also require the "Qu" column (x < 75).
        groups = [g for g in left_margin_groups(lines, max_x=118, need_bold=False) if g.x0 < 75]
        pattern = AQA_MS_LABEL
    found = []
    for g in groups:
        if not in_content(g, boxes):
            continue
        m = pattern.match(g.text.strip())
        if not m:
            continue
        if doc_type == "qp":
            q, sub = int(m.group(1) + m.group(2)), m.group(3)
        else:
            q, sub = int(m.group(1)), m.group(2)
        if q == 0 or q > 20:
            continue
        found.append((g, Key(q, str(int(sub)) if sub else None)))
    return found


def parse_ocr_labels(lines, doc_type, boxes):
    """OCR labels, filling in the question number/letter carried over from above.

    Mark schemes often write "1 (ii)" meaning 1(a)(ii), and question papers
    write "(b)" meaning 1(b), so we keep track of the current position.
    """
    max_x, number_max_x = (110, 66) if doc_type == "qp" else (160, 90)
    # Question paper labels are always bold; some mark schemes aren't.
    groups = left_margin_groups(lines, max_x=max_x, need_bold=(doc_type == "qp"))
    q, letter, roman = 0, None, None
    found = []
    for g in groups:
        if not in_content(g, boxes):
            continue
        text = g.text.strip()
        compact = OCR_COMPACT_LABEL.match(text) if doc_type == "ms" else None
        # "1 (a) (i)" also starts with a bare "1": only use the compact form
        # when it really is compact ("1ai") or no bracketed parts follow.
        if compact and not (compact.group(2) or compact.group(3)) and re.match(r"^\d{1,2}\*?\s*\(", text):
            compact = None
        if compact:
            number = compact.group(1)
            parts = [x for x in compact.groups()[1:] if x]
        else:
            m = OCR_LABEL.match(g.text.strip())
            number = m.group(1) if m else None
            parts = re.findall(r"\(([a-z]{1,4})\)", m.group(2)) if m else []
            if doc_type == "ms":
                # Some mark schemes drop the brackets on roman numerals:
                # "1 (a) i" or a row starting "ii" in the roman column.
                rest = text[m.end():] if m else text
                bare = re.match(r"^(i{1,3}|iv|vi{0,3}|ix|x)(?=\s|$)", rest.strip())
                if bare and (number or parts or 100 < g.x0 < 150):
                    parts.append(bare.group(1))
        if number and g.x0 > number_max_x:
            number = None  # a digit further right is body text, not a question number
        if not number and not parts:
            continue

        new_q, new_letter, new_roman = q, letter, roman
        if number:
            n = int(number)
            # Question papers number questions 1, 2, 3...; ignore anything else.
            if doc_type == "qp" and n != q + 1:
                continue
            if n != q:
                new_q, new_letter, new_roman = n, None, None
        for part in parts:
            # "(i)" is a roman numeral unless we're on letter (h), when it's letter (i).
            if part in ROMANS and not (part == "i" and new_letter == "h"):
                new_roman = part
            elif len(part) == 1 and part in LETTERS:
                new_letter, new_roman = part, None
            else:
                break
        if new_q == 0:
            continue
        if doc_type == "ms" and number and not parts and new_q == q:
            continue  # a repeated number on a continuation row, not a new item
        if (new_q, new_letter, new_roman) == (q, letter, roman):
            continue  # nothing new
        q, letter, roman = new_q, new_letter, new_roman
        found.append((g, Key(q, letter, roman)))
    return found


# --------------------------------------------------------------------------
# Splitting a question paper
# --------------------------------------------------------------------------

# Marks are always the last thing on their line ("[6 marks]" / "..... [6]").
# Anchoring to the end of the line stops code like "array pole[10]" matching.
AQA_MARKS_RE = re.compile(r"\[\s*(\d{1,2})\s*marks?\s*\]\s*$", re.IGNORECASE)
OCR_MARKS_RE = re.compile(r"\[\s*(\d{1,2})\s*\]\s*$")


def marks_on(line, marks_re, page_width):
    """Mark values on a line, if it's a genuine right-aligned marks label."""
    if line.x1 < 0.75 * page_width:
        return []  # marks are printed at the right-hand edge
    return [int(m) for m in marks_re.findall(line.text)]
FIGURE_RE = re.compile(r"\bFig(?:ure|\.)\s*(\d+(?:\.\d+)?[a-z]?)", re.IGNORECASE)


@dataclass
class Part:
    """One question as it will be stored: a markable part with its crops."""

    key: Key
    number: str                 # display form: "3.4" or "2(b)(i)"
    sort_order: int
    regions: list               # the part's own crop(s)
    context_regions: list = field(default_factory=list)  # shared stem(s) shown above it (OCR)
    marks: int | None = None
    text: str = ""
    heading: str | None = None  # AQA section topic printed above the question
    problems: list = field(default_factory=list)

    @property
    def all_regions(self):
        return self.context_regions + self.regions


def normalise_heading(text):
    """'Question 4  Population and environment' -> 'population and environment'.

    Also drops the word "the", because some papers print "Population and
    environment" for the spec's "Population and the environment".
    """
    text = re.sub(r"^question \d+\s+", "", text.strip().lower())
    return " ".join(w for w in text.split() if w != "the")


def heading_text(line):
    return normalise_heading(line.text)


FIGURE_START_RE = re.compile(r"^Figures?\s+(\d+)", re.IGNORECASE)


FIGURE_LIST_RE = re.compile(
    r"\bFig(?:ure)?s?\.?\s*((?:\d+[a-z]?\b(?:\s*(?:,|and|–|-|to)\s*(?=\d))?)+)", re.IGNORECASE
)


def figure_refs(text):
    """Every figure a piece of text mentions, e.g.

    'Figure 10'                   -> {'10'}
    'Figures 10a, 10b and 10c'    -> {'10a', '10b', '10c'}
    'Figures 9a – 9d'             -> {'9a', '9b', '9c', '9d'}  (ranges are expanded)
    'Figures 8, 9 and 10'         -> {'8', '9', '10'}
    """
    refs = set()
    for m in FIGURE_LIST_RE.finditer(text):
        listed = m.group(1)
        refs.update(r.lower() for r in re.findall(r"\d+[a-z]?", listed))
        for num, first, last in re.findall(r"(\d+)([a-z])\s*(?:–|-|to)\s*\1?([a-z])\b", listed, re.IGNORECASE):
            refs.update(f"{num}{chr(c)}" for c in range(ord(first.lower()), ord(last.lower()) + 1))
    return refs


def figure_numbers(text):
    """Just the numbers: 'Figures 8, 9 and 10' -> {'8', '9', '10'}; 'Figure 3a' -> {'3'}."""
    return {re.match(r"\d+", r).group() for r in figure_refs(text)}


def _scan_up(above, top, images, is_stop):
    """Walk upwards through `above` (lines, nearest first) from y=`top`.

    Collects closely spaced lines (and pictures filling a gap) until a line
    that belongs to the previous question. Returns (new_top, stopped, n_lines)
    - stopped is False if we ran out of lines without hitting a stop, and
    n_lines is how many text lines were collected.
    """
    first, n_lines = True, 0
    for line in above:
        gap = top - line.y1
        pics = [r for r in images if r.y1 <= top + 2 and r.y0 >= line.y1 - 2]
        if pics:
            # A picture between this line and what's below belongs with the question.
            top = min(top, min(r.y0 for r in pics))
        elif gap > 30 and not first:
            return top, True, n_lines
        if is_stop(line):
            return top, True, n_lines
        top = line.y0
        first, n_lines = False, n_lines + 1
    return top, False, n_lines


def _preamble_start(label, prev_label, lines, boxes, images, marks_re, headings=frozenset(), after_stem=False):
    """Where a question really starts, including context printed above its number.

    Exam papers often print "Figure 1 is in the insert", a short scenario or
    a photo just above the question number. We walk upwards from the label
    through closely spaced text lines and stop at anything that clearly
    belongs to the previous question: an answer line, a "[n marks]", a
    heading, or a big empty gap (unless a picture fills it).

    If we reach the top of the page without stopping, the context may have
    started on the previous page (e.g. a photo on one page, its question on
    the next), so we carry on from the bottom of that page.
    """
    def is_stop(line):
        return (is_answer_line(line) or marks_re.search(line.text) or BARRIER_RE.search(line.text)
                or PAGE_END_RE.search(line.text) or heading_text(line) in headings)

    def on_picture(line):
        # Text printed on top of a photo/map (a north arrow, labels) is part of the picture.
        mid = pymupdf.Point((line.x0 + line.x1) / 2, line.y_mid)
        return any(mid in r for r in images[line.page])

    page, box = label.page, boxes[label.page]
    if after_stem:
        # The first part straight after its own question number (e.g. "(a)"
        # after "3"): everything in between is the question's shared
        # introduction, shown with every part - so none of it is taken here.
        return (page, safe_cut(label.y0 - 4, page, lines, label.y0 - 0.5))
    floor = prev_label.y1 if prev_label and prev_label.page == page else box.y0
    above = [l for l in lines if l.page == page and l.y1 <= label.y0 + 1 and l.y0 >= floor
             and l.x0 < box.x1 and not on_picture(l)]
    # The first "gap" is between the label and the line just above it.
    top, stopped, _ = _scan_up(list(reversed(above)), label.y0, images[page], is_stop)
    first_scan_hit_nothing = not above or top <= box.y0 + 30

    prev_page = page - 1
    # Only for questions after the first (never reach back into the cover),
    # and never for the first part of a question (e.g. "(a)" straight after
    # "3"): anything on the previous page is then the question's shared
    # introduction (often a figure), which is shown with every part.
    if (prev_label and not after_stem and not stopped and first_scan_hit_nothing and prev_page >= 0
            and not boxes[prev_page].blank and prev_label.page != page):
        pbox = boxes[prev_page]
        pfloor = prev_label.y1 if prev_label and prev_label.page == prev_page else pbox.y0
        tail = [l for l in lines if l.page == prev_page and pfloor <= l.y0 < pbox.y1 and l.x0 < pbox.x1
                and not on_picture(l)]
        ptop, _, n_lines = _scan_up(list(reversed(tail)), pbox.y1, images[prev_page], is_stop)
        # Need real text (e.g. "Figure 6 is a photograph..."), not just a picture.
        if n_lines and ptop < pbox.y1 - 10:
            return (prev_page, safe_cut(ptop - 4, prev_page, lines, ptop))
    return (page, safe_cut(top - 4, page, lines, top))


def safe_cut(y, page, lines, limit=None):
    """Move a cut so it never runs through the ink of a line of text.

    Text boxes can overlap (lines printed close together), so a cut that
    looks clear of one line's box may still slice through the line above.
    We work with the letters' actual ink instead: if the cut passes through
    a line, it moves to the middle of the gap between that line's ink and
    the ink of the next line below. (`limit` is accepted for older callers
    and ignored.) Watermarks ("SPECIMEN" printed down the page) are ignored.
    """
    text_lines = [l for l in lines if l.page == page and l.y1 - l.y0 < 30 and l.text.strip()
                  and not (l.text.strip().isdigit() and l.y1 < 70)]  # page numbers are masked anyway
    cut_through = [l for l in text_lines if l.ink_top < y < l.ink_bottom]
    if not cut_through:
        return y
    above_bottom = max(l.ink_bottom for l in cut_through)
    below = [l.ink_top for l in text_lines if l.ink_top > y and l not in cut_through]
    if not below:
        return above_bottom + 1
    if min(below) <= above_bottom:
        return y  # no real gap between the lines' ink: leave the cut where it was
    return (above_bottom + min(below)) / 2


def split_question_paper(doc, board, topic_names=()):
    """Split a question paper into Parts. board is 'aqa' or 'ocr'."""
    lines = extract_lines(doc)
    boxes = page_boxes(doc, lines)
    marks_re = AQA_MARKS_RE if board == "aqa" else OCR_MARKS_RE
    labels = parse_aqa_labels(lines, "qp", boxes) if board == "aqa" else parse_ocr_labels(lines, "qp", boxes)
    if not labels:
        return [], ["no question numbers found - is this the right exam board's layout?"]

    images = {pno: [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()] for pno, page in enumerate(doc)}
    topic_lookup = {normalise_heading(t): t for t in topic_names}

    # Where each label's question starts (including its preamble).
    starts = []
    for i, (label, key) in enumerate(labels):
        prev = labels[i - 1][0] if i else None
        after_stem = bool(i) and labels[i - 1][1].is_ancestor_of(key.as_tuple())
        starts.append(_preamble_start(label, prev, lines, boxes, images, marks_re, topic_lookup.keys(),
                                      after_stem=after_stem))

    # A figure printed after one question's answer space, that the question
    # never mentions, belongs to the next question (e.g. "Figure 3 shows..."
    # followed by a table to complete in the next part).
    for i in range(len(labels) - 1):
        label = labels[i][0]
        between = [l for l in lines if label.pos <= l.pos < starts[i + 1] and not boxes[l.page].blank]
        marks_at = next((k for k, l in enumerate(between)
                         if marks_on(l, marks_re, doc[l.page].rect.width)), None)
        if marks_at is None:
            continue
        own_figures = figure_numbers(" ".join(l.text for l in between[:marks_at + 1]))
        for line in between[marks_at + 1:]:
            m = FIGURE_START_RE.match(line.text.strip())
            if m and m.group(1) not in own_figures:
                starts[i + 1] = (line.page, line.y0 - 4)
                break

    # Barriers: headings / END OF QUESTIONS that no question runs past.
    barriers = [(l.page, l.y0 - 2) for l in lines if BARRIER_RE.search(l.text) and not boxes[l.page].blank]
    back_matter = [l.page for l in lines if BACK_MATTER_RE.search(l.text)]
    last_page = min(back_matter) - 1 if back_matter and min(back_matter) > labels[-1][0].page else len(doc) - 1
    doc_end = (last_page, boxes[last_page].y1)

    # Section headings (AQA prints the topic name above each section/question).
    headings = [(l.pos, topic_lookup[heading_text(l)]) for l in lines if heading_text(l) in topic_lookup]

    # Each label's own segment: from its start to the next start or barrier.
    segments = []
    for i, (label, key) in enumerate(labels):
        end = starts[i + 1] if i + 1 < len(labels) else doc_end
        later_barriers = [b for b in barriers if label.pos < b < end]
        if later_barriers:
            end = min(later_barriers)
        regions = regions_between(starts[i], end, boxes)
        # Drop an empty sliver at the top of the next page (e.g. the space
        # above the next section's heading).
        if (len(regions) > 1 and regions[-1][0] > label.page and regions[-1][4] - regions[-1][2] < 60
                and not lines_in(regions[-1:], lines)):
            regions = regions[:-1]
        segments.append((label, key, regions))

    # Marks inside each segment (only after the label itself).
    by_key = {}
    for label, key, regions in segments:
        own = [l for l in lines_in(regions, lines) if l.pos >= (label.page, label.y0 - 2)]
        marks = [m for l in own for m in marks_on(l, marks_re, doc[l.page].rect.width)]
        by_key[key.as_tuple()] = (label, key, regions, marks)

    parts = []
    for order, (label, key, regions, marks) in enumerate(by_key.values()):
        # A part is a question if it has marks. Parts with no marks but with
        # children (e.g. OCR "1" or "1(a)" introducing (i), (ii)) are stems.
        children = [k for k in by_key if key.is_ancestor_of(k)]
        if not marks and children:
            continue
        part = Part(key, key.display(board), order, regions)
        part.marks = sum(marks) if marks else None
        if not marks:
            part.problems.append("couldn't find the mark value")
        elif len(marks) > 1:
            part.problems.append(f"found several mark values {marks}, used their total")
        if not regions:
            part.problems.append("crop came out empty")

        # OCR: include the shared stem(s) above, e.g. 1 and 1(a) for 1(a)(i).
        for anc in key.ancestors():
            stem = by_key.get(anc.as_tuple())
            if stem and not stem[3]:  # stems have no marks of their own
                part.context_regions += stem[2]

        text_lines = [l for l in lines_in(part.all_regions, lines) if not is_answer_line(l)]
        part.text = " ".join(l.text for l in text_lines)
        if len(part.text) < 15:
            part.problems.append("very little text found - check the crop")

        current = [name for pos, name in headings if pos < label.pos]
        part.heading = current[-1] if current else None
        parts.append(part)

    return parts, []


# --------------------------------------------------------------------------
# Splitting a mark scheme
# --------------------------------------------------------------------------


def horizontal_rules(page):
    """y positions of the horizontal table lines drawn across a page."""
    min_width = 0.3 * page.rect.width
    ys = set()
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 1 and abs(b.x - a.x) > min_width:
                    ys.add(round(a.y, 1))
            elif item[0] == "re" and item[1].width > min_width:
                ys.update({round(item[1].y0, 1), round(item[1].y1, 1)})
    return sorted(ys)


def trim_to_content(regions, lines, rules):
    """Cut empty space off the bottom of each mark scheme slice.

    Each slice ends just below its last line of text - on the table's
    bottom border if there is one close by. Slices with no text are dropped.
    """
    trimmed = []
    for region in regions:
        p, x0, y0, x1, y1 = region
        inside = lines_in([region], lines)
        if not inside:
            continue
        last = max(l.y1 for l in inside)
        border = [y for y in rules.get(p, []) if last <= y <= last + 20]
        bottom = min(y1, (min(border) + 1) if border else last + 6)
        trimmed.append((p, x0, y0, x1, round(bottom, 1)))
    return trimmed


def split_mark_scheme(doc, board):
    """Return {Key tuple: (regions, text)} for every item in a mark scheme.

    Mark schemes are tables. A label is sometimes centred vertically in its
    table cell, so each item starts at the cell's top border (the nearest
    horizontal table line above the label) rather than at the label itself.
    """
    lines = extract_lines(doc)
    boxes = page_boxes(doc, lines)
    labels = parse_aqa_labels(lines, "ms", boxes) if board == "aqa" else parse_ocr_labels(lines, "ms", boxes)
    barriers = [(l.page, l.y0 - 2) for l in lines if BARRIER_RE.search(l.text)]
    doc_end = (len(doc) - 1, boxes[-1].y1)
    rules = {}

    starts = []
    for i, (label, key) in enumerate(labels):
        if label.page not in rules:
            rules[label.page] = horizontal_rules(doc[label.page])
        prev = labels[i - 1][0] if i else None
        floor = prev.y1 if prev and prev.page == label.page else boxes[label.page].y0 - 1
        above = [y for y in rules[label.page] if floor < y <= label.y0 + 1]
        start = max(above) if above else label.y0 - 3
        # Start below any heading ("Section B", "Question 2", "Qu Part"...)
        # sitting between that table line and the label.
        headings = [l.y1 for l in lines if l.page == label.page and start <= l.y0 < label.y0 - 1
                    and BARRIER_RE.search(l.text)]
        start = max([start] + [y + 1 for y in headings])
        starts.append((label.page, safe_cut(start, label.page, lines)))

    items = {}
    for i, (label, key) in enumerate(labels):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(labels) else doc_end
        later = [b for b in barriers if label.pos < b < end]
        if later:
            end = min(later)
        regions = regions_between(start, end, boxes)
        for p in {r[0] for r in regions}:
            if p not in rules:
                rules[p] = horizontal_rules(doc[p])
        regions = trim_to_content(regions, lines, rules)
        text = " ".join(l.text for l in lines_in(regions, lines))
        # The same item can appear twice (e.g. a table split over two rows);
        # keep both pieces in order.
        old = items.get(key.as_tuple())
        items[key.as_tuple()] = (old[0] + regions, old[1] + " " + text) if old else (regions, text)
    return items


def match_mark_scheme(part, ms_items):
    """Find the mark scheme regions for a question part.

    Exact match first; otherwise use a parent item (the mark scheme answers
    several parts together) or all child items (the mark scheme is more
    finely split than the paper).
    """
    key = part.key.as_tuple()
    if key in ms_items:
        return ms_items[key]
    for anc in part.key.ancestors():
        if anc.as_tuple() in ms_items:
            part.problems.append(f"mark scheme shared with other parts (under {anc.display('ocr')})")
            return ms_items[anc.as_tuple()]
    kids = [v for k, v in ms_items.items() if part.key.is_ancestor_of(k)]
    if kids:
        return [r for v in kids for r in v[0]], " ".join(v[1] for v in kids)
    part.problems.append("no matching mark scheme found")
    return [], ""


# --------------------------------------------------------------------------
# Geography inserts (Figure pages)
# --------------------------------------------------------------------------


def insert_figure_pages(doc):
    """{'3a': [page, ...]} - which insert page each figure is printed on."""
    figures = {}
    for line in extract_lines(doc):
        # Only headings that start a line, e.g. "Figure 3a" or "Figures 9a – 9d".
        if re.match(r"^Figures?\s+\d", line.text.strip(), re.IGNORECASE):
            heading = re.split(r"\s+(?:shows?|is|are)\b", line.text.strip())[0]
            for ref in figure_refs(heading):
                figures.setdefault(ref, set()).add(line.page)
    return {k: sorted(v) for k, v in figures.items()}


def figures_for(part, figure_pages):
    """Insert pages for the figures a question mentions (e.g. 'Figure 3')."""
    pages, missing = set(), []
    for ref in sorted(figure_refs(part.text)):
        number = re.match(r"\d+", ref).group()
        if ref in figure_pages:
            pages.update(figure_pages[ref])
        elif ref == number and any(re.fullmatch(rf"{number}[a-z]", k) for k in figure_pages):
            # "Figure 3" printed in the insert as "Figure 3a" and "Figure 3b".
            pages.update(p for k, ps in figure_pages.items() if re.fullmatch(rf"{number}[a-z]", k) for p in ps)
        elif number in figure_pages:
            # "Figure 3a" printed in the insert under a plain "Figure 3" heading.
            pages.update(figure_pages[number])
        elif re.search(rf"Figures?[^.]*\b{ref}\b[^.]*\binsert", part.text, re.IGNORECASE):
            missing.append(ref)
    if missing:
        part.problems.append(f"Figure {', '.join(missing)} should be in the insert but wasn't found")
    return sorted(pages)


# --------------------------------------------------------------------------
# Rendering crops to images
# --------------------------------------------------------------------------


FOOTER_TEXT_RE = re.compile(r"(?i)^turn over|^© ?ocr|^\(\d{2}\)$|continues on the next page|"
                            r"^do not write|^outside the$|^box$|^G/[A-Z0-9]+/")


def paste(target_page, target, src_doc, pno, clip):
    """Copy `clip` from page `pno` of src_doc into `target` on target_page.

    Footer/margin text ("Turn over", "Do not write outside the box"...) that
    pokes into the slice is covered with white, so it never appears inside
    a question.
    """
    target_page.show_pdf_page(target, src_doc, pno, clip=clip)
    sx, sy = target.width / clip.width, target.height / clip.height
    src_rect = src_doc[pno].rect
    for block in src_doc[pno].get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            x0, y0, x1, y1 = line["bbox"]
            page_number = (re.fullmatch(r"\d{1,3}", text) and y1 < 0.08 * src_rect.height
                           and abs((x0 + x1) / 2 - src_rect.width / 2) < 40)
            if FOOTER_TEXT_RE.search(text) or page_number:
                r = pymupdf.Rect(line["bbox"]) & clip
                if not r.is_empty:
                    target_page.draw_rect(pymupdf.Rect(
                        target.x0 + (r.x0 - clip.x0) * sx - 1, target.y0 + (r.y0 - clip.y0) * sy - 1,
                        target.x0 + (r.x1 - clip.x0) * sx + 1, target.y0 + (r.y1 - clip.y0) * sy + 1,
                    ), color=None, fill=(1, 1, 1))


def merge_touching(regions):
    """Join slices of the same page that touch end-to-start into one slice.

    A question's introduction and its own text are stored as separate
    slices. When they meet exactly, drawing them separately (with a gap)
    could split a line of text in two at the seam - so we join them first.
    """
    merged = []
    for region in regions:
        p, x0, y0, x1, y1 = region
        if merged and merged[-1][0] == p and abs(merged[-1][4] - y0) < 1:
            last = merged[-1]
            merged[-1] = [p, min(last[1], x0), last[2], max(last[3], x1), y1]
        else:
            merged.append(list(region))
    return merged


def render_png(doc, regions, zoom=2.0, gap=6):
    """Stack the regions vertically and return them as PNG bytes (or None).

    We build a temporary one-page PDF, copy each region onto it with
    show_pdf_page (which keeps text and lines as vectors), then render that
    page to a PNG. zoom=2 gives 144 dpi - sharp on screen.
    """
    regions = merge_touching(regions)
    rects = [pymupdf.Rect(x0, y0, x1, y1) for _, x0, y0, x1, y1 in regions]
    if not rects:
        return None  # nothing to draw (the part is flagged for review)
    width = max(r.width for r in rects)
    height = sum(r.height for r in rects) + gap * (len(rects) - 1)
    out = pymupdf.open()
    page = out.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=(1, 1, 1))  # white background
    y = 0
    for (pno, *_), rect in zip(regions, rects):
        target = pymupdf.Rect(0, y, rect.width, y + rect.height)
        paste(page, target, doc, pno, rect)
        y += rect.height + gap
    png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    out.close()
    return png


def render_regions(doc, regions, out_path, zoom=2.0, gap=6):
    """Like render_png, but saves the image to a file. Returns False if empty."""
    png = render_png(doc, regions, zoom, gap)
    if png is None:
        return False
    Path(out_path).write_bytes(png)
    return True


def paper_total(doc, board):
    """The total marks printed on the cover, if found."""
    text = doc[0].get_text() + (doc[1].get_text() if len(doc) > 1 else "")
    m = re.search(r"total (?:number of )?marks? (?:available )?for this paper is (\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None
