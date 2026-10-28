-- ExamBuilder database schema (SQLite).
-- "IF NOT EXISTS" makes this safe to run every time the app starts.

-- One row per imported QP/MS pair. The importer checks this table so that
-- re-running it only processes papers it hasn't seen before.
CREATE TABLE IF NOT EXISTS source_papers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    subject      TEXT NOT NULL CHECK (subject IN ('geography', 'computer-science')),
    paper        TEXT NOT NULL,            -- 'P1', 'P2' (Geography) or 'C01', 'C02' (CS)
    series_key   TEXT NOT NULL,            -- as in the filename: '2022_June' or 'Specimen'
    series       TEXT NOT NULL,            -- display form: 'June 2022' or 'Specimen'
    year         INTEGER,                  -- NULL for specimens; used for sorting
    qp_filename  TEXT NOT NULL,
    ms_filename  TEXT,
    ins_filename TEXT,                     -- Geography insert (figures), if any
    total_marks  INTEGER,                  -- as printed on the cover
    imported_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (subject, paper, series_key)
);

-- One row per question part (e.g. Q1.1, or Q3(b)(ii)).
CREATE TABLE IF NOT EXISTS questions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_paper_id     INTEGER NOT NULL REFERENCES source_papers(id) ON DELETE CASCADE,
    subject             TEXT NOT NULL,
    paper               TEXT NOT NULL,
    series              TEXT NOT NULL,       -- display form, e.g. 'June 2022'
    question_number     TEXT NOT NULL,       -- as printed, e.g. '3.4' or '2(b)(i)'
    sort_order          INTEGER NOT NULL,    -- position within the original paper
    marks               INTEGER,             -- NULL if the importer couldn't read it
    topic               TEXT,
    subtopic            TEXT,
    tag_confidence      TEXT,                -- 'high' / 'medium' / 'low' / 'error' / NULL (not tagged)
    question_image      TEXT,                -- path relative to storage/questions
    markscheme_image    TEXT,                -- path relative to storage/markschemes
    question_regions    TEXT,                -- JSON [[page, x0, y0, x1, y1], ...] in the QP
    markscheme_regions  TEXT,                -- JSON, same format, in the MS
    insert_pages        TEXT,                -- JSON [page, ...] in the insert (Geography figures)
    question_text       TEXT,                -- extracted text (used for tagging/search)
    markscheme_text     TEXT,
    status              TEXT NOT NULL DEFAULT 'needs_review'
                        CHECK (status IN ('needs_review', 'approved')),
    review_reason       TEXT,                -- why the importer flagged it
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_paper_id, question_number)
);

-- Indexes on the columns the question bank filters by, so filtering stays fast.
CREATE INDEX IF NOT EXISTS idx_questions_filter
    ON questions (subject, status, topic, subtopic, paper);

-- Custom papers you build.
CREATE TABLE IF NOT EXISTS built_papers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject     TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which questions are in each built paper, and in what order.
-- This is a "join table": it links built_papers to questions (many-to-many).
CREATE TABLE IF NOT EXISTS built_paper_questions (
    built_paper_id  INTEGER NOT NULL REFERENCES built_papers(id) ON DELETE CASCADE,
    question_id     INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    PRIMARY KEY (built_paper_id, question_id)
);

-- Login attempts, used to lock the account after 5 failures (Stage 4).
-- Stored in the database (not in memory) so the lockout survives a restart.
CREATE TABLE IF NOT EXISTS login_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at  TEXT NOT NULL DEFAULT (datetime('now')),
    ip_address    TEXT,
    success       INTEGER NOT NULL
);

-- Small key/value settings changed from the website, e.g. a new password
-- hash from the Settings page (which overrides LOGIN_PASSWORD_HASH in .env,
-- because on Render the environment can't be edited from the app).
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- "I'm interested" form submissions (the one public page on the site).
CREATE TABLE IF NOT EXISTS interest (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    education   TEXT NOT NULL,
    message     TEXT,
    ip_address  TEXT,
    emailed     INTEGER NOT NULL DEFAULT 0,   -- 1 once the notification email was sent
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
