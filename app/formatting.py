"""Small display helpers shared by the website and the PDF export."""


def question_label(subject, number):
    """A question number written the way that exam board writes it.

    AQA uses two-digit question numbers: stored "3.1" is shown as "03.1"
    (printed on the paper as "0 3 . 1"). OCR numbers like "2(b)(i)" are
    already in OCR's style.
    """
    if subject == "geography":
        main, _, part = number.partition(".")
        main = main.zfill(2) if main.isdigit() else main
        return f"{main}.{part}" if part else main
    return number
