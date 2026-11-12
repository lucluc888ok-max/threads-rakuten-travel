#!/usr/bin/env python3
"""Threads × 楽天トラベル 投稿準備パイプライン。

  python run.py                 prepare を実行し、下書き案があれば finalize まで通す
  python run.py prepare         ①取り込み ②照合 ③選定 → data/candidates/YYYYMMDD.json
  python run.py finalize        ④下書き案を検査して保存 → drafts/YYYYMMDD.md
  python run.py resolve "名前" <施設番号|none>   照合待ちを確定
  python run.py record <下書きID> [--at "YYYY-MM-DD HH:MM"] [--url URL]
  python run.py stats <投稿ID> --views N --clicks N
  python run.py posts           投稿実績の一覧
  python run.py status          未処理の画像・照合待ちの確認

自動投稿は行わない。投稿とリンクの動作確認は人が手動で行う。
"""
from __future__ import annotations

import argparse
import json
import sys

from pipeline import config, db, drafter, ingest, matcher, record, render, selector
from pipeline.rakuten_api import RakutenApiError, RakutenClient


def cmd_prepare(conn, run_date: str, refresh: bool = True) -> int:
    waiting = ingest.pending_images()
    if waiting:
        print("■ まだ読み取っていないスクショがあります。Claude に読み取らせてください（/daily）:")
        for p in waiting:
            print(f"  - inbox/{p.name}")
        return 2

    stats = ingest.ingest_inbox(conn, run_date)
    print(f"■ ①取り込み: {stats['files']}ファイル / 新規 {stats['inserted']}件 / 重複 {stats['duplicates']}件")

    bdate = selector.batch_date(conn, run_date)
    if bdate is None:
        print("  取り込み済みのクーポンがありません。inbox/ にスクショを置いてください。")
        return 1
    names = [r["hotel_name_raw"] for r in conn.execute(
        "SELECT DISTINCT hotel_name_raw FROM coupons WHERE import_date = ? ORDER BY id", (bdate,))]

    try:
        client = RakutenClient(config.load_settings())
        res = matcher.match_all(conn, client, names, refresh_hotels=refresh)
    except RakutenApiError as e:
        print(f"■ ②照合で停止: {e}")
        if e.status == 403:
            print("  → アクセスキー（環境設定の accessKey）とアプリIDを確認。アプリにIP制限があると失敗します。")
        db.dump(conn)
        return 1
    print(f"■ ②照合: 確定 {len(res['matched'])}件 / 確認待ち {len(res['pending'])}件"
          f" / 該当なし {len(res['not_found'])}件 / 対象外 {len(res['skipped'])}件")
    for p in res["pending"]:
        print(f"  ? {p['name']}")
        for c in p["candidates"]:
            print(f"      {c['hotelNo']}  {c['hotelName']}（{c['address']}・口コミ{c['reviewCount']}件・一致度{c['score']}）")
    for n in res["not_found"]:
        print(f"  ✕ {n}（楽天トラベルで見つからない）")

    data = selector.select(conn, run_date)
    config.CANDIDATES.mkdir(parents=True, exist_ok=True)
    out = config.CANDIDATES / f"{run_date}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"■ ③選定: 候補 {len(data['candidates'])}件 / 除外 {len(data['excluded'])}件 → {out.relative_to(config.ROOT)}")
    for c in data["candidates"]:
        print(f"  ◎ {c['hotel']['name']}（{c['hotel_no']}）条件{c['criteria_met']}/3"
              f" 強み: {', '.join(s['label'] for s in c['strengths']) or 'なし'}")
    for e in data["excluded"]:
        print(f"  - {e['hotel_name']}: {e['reason']}")

    md = render.write(conn, run_date, data)
    db.dump(conn)
    print(f"■ 一覧（下書き前）: {md}")
    return 0


def cmd_finalize(conn, run_date: str) -> int:
    try:
        res = drafter.check_and_save(conn, run_date)
    except FileNotFoundError as e:
        print(f"■ ④下書き: {e}")
        return 1
    for w in res["warnings"]:
        print(f"  注意: {w}")
    if res["errors"]:
        print("■ ④下書き: ルール違反があるため保存しませんでした")
        for e in res["errors"]:
            print(f"  ✕ {e}")
        return 3
    md = render.write(conn, run_date, drafter.load_candidates(run_date))
    db.dump(conn)
    print(f"■ ④下書き: {res['saved']}案を保存 → {md}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="実行日 YYYYMMDD（既定: 今日 JST）")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("prepare")
    p.add_argument("--no-refresh", action="store_true", help="確定済みホテルの情報を再取得しない")
    sub.add_parser("finalize")
    p = sub.add_parser("resolve")
    p.add_argument("name")
    p.add_argument("hotel_no", help="施設番号、または none（該当なし）")
    p = sub.add_parser("record")
    p.add_argument("draft_id", type=int)
    p.add_argument("--at", help="投稿日時（既定: 現在）")
    p.add_argument("--url", help="Threads の投稿URL")
    p.add_argument("--notes")
    p = sub.add_parser("stats")
    p.add_argument("post_id", type=int)
    p.add_argument("--views", type=int)
    p.add_argument("--clicks", type=int)
    p.add_argument("--url")
    p.add_argument("--notes")
    sub.add_parser("posts")
    sub.add_parser("status")
    args = ap.parse_args(argv)

    run_date = args.date or config.today_str()
    conn = db.connect()
    try:
        if args.cmd in (None, "prepare"):
            rc = cmd_prepare(conn, run_date, refresh=not getattr(args, "no_refresh", False))
            if args.cmd is None and rc == 0 and (config.DRAFT_INPUT / f"{run_date}.json").exists():
                rc = cmd_finalize(conn, run_date)
            return rc
        if args.cmd == "finalize":
            return cmd_finalize(conn, run_date)
        if args.cmd == "resolve":
            hotel_no = None if args.hotel_no.lower() == "none" else int(args.hotel_no)
            if hotel_no is not None:
                matcher.fetch_and_save_hotel(conn, RakutenClient(config.load_settings()), hotel_no)
            matcher.set_alias(conn, args.name, hotel_no, "human")
            db.dump(conn)
            print(f"確定: {args.name} → {hotel_no or '該当なし'}（次に python run.py prepare）")
            return 0
        if args.cmd == "record":
            pid = record.record_post(conn, args.draft_id, args.at, args.url, args.notes)
            db.dump(conn)
            print(f"投稿ID {pid} を記録しました。数値は python run.py stats {pid} --views N --clicks N")
            return 0
        if args.cmd == "stats":
            record.update_stats(conn, args.post_id, args.views, args.clicks, args.url, args.notes)
            db.dump(conn)
            print(f"投稿ID {args.post_id} を更新しました")
            return 0
        if args.cmd == "posts":
            rows = record.list_posts(conn)
            print("ID | 投稿日時 | ホテル | 案 | 閲覧 | クリック | CTR% | 下書きID")
            for r in rows:
                print(f"{r['id']} | {r['posted_at'][:16]} | {r['name']} | {r['adopted_variant']} | "
                      f"{r['views'] if r['views'] is not None else '-'} | "
                      f"{r['clicks'] if r['clicks'] is not None else '-'} | {r['ctr'] or '-'} | {r['draft_id']}")
            return 0
        if args.cmd == "status":
            imgs = ingest.pending_images()
            print(f"未読み取りの画像: {len(imgs)}件 " + " ".join(p.name for p in imgs))
            print(f"取り込み待ちJSON: {len(list(config.INBOX.glob('*' + ingest.SIDECAR_SUFFIX)))}件")
            for r in conn.execute("SELECT alias_name FROM match_pending"):
                print(f"照合待ち: {r['alias_name']}")
            return 0
    except (ValueError, RuntimeError) as e:
        print(f"エラー: {e}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
