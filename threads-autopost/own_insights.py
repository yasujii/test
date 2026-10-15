#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自分の投稿の表示回数を公式 Threads API で取得する（読み取り専用・投稿は一切しない）。

- GET /me/threads で SINCE 以降の自分の投稿（ツリーの1枚目）を全件取得
- 各投稿の GET /{id}/insights で views / likes / replies / reposts / quotes / shares を取得
- 結果は表示回数の多い順に ROW 行（タブ区切り）でログに出す

スクレイピングではなく、本人のトークンによる公式APIの読み取りのみ。
他人の投稿の表示回数は公式APIでは取れない（research_threads.py の実地調査で確認済み）。
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

API = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
SINCE = os.environ.get("SINCE", "2026-07-01")
METRICS_FULL = "views,likes,replies,reposts,quotes,shares"
METRICS_MIN = "views,likes,replies,reposts,quotes"
JST = timezone(timedelta(hours=9))


def mask(s):
    return s.replace(TOKEN, "<TOKEN>") if TOKEN else s


def get(path, params):
    """GET を叩いて (ok, data_or_errtext) を返す。"""
    url = f"{API}/{path}?" + urllib.parse.urlencode(dict(params, access_token=TOKEN))
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, mask(f"HTTP {e.code} {e.read().decode('utf-8', 'ignore')[:400]}")
    except Exception as e:
        return False, mask(f"{type(e).__name__}: {e}")


def list_posts():
    params = {"fields": "id,text,timestamp,media_type,is_quote_post", "limit": 100, "since": SINCE}
    posts = []
    ok, data = get("me/threads", params)
    while ok:
        posts += data.get("data", [])
        after = data.get("paging", {}).get("cursors", {}).get("after")
        if not data.get("paging", {}).get("next") or not after:
            break
        ok, data = get("me/threads", dict(params, after=after))
        time.sleep(0.3)
    if not ok:
        print(f"[ERROR] 投稿一覧の取得に失敗: {data}")
    return posts


def insights(media_id, metrics):
    ok, data = get(f"{media_id}/insights", {"metric": metrics})
    if not ok:
        return None, data
    out = {}
    for m in data.get("data", []):
        v = None
        if m.get("values"):
            v = m["values"][0].get("value")
        elif m.get("total_value"):
            v = m["total_value"].get("value")
        out[m.get("name")] = v
    return out, None


def jst(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(JST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts or ""


def clean(text):
    return (text or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")[:140]


def main():
    if not TOKEN:
        print("[ERROR] THREADS_ACCESS_TOKEN 未設定")
        sys.exit(1)

    ok, me = get("me", {"fields": "id,username"})
    if not ok:
        print(f"[ERROR] トークン確認に失敗（期限切れの可能性）: {me}")
        sys.exit(1)
    print(f"account=@{me.get('username', '?')}")

    posts = list_posts()
    print(f"posts_since_{SINCE}={len(posts)}")

    metrics, rows, errors = METRICS_FULL, [], 0
    for p in posts:
        if p.get("media_type") == "REPOST_FACADE":
            continue
        got, err = insights(p["id"], metrics)
        if got is None and metrics == METRICS_FULL:
            # shares 非対応の環境では最小セットで取り直す
            metrics = METRICS_MIN
            got, err = insights(p["id"], metrics)
        if got is None:
            errors += 1
            if errors <= 3:
                print(f"[WARN] insights 取得失敗: {err}")
            continue
        rows.append((jst(p.get("timestamp")), got, p.get("media_type", ""), clean(p.get("text"))))
        time.sleep(0.2)

    rows.sort(key=lambda r: r[1].get("views") or 0, reverse=True)
    print("COLS\tdate_jst\tviews\tlikes\treplies\treposts\tquotes\tshares\tmedia\ttext")
    for date, m, media, text in rows:
        vals = [m.get(k) for k in ("views", "likes", "replies", "reposts", "quotes", "shares")]
        print("ROW\t" + "\t".join([date] + ["" if v is None else str(v) for v in vals] + [media, text]))

    views = [r[1].get("views") or 0 for r in rows]
    print("===== SUMMARY =====")
    print(f"insights_ok={len(rows)} insights_error={errors} metrics={metrics}")
    for th in (100000, 10000, 5000, 1000):
        print(f"views>={th}: {sum(v >= th for v in views)}")


if __name__ == "__main__":
    main()
