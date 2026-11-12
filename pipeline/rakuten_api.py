"""② 楽天トラベルAPIクライアント（標準ライブラリのみ）。

接続先・パラメータの根拠は config.py の出典メモを参照。
レスポンスは formatVersion 既定（1）の形
  {"hotels": [{"hotel": [{"hotelBasicInfo": {...}}, {"hotelRatingInfo": {...}}, ...]}]}
を想定し、セクションを1つの dict に平坦化して扱う。形が違っても落ちないよう防御的に読む。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class RakutenApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"楽天API エラー {status}: {message}")
        self.status = status
        self.message = message


class RakutenClient:
    def __init__(self, settings: config.Settings, opener=None, interval: float = config.API_INTERVAL_SEC):
        if not settings.app_id:
            raise RakutenApiError(0, "RAKUTEN_APP_ID が未設定です（README の設定手順を参照）")
        self.s = settings
        self._opener = opener or urllib.request.urlopen
        self._interval = interval
        self._last = 0.0

    def _get(self, api: str, params: dict) -> dict:
        q = {"format": "json", "applicationId": self.s.app_id}
        if self.s.affiliate_id:
            q["affiliateId"] = self.s.affiliate_id
        if self.s.access_key:
            q["accessKey"] = self.s.access_key
        q.update({k: v for k, v in params.items() if v not in (None, "")})
        url = f"{self.s.api_base}/{api}?{urllib.parse.urlencode(q)}"
        headers = {"Accept": "application/json", "User-Agent": "threads-rakuten-travel/1.0"}

        for attempt in range(4):
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(url, headers=headers)
            try:
                with self._opener(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                msg = _error_message(body)
                if e.code == 404 and "not_found" in body.lower():
                    return {"hotels": []}  # 該当なし
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RakutenApiError(e.code, msg) from None
        raise RakutenApiError(0, "リトライ上限に達しました")

    def keyword_search(self, keyword: str, hits: int = 10) -> list[dict]:
        raw = self._get(config.KEYWORD_SEARCH, {"keyword": keyword, "hits": hits})
        return [h for h in (flatten_hotel(w) for w in raw.get("hotels") or []) if h]

    def hotel_detail(self, hotel_no: int) -> dict | None:
        raw = self._get(config.HOTEL_DETAIL, {"hotelNo": hotel_no, "responseType": "large"})
        hotels = [h for h in (flatten_hotel(w) for w in raw.get("hotels") or []) if h]
        return hotels[0] if hotels else None


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body[:200]
    if isinstance(data.get("errors"), dict):
        return str(data["errors"].get("errorMessage"))
    return str(data.get("error_description") or data.get("error") or body[:200])


def flatten_hotel(wrapper) -> dict | None:
    """{"hotel":[{section:{...}},...]} → {"_sections": {...}, 各フィールド...}"""
    sections_list = wrapper.get("hotel") if isinstance(wrapper, dict) else wrapper
    if isinstance(sections_list, dict):
        sections_list = [sections_list]
    if not isinstance(sections_list, list):
        return None
    sections: dict[str, dict] = {}
    flat: dict = {}
    for item in sections_list:
        if not isinstance(item, dict):
            continue
        for name, body in item.items():
            if isinstance(body, dict):
                sections[name] = body
                for k, v in body.items():
                    flat.setdefault(k, v)
            elif isinstance(body, list):  # 一部APIは配列で返す
                sections[name] = {"_list": body}
    if "hotelNo" not in flat:
        return None
    flat["_sections"] = sections
    return flat


def section_strings(hotel: dict, exclude=("hotelBasicInfo", "hotelRatingInfo")) -> dict[str, str]:
    """強み抽出用: 詳細系セクションの文字列項目を {"section.key": value} で返す。"""
    out = {}
    for sec, body in (hotel.get("_sections") or {}).items():
        if sec in exclude:
            continue
        _collect_strings(body, sec, out)
    return out


def _collect_strings(obj, prefix: str, out: dict) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _collect_strings(v, f"{prefix}.{k}", out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _collect_strings(v, f"{prefix}[{i}]", out)
    elif isinstance(obj, str) and obj.strip():
        out[prefix] = obj.strip()
