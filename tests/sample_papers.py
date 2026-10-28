"""
Build small sample exam papers for the splitter tests.

Real exam papers are copyrighted, so instead of storing them in the repo we
draw miniature papers with PyMuPDF that copy the real layouts: the same
label positions, bold fonts, "[n marks]" placement, headers and footers.
Each one includes the awkward cases the splitter has to handle.

PyMuPDF's insert_text() takes the text *baseline* position, so a line drawn
at y=100 occupies roughly y=89..103.
"""
import pymupdf

A4 = (595, 842)
A4_LANDSCAPE = (842, 595)


def text(page, x, y, s, bold=False, size=11):
    page.insert_text((x, y), s, fontname="hebo" if bold else "helv", fontsize=size)


def answer_lines(page, x, y_from, y_to, char="_", step=26):
    y = y_from
    while y <= y_to:
        text(page, x, y, char * 75, size=9)
        y += step


def aqa_header_footer(page, number, turn_over=True):
    # AQA's grey "Do not write outside the box" frame around the page.
    page.draw_rect(pymupdf.Rect(40, 54, 538, 765), color=(0.58, 0.59, 0.6), width=0.5)
    text(page, 290, 40, str(number))
    text(page, 547, 60, "Do not write", size=7)
    text(page, 548, 67, "outside the", size=7)
    text(page, 560, 74, "box", size=7)
    if turn_over:
        text(page, 480, 775, "Turn over", bold=True)
    text(page, 45, 805, f"({number:02d})", size=14)


def build_aqa_qp(path):
    """AQA-style Paper 1 with 4 questions:

    1.1 [4]  plain question
    1.2 [6]  "Figure 1 is in the insert." printed ABOVE the label
    1.3 [9]  answer space runs onto the next page, then a blank page follows
    2.1 [4]  under a "Question 2 / Coastal systems and landscapes" heading
    """
    doc = pymupdf.open()
    cover = doc.new_page(width=A4[0], height=A4[1])
    text(cover, 45, 300, "A-level")
    text(cover, 45, 320, "GEOGRAPHY", bold=True)
    text(cover, 45, 700, "The total number of marks available for this paper is 120.")

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 1
    aqa_header_footer(p, 2)
    text(p, 264, 80, "Section A", bold=True)
    text(p, 224, 106, "Water and carbon cycles", bold=True)
    text(p, 201, 132, "Answer all questions in this section.")
    text(p, 51, 180, "0", bold=True)
    text(p, 68, 180, "1 . 1", bold=True)
    text(p, 116, 180, "Explain the concept of negative feedback within the carbon cycle.")
    text(p, 484, 195, "[4 marks]", bold=True)
    answer_lines(p, 115, 225, 400)
    text(p, 116, 560, "Figure 1 is in the insert.", bold=True)
    text(p, 116, 586, "Figure 1 shows changes in the water system.", bold=True)
    text(p, 51, 615, "0", bold=True)
    text(p, 68, 615, "1 . 2", bold=True)
    text(p, 116, 615, "Analyse the changes shown in Figure 1.")
    text(p, 484, 630, "[6 marks]", bold=True)
    answer_lines(p, 115, 660, 740)

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 2
    aqa_header_footer(p, 3, turn_over=False)
    text(p, 51, 100, "0", bold=True)
    text(p, 68, 100, "1 . 3", bold=True)
    text(p, 116, 100, "Assess the importance of the carbon cycle, either globally or locally.")
    text(p, 116, 113, "either way, use examples.")  # lower-case 'either' is NOT a heading
    text(p, 484, 128, "[9 marks]", bold=True)
    answer_lines(p, 115, 160, 740)

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 3: continuation
    aqa_header_footer(p, 4)
    answer_lines(p, 115, 80, 300)

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 4: blank
    aqa_header_footer(p, 5)
    text(p, 150, 400, "There are no questions printed on this page", bold=True)

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 5
    aqa_header_footer(p, 6)
    text(p, 45, 80, "Question 2", bold=True)
    text(p, 116, 80, "Coastal systems and landscapes", bold=True)
    text(p, 51, 110, "0", bold=True)
    text(p, 68, 110, "2 . 1", bold=True)
    text(p, 116, 110, "Outline factors leading to the formation of spits.")
    text(p, 484, 125, "[4 marks]", bold=True)
    answer_lines(p, 115, 155, 300)
    text(p, 230, 340, "END OF QUESTIONS", bold=True)
    doc.save(path)
    return path


def build_aqa_ms(path):
    """Matching AQA-style mark scheme: table with Qu / Part columns and rules."""
    doc = pymupdf.open()
    doc.new_page(width=A4[0], height=A4[1])               # cover
    p = doc.new_page(width=A4[0], height=A4[1])
    text(p, 230, 45, "MARK SCHEME – A-LEVEL GEOGRAPHY – 7037/1 – JUNE 2099", size=9)
    text(p, 58, 170, "Qu", bold=True)
    text(p, 90, 170, "Part", bold=True)
    text(p, 255, 170, "Marking guidance", bold=True)
    for y in (180, 330, 480, 700):
        p.draw_line((40, y), (556, y))
    text(p, 60, 200, "01")
    text(p, 98, 200, "1")
    text(p, 124, 200, "Negative feedback reduces change. 1 mark per valid point.")
    text(p, 60, 350, "01")
    text(p, 98, 350, "2")
    text(p, 124, 350, "AO3 - analysis of Figure 1. Level 2 (4-6 marks).")
    text(p, 60, 500, "01")
    text(p, 98, 500, "3")
    text(p, 124, 500, "Level 3 (7-9 marks): thorough assessment.")
    text(p, 42, 800, "2")                                  # page number: not a label!
    p = doc.new_page(width=A4[0], height=A4[1])
    text(p, 230, 45, "MARK SCHEME – A-LEVEL GEOGRAPHY – 7037/1 – JUNE 2099", size=9)
    text(p, 124, 90, "Level 1 (1-3 marks): basic assessment.")  # 01.3 continues
    p.draw_line((40, 110), (556, 110))
    text(p, 42, 130, "Section B", bold=True)
    text(p, 42, 156, "Question 2", bold=True)
    text(p, 60, 200, "02")
    text(p, 98, 200, "1")
    text(p, 124, 200, "Spits form by longshore drift.")
    p.draw_line((40, 240), (556, 240))
    doc.save(path)
    return path


def ocr_header_footer(page, number):
    text(page, 292, 52, str(number), bold=True)
    text(page, 485, 785, "Turn over", bold=True, size=10)
    text(page, 61, 790, "© OCR 2099", size=6)


def build_ocr_qp(path):
    """OCR-style Component 01 with:

    1        stem "A shop sells computer parts."
    1(a)     [1]
    1(b)     stem with code containing stock[10] (must not count as marks)
    1(b)(i)  [2]
    1(b)(ii) [3] answer space continues onto the next page, whose "[3]" sits
             low on that page (inside the footer zone)
    2(a)     [4] label printed near the top of its page (y≈54)
    """
    doc = pymupdf.open()
    cover = doc.new_page(width=A4[0], height=A4[1])
    text(cover, 60, 700, "The total mark for this paper is 140.")

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 1
    ocr_header_footer(p, 2)
    text(p, 50, 85, "1", bold=True)
    text(p, 72, 85, "A shop sells computer parts.")
    text(p, 72, 120, "(a) State one input device used in the shop.", bold=True)
    text(p, 95, 150, "." * 95)
    text(p, 532, 150, "[1]", bold=True)
    text(p, 72, 190, "(b) The shop stores its stock levels in an array:", bold=True)
    text(p, 95, 212, "array stock[10]")
    text(p, 96, 250, "(i) Describe what is meant by an array.", bold=True)
    text(p, 118, 280, "." * 90)
    text(p, 118, 306, "." * 90)
    text(p, 532, 306, "[2]", bold=True)
    text(p, 93, 345, "(ii) Explain how the shop could search the array.", bold=True)
    y = 375
    while y < 740:
        text(p, 118, y, "." * 90)
        y += 26

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 2: continuation
    ocr_header_footer(p, 3)
    y = 80
    while y < 760:
        text(p, 118, y, "." * 90)
        y += 26
    text(p, 532, 766, "[3]", bold=True)                   # low on the page

    p = doc.new_page(width=A4[0], height=A4[1])          # page index 3
    ocr_header_footer(p, 4)
    text(p, 50, 66, "2", bold=True)
    text(p, 72, 66, "(a) Explain recursion.", bold=True)  # label near the top
    text(p, 95, 100, "." * 95)
    text(p, 532, 100, "[4]", bold=True)
    text(p, 200, 200, "END OF QUESTION PAPER", bold=True)
    doc.save(path)
    return path


def build_ocr_ms(path, compact=False):
    """Matching OCR-style mark scheme (landscape table).

    compact=False writes labels like "1 (b) (i)" in bold columns;
    compact=True writes them like "1bi", not bold, vertically centred in each
    table cell (as some real OCR mark schemes do).
    """
    doc = pymupdf.open()
    p = doc.new_page(width=A4_LANDSCAPE[0], height=A4_LANDSCAPE[1])
    text(p, 47, 30, "H446/01", bold=True)
    text(p, 371, 30, "Mark Scheme", bold=True)
    text(p, 731, 30, "June 2099", bold=True)
    text(p, 80, 70, "Question", bold=True)
    rows = [  # (key parts, cell top, cell bottom, answer)
        (("1", "(a)", ""), 80, 140, "Keyboard / scanner / barcode reader"),
        (("1", "(b)", "(i)"), 140, 220, "A data structure holding items of the same type"),
        (("1", "", "(ii)"), 220, 320, "Linear search: check each item in turn"),
        (("2", "(a)", ""), 320, 420, "A function that calls itself, with a base case"),
    ]
    for (num, letter, roman), top, bottom, answer in rows:
        p.draw_line((40, top), (800, top))
        if compact:
            label = num + letter.strip("()") + roman.strip("()")
            text(p, 75, (top + bottom) / 2, label)               # centred, not bold
        else:
            text(p, 67, top + 15, num, bold=True)
            if letter:
                text(p, 97, top + 15, letter, bold=True)
            if roman:
                text(p, 132, top + 15, roman, bold=True)
        text(p, 160, top + 15, answer)
    p.draw_line((40, 420), (800, 420))
    text(p, 414, 550, "7")                                       # page number
    doc.save(path)
    return path


def build_aqa_figures_qp(path):
    """AQA-style paper testing pictures:

    1.1 [4]  plain question
    1.2 [6]  caption + photo (with a north arrow printed ON the photo) above the label
    1.3 [9]  caption + photo at the bottom of one page, label at the top of the next
    """
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40), False)
    photo.set_rect(photo.irect, (120, 160, 200))

    doc = pymupdf.open()
    doc.new_page(width=A4[0], height=A4[1])               # cover
    p = doc.new_page(width=A4[0], height=A4[1])           # index 1
    aqa_header_footer(p, 2)
    text(p, 51, 100, "0", bold=True)
    text(p, 68, 100, "1 . 1", bold=True)
    text(p, 116, 100, "Outline the formation of spits.")
    text(p, 484, 115, "[4 marks]", bold=True)
    answer_lines(p, 115, 145, 300)
    text(p, 116, 360, "Figure 2 is a photograph of a spit.", bold=True)
    text(p, 268, 384, "Figure 2", bold=True)
    p.insert_image(pymupdf.Rect(100, 395, 480, 640), pixmap=photo)
    text(p, 460, 420, "N")                                 # north arrow ON the photo
    text(p, 51, 680, "0", bold=True)
    text(p, 68, 680, "1 . 2", bold=True)
    text(p, 116, 680, "Using Figure 2, analyse the landform.")
    text(p, 484, 695, "[6 marks]", bold=True)
    answer_lines(p, 115, 720, 745)

    p = doc.new_page(width=A4[0], height=A4[1])           # index 2
    aqa_header_footer(p, 3)
    answer_lines(p, 115, 80, 300)                          # 1.2 answer space continues
    text(p, 116, 360, "Figure 3 is a photograph of a salt marsh.", bold=True)
    text(p, 268, 384, "Figure 3", bold=True)
    p.insert_image(pymupdf.Rect(100, 395, 480, 700), pixmap=photo)

    p = doc.new_page(width=A4[0], height=A4[1])           # index 3
    aqa_header_footer(p, 4)
    text(p, 51, 80, "0", bold=True)
    text(p, 68, 80, "1 . 3", bold=True)
    text(p, 116, 80, "Using Figure 3, assess how salt marshes develop.")
    text(p, 484, 95, "[9 marks]", bold=True)
    answer_lines(p, 115, 125, 400)
    text(p, 230, 440, "END OF QUESTIONS", bold=True)
    doc.save(path)
    return path


def build_aqa_insert(path):
    """A two-page insert; Figure 1 is on the second page (index 1)."""
    doc = pymupdf.open()
    p = doc.new_page(width=A4[0], height=A4[1])
    text(p, 200, 300, "Insert", bold=True, size=20)
    p = doc.new_page(width=A4[0], height=A4[1])
    text(p, 268, 80, "Figure 1", bold=True)
    text(p, 150, 100, "Changes in the water system, 2012-2016")
    p.draw_rect(pymupdf.Rect(100, 120, 495, 500), color=(0, 0, 0))
    doc.save(path)
    return path
