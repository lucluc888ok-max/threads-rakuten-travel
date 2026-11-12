"""設定と .env 読み込み（外部ライブラリ不使用）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
JST = ZoneInfo("Asia/Tokyo")

INBOX = ROOT / "inbox"
INBOX_DONE = INBOX / "done"
DATA = ROOT / "data"
EXTRACTED = DATA / "extracted"      # 取り込み済みクーポンJSONの控え
CANDIDATES = DATA / "candidates"    # prepare の出力（下書き作成用の事実データ）
DRAFT_INPUT = DATA / "drafts_input"  # Claude が書いた下書き案（finalize の入力）
DRAFTS = ROOT / "drafts"
DB_PATH = DATA / "app.db"
DB_DUMP = DATA / "db_dump.sql"      # git で永続化するテキストダンプ
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
CAPITAL_PREFS = ("東京都", "神奈川県", "千葉県", "埼玉県")

# ---- 選定ルール ----
MIN_REVIEW_COUNT = 500          # 条件1: 口コミ件数
TOP_DISCOUNT_RATIO = 0.3        # 条件2: 円・％それぞれ取り込み分の上位30%（最低1件）
REPOST_BLOCK_DAYS = 14          # 過去N日以内に投稿済みのホテルは除外
URGENCY_MAX_DAYS = 7            # 獲得期限までN日以内のときだけ急かす表現を許可

# ---- 下書きチェック ----
POST1_MIN_CHARS = 70
POST1_MAX_CHARS = 130
URGENCY_WORDS = (
    "満室前に", "残りわずか", "残り僅か", "お早めに", "急いで", "今すぐ", "いますぐ",
    "なくなり次第", "無くなり次第", "売り切れ", "売切れ", "先着", "見逃し厳禁", "ラストチャンス",
)

# ---- 楽天API ----
# 出典メモ: 公式ドキュメント(webservice.rakuten.co.jp)はクラウド環境から503で閲覧不可だったため、
# 2026-11-12 に実APIへ疎通確認した結果と非公式パッケージ rakuten-mcp 1.3.0 の実装を根拠にしている。
#   - 接続先: https://openapi.rakuten.co.jp/engine/api/Travel/<API名>/<版>
#   - applicationId はクエリ必須、accessKey はクエリ or ヘッダー "accessKey"
# 公式ドキュメントで変更を確認したら RAKUTEN_API_BASE / 各バージョンを更新すること。
DEFAULT_API_BASE = "https://openapi.rakuten.co.jp/engine/api/Travel"
KEYWORD_SEARCH = "KeywordHotelSearch/20170426"
HOTEL_DETAIL = "HotelDetailSearch/20170426"
API_INTERVAL_SEC = 1.1          # 連続リクエストの間隔


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)  # 既存の環境変数を優先


@dataclass
class Settings:
    app_id: str = ""
    access_key: str = ""    # 空なら送らない（クラウドではプロキシがヘッダーを付与する）
    affiliate_id: str = ""
    api_base: str = DEFAULT_API_BASE
    extra: dict = field(default_factory=dict)


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    return Settings(
        app_id=os.environ.get("RAKUTEN_APP_ID", "").strip(),
        access_key=os.environ.get("RAKUTEN_ACCESS_KEY", "").strip(),
        affiliate_id=os.environ.get("RAKUTEN_AFFILIATE_ID", "").strip(),
        api_base=os.environ.get("RAKUTEN_API_BASE", DEFAULT_API_BASE).rstrip("/"),
    )


def now() -> datetime:
    return datetime.now(JST)


def today_str() -> str:
    return now().strftime("%Y%m%d")
