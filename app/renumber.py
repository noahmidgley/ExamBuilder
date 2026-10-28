"""
Renumber questions in the exported PDFs so they run 1, 2, 3... in order.

Rules (as chosen):
  - Every question you picked gets its own number, in the order of the paper.
  - Parts are re-lettered so each question starts at (a) / (i) / .1:
        OCR 3(d)(ii) picked as question 2  ->  2(a)(i)
        AQA 04.3     picked as question 1  ->  01.1
  - The mark scheme is renumbered the same way.

How a label is replaced
-----------------------
We look at every character inside the slice being copied (PyMuPDF's
"rawdict" gives each character's box, font, size, colour and baseline),
group characters into words, and recognise question labels by what they say
and where they are (left margin, usually bold). For each label that needs
to change we:
  1. cover just the old characters with white - for AQA only the inside of
     each little digit box, so the boxes themselves are untouched;
  2. draw the new characters at the same baseline and size, using the font
     embedded in that exam PDF (Arial / Arial Bold), so they look identical.
     If a character isn't in the embedded font we fall back to Helvetica,
     which looks practically the same.
"""
import re
from dataclasses import dataclass

import pymupdf

ROMAN_RE = r"(?:i{1,3}|iv|vi{0,3}|ix|x)"


# --------------------------------------------------------------------------
# What each picked question becomes
# --------------------------------------------------------------------------

@dataclass
class Renumbering:
    board: str            # 'aqa' or 'ocr'
    old_q: int
    old_letter: str | None   # OCR letter, or AQA part number as a string
    old_roman: str | None
    new_q: int

    @property
    def new_letter(self):
        if not self.old_letter:
            return None
        return "1" if self.board == "aqa" else "a"

    @property
    def new_roman(self):
        return "i" if self.old_roman else None

    def label(self):
        """The new number as text, e.g. '01.1' or '2(a)(i)'."""
        if self.board == "aqa":
            return f"{self.new_q:02d}" + (f".{self.new_letter}" if self.new_letter else "")
        return f"{self.new_q}" + (f"({self.new_letter})" if self.new_letter else "") + \
            (f"({self.new_roman})" if self.new_roman else "")


def plan(subject, number, new_q):
    """Work out the renumbering for one stored question number ('3.1' / '3(d)(ii)')."""
    board = "aqa" if subject == "geography" else "ocr"
    if board == "aqa":
        main, _, part = number.partition(".")
        return Renumbering(board, int(main), part or None, None, new_q)
    m = re.match(rf"^(\d+)(?:\(([a-z])\))?(?:\(({ROMAN_RE})\))?$", number)
    return Renumbering(board, int(m.group(1)), m.group(2), m.group(3), new_q)


# --------------------------------------------------------------------------
# Reading the characters in a slice
# --------------------------------------------------------------------------

@dataclass
class Char:
    c: str
    bbox: pymupdf.Rect
    origin: tuple
    size: float
    font: str
    bold: bool
    color: int


def words_in(src_page, clip):
    """Characters in the clip, grouped into words (split at spaces/gaps)."""
    words = []
    for block in src_page.get_text("rawdict", clip=clip)["blocks"]:
        for line in block.get("lines", []):
            current = []
            for span in line["spans"]:
                bold = bool(span["flags"] & 16) or "bold" in span["font"].lower()
                for ch in span["chars"]:
                    char = Char(ch["c"], pymupdf.Rect(ch["bbox"]), ch["origin"], span["size"],
                                span["font"], bold, span["color"])
                    gap = current and char.bbox.x0 - current[-1].bbox.x1 > 0.35 * char.size
                    if ch["c"].isspace() or gap:
                        if current:
                            words.append(current)
                        current = [] if ch["c"].isspace() else [char]
                    else:
                        current.append(char)
            if current:
                words.append(current)
    return [w for w in words if pymupdf.Rect(w[0].bbox).intersects(clip)]


def text_of(word):
    return "".join(ch.c for ch in word)


# --------------------------------------------------------------------------
# Fonts: reuse the exam PDF's own font
# --------------------------------------------------------------------------

class FontBox:
    """Finds and caches the embedded font buffers of a source document."""

    def __init__(self):
        self.cache = {}

    def buffer_for(self, src_page, fontname, text):
        """A font buffer that can draw `text`, matching the span's font name."""
        doc = src_page.parent
        base = fontname.split("+")[-1]
        key = (id(doc), base, text)
        if key in self.cache:
            return self.cache[key]
        found = None
        for xref, _ext, _typ, basefont, *_ in src_page.get_fonts(full=True):
            if basefont.split("+")[-1] != base:
                continue
            buf = doc.extract_font(xref)[-1]
            if buf and all(pymupdf.Font(fontbuffer=buf).has_glyph(ord(c)) for c in text if not c.isspace()):
                found = buf
                break
        self.cache[key] = found
        return found


class Writer:
    """Draws replacement text on an output page, embedding fonts once per page."""

    def __init__(self, fonts):
        self.fonts = fonts
        self.embedded = {}  # (page number, font buffer id) -> alias

    def draw(self, page, point, text, size, src_page, sample):
        buf = self.fonts.buffer_for(src_page, sample.font, text)
        color = tuple(((sample.color >> s) & 255) / 255 for s in (16, 8, 0))
        if buf:
            key = (page.number, id(buf))
            if key not in self.embedded:
                alias = f"EB{len(self.embedded)}"
                page.insert_font(fontname=alias, fontbuffer=buf)
                self.embedded[key] = alias
            page.insert_text(point, text, fontname=self.embedded[key], fontsize=size, color=color)
        else:
            page.insert_text(point, text, fontname="hebo" if sample.bold else "helv", fontsize=size, color=color)


# --------------------------------------------------------------------------
# Finding labels and replacing them
# --------------------------------------------------------------------------

@dataclass
class Replacement:
    chars: list      # the old characters to cover
    new: str         # the text to draw instead (at the first char's origin)
    in_box: bool = False  # AQA digit boxes: cover only inside the box


def find_replacements(words, r, doc_type):
    """Which words in a slice are labels to change, and to what."""
    reps = []
    if r.board == "aqa":
        reps += _aqa_replacements(words, r, doc_type)
    else:
        reps += _ocr_replacements(words, r, doc_type)
    return reps


def _aqa_replacements(words, r, doc_type):
    reps = []
    if doc_type == "qp":
        # The boxed label, e.g. chars "0" "3" "." "1": bold, left margin, on one line.
        chars = [ch for w in words for ch in w if ch.bold and ch.bbox.x0 < 135 and ch.c in "0123456789."]
        if not chars:
            return reps
        top = min(ch.bbox.y0 for ch in chars)
        line = sorted((ch for ch in chars if ch.bbox.y0 < top + 6), key=lambda ch: ch.bbox.x0)
        before = [ch for ch in line if ch.c.isdigit() and (not any(c.c == "." for c in line)
                                                          or ch.bbox.x0 < next(c for c in line if c.c == ".").bbox.x0)]
        after = [ch for ch in line if ch.c.isdigit() and ch not in before]
        if len(before) == 2 and int(before[0].c + before[1].c) == r.old_q:
            new_q = f"{r.new_q:02d}"
            reps += [Replacement([ch], new, in_box=True) for ch, new in zip(before, new_q)]
            if after and r.new_letter:
                reps.append(Replacement([after[0]], r.new_letter, in_box=True))
                reps += [Replacement([ch], "", in_box=True) for ch in after[1:]]
    else:
        # Mark scheme columns: "03" (Qu) then "1" (Part).
        for w in words:
            t = text_of(w)
            x = w[0].bbox.x0
            if x < 75 and re.fullmatch(r"\d{2}", t) and int(t) == r.old_q:
                reps.append(Replacement(w, f"{r.new_q:02d}"))
            elif 80 < x < 118 and r.old_letter and t == r.old_letter:
                reps.append(Replacement(w, r.new_letter))
    return reps


def _ocr_replacements(words, r, doc_type):
    reps = []
    num_x, part_x = (66, 110) if doc_type == "qp" else (90, 160)
    for w in words:
        t = text_of(w)
        x = w[0].bbox.x0
        bold_ok = w[0].bold or doc_type == "ms"
        if not bold_ok or x > part_x:
            continue
        m = re.fullmatch(r"(\d{1,2})(\*?)", t)
        if m and x < num_x and int(m.group(1)) == r.old_q:
            reps.append(Replacement(w, f"{r.new_q}{m.group(2)}"))
            continue
        m = re.fullmatch(r"\(([a-z]{1,4})\)(\*?)", t)
        if m:
            inner = m.group(1)
            if r.old_roman and inner == r.old_roman and not (inner == r.old_letter):
                reps.append(Replacement(w, f"({r.new_roman}){m.group(2)}"))
            elif r.old_letter and inner == r.old_letter:
                reps.append(Replacement(w, f"({r.new_letter}){m.group(2)}"))
            continue
        if doc_type == "ms":
            # Compact mark scheme labels like "3dii", or a bare roman "ii".
            m = re.fullmatch(rf"(\d{{1,2}})([a-h])?({ROMAN_RE})?(\*?)", t)
            if m and x < num_x and int(m.group(1)) == r.old_q and (m.group(2) or m.group(3)):
                new = f"{r.new_q}" + (r.new_letter or "" if m.group(2) else "") + \
                    ((r.new_roman or "") if m.group(3) else "") + m.group(4)
                reps.append(Replacement(w, new))
            elif r.old_roman and t == r.old_roman and 100 < x < 150:
                reps.append(Replacement(w, r.new_roman))
    return reps


def has_number(reps, r):
    """Did we find the question number itself (not just a part letter)?"""
    return any(rep.new.startswith(str(r.new_q)) or (r.board == "aqa" and rep.in_box) for rep in reps)


def apply(page, target, clip, src_page, r, doc_type, writer):
    """Replace the labels in one copied slice. Returns True if the question
    number itself was found in this slice."""
    words = words_in(src_page, clip)
    reps = find_replacements(words, r, doc_type)
    sx, sy = target.width / clip.width, target.height / clip.height

    def to_out(x, y):
        return target.x0 + (x - clip.x0) * sx, target.y0 + (y - clip.y0) * sy

    for rep in reps:
        first = rep.chars[0]
        size = first.size
        baseline = first.origin[1]
        x0 = min(ch.bbox.x0 for ch in rep.chars)
        x1 = max(ch.bbox.x1 for ch in rep.chars)
        if rep.in_box:
            # Only the glyph ink, so AQA's digit box lines stay visible.
            cover = pymupdf.Rect(x0 + 0.2, baseline - 0.75 * size, x1 - 0.2, baseline + 0.6)
        else:
            cover = pymupdf.Rect(x0 - 0.5, baseline - 0.8 * size, x1 + 0.5, baseline + 0.28 * size)
        ox0, oy0 = to_out(cover.x0, cover.y0)
        ox1, oy1 = to_out(cover.x1, cover.y1)
        page.draw_rect(pymupdf.Rect(ox0, oy0, ox1, oy1), color=None, fill=(1, 1, 1))
        if rep.new:
            px, py = to_out(first.origin[0], baseline)
            if rep.in_box:
                # Centre the new digit where the old one was.
                width = pymupdf.get_text_length(rep.new, fontname="hebo", fontsize=size * sx)
                px = ox0 + ((ox1 - ox0) - width) / 2
            writer.draw(page, (px, py), rep.new, size * sx, src_page, first)
    return has_number(reps, r)


def add_missing_number(page, point, r, size, writer, src_page, sample):
    """OCR: the question number wasn't printed in any copied slice (it was
    on a line we didn't include) - write it in the number column."""
    writer.draw(page, point, str(r.new_q), size, src_page, sample)
