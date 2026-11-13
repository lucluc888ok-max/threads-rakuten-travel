"""drafts/YYYYMMDD.md の出力。"""
from __future__ import annotations

import json
import sqlite3

from . import config


def _yen(v) -> str:
    return "—" if v is None else f"{int(v):,}円"


def _n(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _discount(c: dict) -> str:
    v = _n(c["discount_value"])
    return f"{v:,}円OFF" if c["discount_type"] == "yen" else f"{v}%OFF"


def render(conn: sqlite3.Connection, run_date: str, cand_data: dict, pending: list[dict]) -> str:
    drafts = {(r["hotel_no"], r["variant"]): dict(r) for r in conn.execute(
        "SELECT * FROM drafts WHERE run_date = ?", (run_date,))}
    d = run_date
    lines = [f"# {d[:4]}-{d[4:6]}-{d[6:]} Threads 下書き", ""]
    lines.append(f"- 生成: {cand_data['generated_at']} / 対象クーポン: {cand_data['batch_date']} 取り込み分")
    th = cand_data.get("thresholds", {})
    if th:
        parts = []
        if "yen" in th:
            parts.append(f"{int(th['yen']):,}円以上")
        if "percent" in th:
            parts.append(f"{_n(th['percent'])}%以上")
        lines.append(f"- 割引上位の基準: {' / '.join(parts)}")
    lines.append(f"- 候補 {len(cand_data['candidates'])} 件 / 除外 {len(cand_data['excluded'])} 件"
                 f" / 照合待ち {len(pending)} 件")
    lines.append("- 投稿後: `python run.py record <下書きID> --at \"YYYY-MM-DD HH:MM\"`")
    lines.append("")

    if pending:
        lines += ["## ⚠ ホテル照合の確認待ち", ""]
        for p in pending:
            lines.append(f"### {p['alias_name']}")
            cands = json.loads(p["candidates_json"])
            if not cands:
                lines.append("- 楽天トラベルで候補が見つかりません → `python run.py resolve \"名前\" none`")
            for c in cands:
                lines.append(f"- `{c['hotelNo']}` {c['hotelName']}（{c['address']}・口コミ{c['reviewCount']}件・一致度{c['score']}）")
            lines.append("")

    for i, c in enumerate(cand_data["candidates"], 1):
        h, cp = c["hotel"], c["coupon"]
        lines.append(f"## {i}. {h['name']}（施設番号 {c['hotel_no']}）")
        lines.append("")
        lines.append(f"- クーポン: {cp.get('coupon_name') or '（名称なし）'} / **{_discount(cp)}**"
                     f" / 利用条件 {_yen(cp.get('min_amount')) if cp.get('min_amount') else 'なし'}")
        lines.append(f"- 獲得期限: {cp['acquire_deadline']}（残り{c['days_to_deadline']}日）"
                     f" / 宿泊対象: {cp.get('stay_start') or '?'} 〜 {cp.get('stay_end') or '?'}")
        rc = h.get("review_count")
        lines.append(f"- 口コミ: {f'{rc:,}' if isinstance(rc, int) else rc}件 ★{h.get('review_average')}"
                     f" / 最安料金: {_yen(h.get('min_charge'))}（{(h.get('min_charge_fetched_at') or '')[:10]} 取得時点）")
        lines.append(f"- アクセス: {h.get('access') or '—'}")
        crit = c["criteria"]
        lines.append(f"- 選定条件 {c['criteria_met']}/3: 口コミ500件以上{'○' if crit['reviews_500plus'] else '×'}"
                     f" / 割引上位{'○' if crit['top_discount'] else '×'} / 強みあり{'○' if crit['has_strength'] else '×'}")
        lines.append(f"- 急かす表現: {'使用可' if c['urgency_allowed'] else '使用不可'}")
        # 楽天トラベルの規約: 施設画像（API取得分も含む）の SNS 投稿は不可。md にも載せない
        if not h.get("affiliate_url"):
            lines.append("- ⚠ アフィリエイトURLが取得できていません。リンクの動作確認時に要確認")
        if c["strengths"]:
            lines.append("- 強みと根拠:")
            for s in c["strengths"]:
                ev = " / ".join(f"{e['field']}「{e['text']}」" for e in s["evidence"])
                lines.append(f"  - **{s['label']}**: {ev}")
        lines.append("")
        for v, title in (("A", "型A：割引訴求"), ("B", "型B：特徴訴求")):
            dr = drafts.get((c["hotel_no"], v))
            if not dr:
                lines += [f"### {title}", "", "（未作成）", ""]
                continue
            lines += [f"### {title}　下書きID {dr['id']}", ""]
            if dr.get("strength"):
                lines.append(f"根拠メモ: {dr['strength']} ← {dr.get('strength_evidence') or ''}")
                lines.append("")
            for w in json.loads(dr.get("lint_json") or "[]"):
                lines.append(f"> 注意: {w}")
            lines += ["**1投稿目**", "", "```", dr["post1"], "```", "",
                      "**2投稿目**", "", "```", dr["post2"], "```", ""]

    if cand_data["excluded"]:
        lines += ["## 除外", "", "| ホテル | 理由 |", "|---|---|"]
        for e in cand_data["excluded"]:
            lines.append(f"| {e['hotel_name']} | {e['reason']} |")
        lines.append("")
    return "\n".join(lines)


def write(conn: sqlite3.Connection, run_date: str, cand_data: dict) -> str:
    pending = [dict(r) for r in conn.execute("SELECT * FROM match_pending ORDER BY alias_name")]
    config.DRAFTS.mkdir(parents=True, exist_ok=True)
    path = config.DRAFTS / f"{run_date}.md"
    path.write_text(render(conn, run_date, cand_data, pending), encoding="utf-8")
    return str(path.relative_to(config.ROOT))
