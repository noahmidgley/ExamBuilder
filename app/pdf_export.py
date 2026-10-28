"""
Stage 5: build the question paper and mark scheme PDFs for a saved paper.

The aim is for the result to look like a real exam paper that happens to
contain only the questions you chose:

- Each question is copied from the original PDF at its actual size and in
  the same position across the page, so the board's fonts, spacing, answer
  lines and original question numbers ("0 3 . 4", "2 (b) (i)") are kept.
  show_pdf_page copies them as vector graphics, so they print crisply.
- Each output page is dressed like the board's own pages: AQA's grey "Do not
  write outside the box" frame (copied from the original paper) and a page
  number at the top; OCR's bold page number.
- Any insert pages the questions need (Geography figures) are added at the
  back, exactly as they appear in the original insert.
- A front cover in the board's own style (AQA or OCR), made from what you
  selected: title, time, total marks, materials, instructions and an
  examiner's grid listing your questions by their original numbers.
  Pass cover=False for just the questions.
- Questions are renumbered 1, 2, 3... (parts re-lettered from (a) / .1) in
  the paper's own font - see app/renumber.py. The mark scheme matches.

The mark scheme PDF works the same way, using the original mark scheme
pages' size (OCR's are landscape) and layout.

If an original PDF is missing (e.g. not copied to the server), the stored
PNG crop is used instead, so export still works.
"""
import json
import re

import pymupdf

import config
from app import renumber
from app.formatting import question_label
from importer.splitter import merge_touching, page_frame, paste

GAP = 14          # space between questions
FALLBACK_SIZE = (595, 842)


class SourceDocs:
    """Opens each original PDF once and reuses it."""

    def __init__(self):
        self.cache = {}

    def get(self, subject, filename):
        if not filename:
            return None
        folder = config.GEOGRAPHY_PAPERS_DIR if subject == "geography" else config.CS_PAPERS_DIR
        if filename not in self.cache:
            path = folder / filename
            self.cache[filename] = pymupdf.open(path) if path.exists() else None
        return self.cache[filename]

    def close(self):
        for doc in self.cache.values():
            if doc:
                doc.close()


# --------------------------------------------------------------------------
# Page style: copied from the first original page we use
# --------------------------------------------------------------------------

class PageStyle:
    """What a blank page of this paper looks like: size, frame, margin note.

    Taken from a real page of the source paper, so the output matches it.
    """

    def __init__(self, src_page=None, use_frame=True, page_offset=0):
        self.src_page = src_page
        self.page_offset = page_offset  # 1 when a cover will be page 1
        self.size = (src_page.rect.width, src_page.rect.height) if src_page else FALLBACK_SIZE
        self.frame = page_frame(src_page) if (src_page and use_frame) else None
        self.frame_color = (0.58, 0.59, 0.6)
        if self.frame:
            for d in src_page.get_drawings():
                if d["rect"] == self.frame and d.get("color"):
                    self.frame_color = d["color"]
        w, h = self.size
        if self.frame:
            self.top, self.bottom = self.frame.y0 + 10, self.frame.y1 - 8
        else:
            self.top, self.bottom = 0.07 * h, 0.92 * h

    def new_page(self, out):
        w, h = self.size
        page = out.new_page(width=w, height=h)
        number = str(out.page_count + self.page_offset)
        if self.frame:
            # AQA: the grey frame, the "Do not write outside the box" note in
            # the right margin (copied from the original), page number on top.
            page.draw_rect(self.frame, color=self.frame_color, width=0.5)
            margin = pymupdf.Rect(self.frame.x1 + 1, self.frame.y0 - 4, w, self.frame.y0 + 26)
            page.show_pdf_page(margin, self.src_page.parent, self.src_page.number, clip=margin)
            page.insert_text((w / 2 - 3 * len(number), 38), number, fontname="helv", fontsize=11)
        else:
            # OCR style: bold page number at the top centre.
            page.insert_text((w / 2 - 3 * len(number), 0.06 * h), number, fontname="hebo", fontsize=11)
        return page


class Flow:
    """Places slices down the page, starting a new page when one is full."""

    def __init__(self, out, style):
        self.out, self.style = out, style
        self.page = None
        self.y = 0

    def ensure(self, height):
        if self.page is None or self.y + height > self.style.bottom:
            self.page = self.style.new_page(self.out)
            self.y = self.style.top

    def room(self):
        return self.style.bottom - self.style.top


# --------------------------------------------------------------------------
# Copying slices of the original pages
# --------------------------------------------------------------------------

def trimmed_clip(src_page, clip, cache):
    """Cut empty space off the bottom of a slice (below its last text,
    picture or drawn shape). Page frames don't count as content."""
    key = src_page.number
    if key not in cache:
        frame = page_frame(src_page)
        cache[key] = {
            "shapes": [d["rect"] for d in src_page.get_drawings() if d["rect"] != frame
                       and d["rect"].height < 0.6 * src_page.rect.height],
            "images": [pymupdf.Rect(i["bbox"]) for i in src_page.get_image_info()],
        }
    bottoms = []
    for block in src_page.get_text("dict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            if line["bbox"][1] >= clip.y0 and "".join(s["text"] for s in line["spans"]).strip():
                bottoms.append(line["bbox"][3])
    for r in cache[key]["shapes"] + cache[key]["images"]:
        if r.intersects(clip) and r.y0 >= clip.y0 - 1:
            bottoms.append(r.y1)
    if not bottoms:
        return clip
    bottom = min(clip.y1, max(bottoms) + 8)
    return pymupdf.Rect(clip.x0, clip.y0, clip.x1, bottom) if clip.y1 - bottom > 20 else clip


def place_regions(flow, src_doc, regions, image_path=None, relabel=None):
    """Copy each slice at its original size and horizontal position.

    Keep-together rule: if a question's first two slices (usually its
    introduction and the start of the question) would fit on one page but
    not in the space left on this one, start the question on a new page, so
    an introduction isn't split from what follows it.

    relabel: (Renumbering, "qp" or "ms", Writer) to renumber the labels.
    """
    if src_doc is None:
        place_image(flow, image_path)
        return
    cache = {}
    clips = [(pno, trimmed_clip(src_doc[pno], pymupdf.Rect(x0, y0, x1, y1), cache))
             for pno, x0, y0, x1, y1 in merge_touching(regions)]
    if len(clips) >= 2 and flow.page is not None:
        together = clips[0][1].height + clips[1][1].height + 2
        if together <= flow.room() and flow.y + together > flow.style.bottom:
            flow.page = None  # forces a new page
    number_found, first_placed = False, None
    for pno, clip in clips:
        scale = min(1.0, flow.room() / clip.height)  # only shrinks a slice taller than a page
        height = clip.height * scale
        flow.ensure(height)
        target = pymupdf.Rect(clip.x0, flow.y, clip.x0 + clip.width * scale, flow.y + height)
        paste(flow.page, target, src_doc, pno, clip)
        if relabel:
            r, doc_type, writer = relabel
            number_found |= renumber.apply(flow.page, target, clip, src_doc[pno], r, doc_type, writer)
            first_placed = first_placed or (flow.page, target, clip, src_doc[pno])
        flow.y += height + 2

    if relabel and not number_found and first_placed and relabel[0].board == "ocr" and relabel[1] == "qp":
        # The question number wasn't in any slice: write it in the number
        # column, level with the first part label, e.g. "(a)".
        page, target, clip, src_page = first_placed
        words = renumber.words_in(src_page, clip)
        first = next((w for w in words if renumber.text_of(w).startswith("(") and w[0].bbox.x0 < 110), None)
        if first:
            sx = target.width / clip.width
            point = (target.x0 + (50 - clip.x0) * sx, target.y0 + (first[0].origin[1] - clip.y0) * sx)
            relabel[2].draw(page, point, str(relabel[0].new_q), first[0].size * sx, src_page, first[0])


def place_image(flow, image_path):
    """Fallback: place the stored PNG crop when the original PDF is missing."""
    w, _ = flow.style.size
    if not image_path or not image_path.exists():
        flow.ensure(20)
        flow.page.insert_text((60, flow.y + 12), "(This question's image is missing.)",
                              fontname="helv", fontsize=10, color=(0.7, 0, 0))
        flow.y += 20
        return
    pix = pymupdf.Pixmap(str(image_path))
    scale = min((w - 100) / pix.width, flow.room() / pix.height)
    height = pix.height * scale
    flow.ensure(height)
    flow.page.insert_image(pymupdf.Rect(50, flow.y, 50 + pix.width * scale, flow.y + height),
                           filename=str(image_path))
    flow.y += height + 2


def first_source_page(docs, subject, questions, file_key, regions_key):
    """A real page from the first question's source PDF, to copy the style from."""
    for q in questions:
        doc = docs.get(subject, q[file_key])
        regions = json.loads(q[regions_key] or "[]")
        if doc is not None and regions:
            return doc[regions[0][0]]
    return None


# --------------------------------------------------------------------------
# Inserts (Geography figures)
# --------------------------------------------------------------------------

def needed_insert_pages(questions):
    """Insert pages the questions use, in question order, each once.

    Also returns the questions that mention the insert but whose insert
    isn't available, so we can say so rather than silently leave it out.
    """
    pages, missing = [], []
    for q in questions:
        used = json.loads(q["insert_pages"] or "[]")
        if used and q["ins_filename"]:
            for p in used:
                if (q["ins_filename"], p) not in pages:
                    pages.append((q["ins_filename"], p))
        elif re.search(r"in the insert", q["question_text"] or "", re.IGNORECASE):
            missing.append(q)
    return pages, missing


def add_inserts(out, docs, subject, pages, missing, source_label):
    for fname, pno in pages:
        src = docs.get(subject, fname)
        if src is None or pno >= src.page_count:
            continue
        rect = src[pno].rect
        out.new_page(width=rect.width, height=rect.height).show_pdf_page(rect, src, pno)
    if missing:
        page = out.new_page(width=FALLBACK_SIZE[0], height=FALLBACK_SIZE[1])
        names = "".join(f"<li>{source_label(q)}</li>" for q in missing)
        page.insert_htmlbox(
            pymupdf.Rect(60, 80, FALLBACK_SIZE[0] - 60, 500),
            f"<p><b>Missing insert pages</b></p><p>These questions use figures from an insert that "
            f"hasn't been downloaded yet:</p><ul>{names}</ul>",
            css="* { font-family: sans-serif; font-size: 11px; }",
        )


# --------------------------------------------------------------------------
# Front covers, in each board's style
# --------------------------------------------------------------------------

COVER_CSS = """
* { font-family: sans-serif; color: #000; }
p, li { font-size: 10.5px; margin: 0 0 3px 0; }
ul { margin: 0 0 8px 0; padding-left: 12px; }
h3 { font-size: 11.5px; margin: 10px 0 3px 0; }
.big { font-size: 22px; margin: 0; }
.subject { font-size: 30px; font-weight: bold; margin: 0; }
.title { font-size: 17px; margin: 6px 0 0 0; }
.small { font-size: 8.5px; color: #555; }
table { border-collapse: collapse; }
td, th { border: 1px solid #000; font-size: 10px; padding: 3px 6px; text-align: center; }
"""


def short_series(series):
    """'June 2022' -> 'Jun 22'; 'Specimen' stays."""
    m = re.match(r"(\w{3})\w* (\d{2})(\d{2})", series)
    return f"{m.group(1)} {m.group(3)}" if m else series


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def boxes(page, x, y, count, size=17):
    """A row of little digit boxes (centre/candidate number)."""
    for i in range(count):
        page.draw_rect(pymupdf.Rect(x + i * size, y, x + (i + 1) * size, y + 20), color=(0, 0, 0), width=0.6)


def write_line(page, x0, x1, y):
    page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.5)


def sources_text(questions, paper_names):
    seen = []
    for q in questions:
        name = f"{paper_names.get(q['paper'], q['paper'])} {q['series']}"
        if name not in seen:
            seen.append(name)
    return ", ".join(seen)


def aqa_cover(paper, questions, time_text, marks, has_insert, paper_names, new_labels):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((46, 125), "Please write clearly in block capitals.", fontname="helv", fontsize=10)
    page.insert_text((57, 168), "Centre number", fontname="helv", fontsize=10)
    boxes(page, 135, 154, 5)
    page.insert_text((315, 168), "Candidate number", fontname="helv", fontsize=10)
    boxes(page, 410, 154, 4)
    for y, label in ((204, "Surname"), (230, "Forename(s)"), (256, "Candidate signature")):
        page.insert_text((46, y), label, fontname="helv", fontsize=10)
        write_line(page, 150, 550, y + 2)
    page.insert_text((360, 270), "I declare this is my own work.", fontname="helv", fontsize=8)

    materials = (["the insert (printed at the back of this booklet)"] if has_insert else []) + \
        ["a pencil", "a rubber", "a ruler."]
    left = f"""
      <p class='big'>A-level</p>
      <p class='subject'>GEOGRAPHY</p>
      <p class='title'>{esc(paper['title'])}</p>
      <p class='small'>Questions from {esc(sources_text(questions, paper_names))}</p>
      <h3>Materials</h3>
      <p>For this paper you must have:</p>
      <ul>{''.join(f'<li>{esc(m)}</li>' for m in materials)}</ul>
      <p>You may use a calculator.</p>
      <h3>Instructions</h3>
      <ul>
        <li>Use black ink or black ball-point pen.</li>
        <li>Fill in the boxes at the top of this page.</li>
        <li>Answer <b>all</b> questions.</li>
        <li>You must answer the questions in the spaces provided. Do not write outside the box around each page or on blank pages.</li>
        <li>Do all rough work in this book. Cross through any work you do not want to be marked.</li>
      </ul>
      <h3>Information</h3>
      <ul>
        <li>The marks for questions are shown in brackets.</li>
        <li>The total number of marks available for this paper is {marks}.</li>
      </ul>
    """
    page.insert_htmlbox(pymupdf.Rect(46, 285, 430, 800), left, css=COVER_CSS, scale_low=0.6)
    page.insert_text((330, 420), f"Time allowed: {time_text}", fontname="helv", fontsize=11)

    rows = "".join(
        f"<tr><td>{esc(label)} <span class='small'>({esc(question_label(q['subject'], q['question_number']))} "
        f"{esc(short_series(q['series']))})</span></td><td>&nbsp;</td></tr>"
        for q, label in zip(questions, new_labels))
    grid = (f"<p><b>For Examiner’s Use</b></p><table><tr><th>Question</th><th>Mark</th></tr>{rows}"
            f"<tr><th>TOTAL</th><td>&nbsp;</td></tr></table>")
    page.insert_htmlbox(pymupdf.Rect(440, 440, 560, 800), grid, css=COVER_CSS, scale_low=0.4)
    return doc


def ocr_cover(paper, questions, time_text, marks, total_pages, paper_names):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((46, 70), "Please write clearly in black ink.", fontname="helv", fontsize=10)
    page.draw_rect(pymupdf.Rect(46, 80, 550, 140), color=(0, 0, 0), width=0.6)
    page.insert_text((52, 96), "First name(s)", fontname="helv", fontsize=9)
    page.insert_text((52, 126), "Last name", fontname="helv", fontsize=9)
    write_line(page, 46, 550, 110)
    page.insert_text((52, 165), "Centre number", fontname="helv", fontsize=9)
    boxes(page, 130, 151, 5)
    page.insert_text((300, 165), "Candidate number", fontname="helv", fontsize=9)
    boxes(page, 390, 151, 4)

    body = f"""
      <p class='big'><b>A Level Computer Science</b></p>
      <p class='title'><b>{esc(paper['title'])}</b></p>
      <p class='small'>Questions from {esc(sources_text(questions, paper_names))}</p>
      <p style='margin-top:6px'><b>Time allowed: {esc(time_text)}</b></p>
      <table style='margin:10px 0'><tr>
        <td style='text-align:left; width:150px'><b>You can use:</b><br>• a ruler (cm/mm)<br>• an HB pencil</td>
        <td style='text-align:left; width:150px'><b>Do not use:</b><br>• a calculator</td>
      </tr></table>
      <h3>INSTRUCTIONS</h3>
      <ul>
        <li>Use black ink. You can use an HB pencil, but only for graphs and diagrams.</li>
        <li>Write your answer to each question in the space provided.</li>
        <li>Answer <b>all</b> the questions.</li>
      </ul>
      <h3>INFORMATION</h3>
      <ul>
        <li>The total mark for this paper is <b>{marks}</b>.</li>
        <li>The marks for each question are shown in brackets [ ].</li>
        <li>Quality of extended response will be assessed in questions marked with an asterisk (*).</li>
        <li>This document has <b>{total_pages}</b> pages.</li>
      </ul>
      <h3>ADVICE</h3>
      <ul><li>Read each question carefully before you start your answer.</li></ul>
    """
    page.insert_htmlbox(pymupdf.Rect(46, 200, 550, 800), body, css=COVER_CSS, scale_low=0.6)
    return doc


# --------------------------------------------------------------------------
# Public functions used by the routes
# --------------------------------------------------------------------------

def build_question_paper(paper, questions, source_label, time_text="", paper_names=None, cover=True):
    """Return the question paper PDF as bytes (with a board-style cover unless cover=False)."""
    out, docs = pymupdf.open(), SourceDocs()
    subject = paper["subject"]
    try:
        style = PageStyle(first_source_page(docs, subject, questions, "qp_filename", "question_regions"),
                          page_offset=1 if cover else 0)
        flow = Flow(out, style)
        writer = renumber.Writer(renumber.FontBox())
        plans = [renumber.plan(subject, q["question_number"], k) for k, q in enumerate(questions, start=1)]
        for q, r in zip(questions, plans):
            # Every question gets its own number, so each one is printed with
            # its own introduction (carrying the new number).
            if flow.page is not None:
                flow.y += GAP
            place_regions(flow, docs.get(subject, q["qp_filename"]), json.loads(q["question_regions"] or "[]"),
                          config.QUESTION_IMAGES_DIR / (q["question_image"] or ""), relabel=(r, "qp", writer))
        pages, missing = needed_insert_pages(questions)
        add_inserts(out, docs, subject, pages, missing, source_label)
        if out.page_count == 0:
            style.new_page(out)
        if cover:
            marks = sum(q["marks"] or 0 for q in questions)
            names = paper_names or {}
            front = (aqa_cover(paper, questions, time_text, marks, bool(pages or missing), names,
                               [r.label() for r in plans])
                     if subject == "geography"
                     else ocr_cover(paper, questions, time_text, marks, out.page_count + 1, names))
            front.insert_pdf(out)  # cover first, then the questions
            out.close()
            out = front
        return out.tobytes(garbage=3, deflate=True)
    finally:
        docs.close()
        out.close()


def build_mark_scheme(paper, questions, source_label):
    """Return the mark scheme PDF as bytes."""
    out, docs = pymupdf.open(), SourceDocs()
    subject = paper["subject"]
    try:
        # Mark schemes have no frame: plain pages the same size as the original.
        style = PageStyle(first_source_page(docs, subject, questions, "ms_filename", "markscheme_regions"),
                          use_frame=False)
        flow = Flow(out, style)
        writer = renumber.Writer(renumber.FontBox())
        for k, q in enumerate(questions, start=1):
            r = renumber.plan(subject, q["question_number"], k)
            regions = json.loads(q["markscheme_regions"] or "[]")
            if flow.page is not None:
                flow.y += GAP
            if not regions and not q["markscheme_image"]:
                flow.ensure(20)
                flow.page.insert_text((60, flow.y + 12), f"{r.label()}: no mark scheme was found for {source_label(q)}.",
                                      fontname="helv", fontsize=10, color=(0.7, 0, 0))
                flow.y += 20
                continue
            place_regions(flow, docs.get(subject, q["ms_filename"]), regions,
                          config.MARKSCHEME_IMAGES_DIR / (q["markscheme_image"] or ""), relabel=(r, "ms", writer))
        if out.page_count == 0:
            style.new_page(out)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        docs.close()
        out.close()
