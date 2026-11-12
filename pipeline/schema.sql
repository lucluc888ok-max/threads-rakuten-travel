-- 楽天トラベル施設
CREATE TABLE IF NOT EXISTS hotels (
    hotel_no            INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    address             TEXT,
    review_count        INTEGER,
    review_average      REAL,
    ratings_json        TEXT,           -- 項目別評価 {"serviceAverage":4.2,...}
    min_charge          INTEGER,        -- 最安料金（取得時点）
    access              TEXT,
    nearest_station     TEXT,
    hotel_special       TEXT,           -- 施設特色
    facilities_json     TEXT,           -- 詳細情報の文字列項目
    image_url           TEXT,
    thumbnail_url       TEXT,
    info_url            TEXT,
    affiliate_url       TEXT,           -- affiliateId 付与で返る URL
    raw_json            TEXT,
    fetched_at          TEXT NOT NULL
);

-- スクショ上の表記 → 施設番号（人が確定したものを保存し次回から確認不要にする）
CREATE TABLE IF NOT EXISTS hotel_aliases (
    alias_name          TEXT PRIMARY KEY,
    hotel_no            INTEGER,        -- NULL = 楽天トラベルに該当なし（対象外）
    confirmed_by        TEXT NOT NULL,  -- 'auto' | 'human'
    confirmed_at        TEXT NOT NULL
);

-- 照合待ち（候補を人に提示する）
CREATE TABLE IF NOT EXISTS match_pending (
    alias_name          TEXT PRIMARY KEY,
    candidates_json     TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

-- ラ・クーポン
CREATE TABLE IF NOT EXISTS coupons (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    import_date         TEXT NOT NULL,  -- YYYYMMDD（「取り込み分」の単位）
    source_image        TEXT,
    hotel_name_raw      TEXT NOT NULL,
    hotel_no            INTEGER REFERENCES hotels(hotel_no),
    coupon_name         TEXT,
    discount_type       TEXT NOT NULL CHECK (discount_type IN ('yen', 'percent')),
    discount_value      REAL NOT NULL,
    min_amount          INTEGER,        -- 利用条件額（なしは NULL）
    acquire_deadline    TEXT,           -- 獲得期限 ISO8601
    stay_start          TEXT,           -- 宿泊対象期間 YYYY-MM-DD
    stay_end            TEXT,
    extraction_note     TEXT,           -- 読み取り時の注記（年を補った等）
    imported_at         TEXT NOT NULL,
    UNIQUE (hotel_name_raw, coupon_name, discount_type, discount_value, acquire_deadline)
);

-- 下書き
CREATE TABLE IF NOT EXISTS drafts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date            TEXT NOT NULL,
    hotel_no            INTEGER NOT NULL REFERENCES hotels(hotel_no),
    coupon_id           INTEGER REFERENCES coupons(id),
    variant             TEXT NOT NULL CHECK (variant IN ('A', 'B')),
    post1               TEXT NOT NULL,
    post2               TEXT NOT NULL,
    criteria_json       TEXT,           -- 満たした選定条件
    strength            TEXT,
    strength_evidence   TEXT,           -- 強みの根拠（項目名と原文）
    urgency_allowed     INTEGER NOT NULL,
    lint_json           TEXT,           -- 自動チェックの警告
    created_at          TEXT NOT NULL,
    UNIQUE (run_date, hotel_no, variant)
);

-- 投稿実績（投稿後に人が入力）
CREATE TABLE IF NOT EXISTS posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            INTEGER REFERENCES drafts(id),
    hotel_no            INTEGER NOT NULL REFERENCES hotels(hotel_no),
    adopted_variant     TEXT CHECK (adopted_variant IN ('A', 'B')),
    posted_at           TEXT NOT NULL,
    threads_url         TEXT,
    views               INTEGER,
    clicks              INTEGER,
    notes               TEXT,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posts_hotel ON posts (hotel_no, posted_at);
CREATE INDEX IF NOT EXISTS idx_coupons_import ON coupons (import_date);
