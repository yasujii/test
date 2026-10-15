#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
長期トークンで「他人の投稿」と「他人の投稿の表示回数」が取れるかを、実際に叩いて確かめる実験。
（読み取り専用・投稿／いいね／フォローは一切しない。GET のみ）

公開リポジトリの Actions ログは誰でも見られるので、他人の投稿の本文やアカウント名は出さない。
出すのは「件数」「返ってきた項目の名前」「表示回数が取れたか（1万回以上があったか）」だけ。

試すこと:
  0) /me と debug_token（トークンの許可の中身・期限）
  1) keyword_search：腸活・便秘 × 人気順(TOP)・新着順(RECENT)、タグ検索(TAG)
     → 他人の投稿が何件混ざるか。表示回数などの項目を要求したらどうなるか
  2) profile_lookup：@threads（審査前でも見られる公式4アカウントの1つ）と @zuck（一般の公開アカウント）
  3) profile_posts：@threads の投稿一覧
  4) 他人の投稿1本に対して、項目指定・insights（表示回数）・oEmbed で数字を取りにいく
  5) 比較：自分の投稿1本の insights（表示回数）
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

API = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
RICH = "views,like_count,likes,reply_count,replies,repost_count,reposts,quote_count,quotes,view_count"
BASIC = "id,username,text,timestamp,permalink,media_type"
results = {}


def mask(s):
    return s.replace(TOKEN, "<TOKEN>") if TOKEN else s


def call(path, params, label):
    """GET して (ok, data_or_errtext)。URL・トークンは出さず、パスとパラメータ名だけ出す。"""
    time.sleep(1)
    q = dict(params, access_token=TOKEN)
    url = f"{API}/{path}?" + urllib.parse.urlencode(q)
    shown = {k: v for k, v in params.items()}
    print(f"\n----- {label} -----\nGET /{path} {shown}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            print("[HTTP 200]")
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        txt = mask(e.read().decode("utf-8", "ignore"))
        try:
            msg = json.loads(txt).get("error", {}).get("message", txt)
        except Exception:
            msg = txt
        print(f"[HTTP {e.code}] {msg[:300]}")
        return False, msg
    except Exception as e:
        print(f"[EXC] {type(e).__name__}: {mask(str(e))[:300]}")
        return False, str(e)


def keys_of(items):
    ks = set()
    for it in items:
        ks.update(it.keys())
    return sorted(ks)


def num_over(d, keys, th=10000):
    """d の中の数字の項目のうち、th 以上のものがあったか（値そのものは出さない）"""
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and v >= th:
            return True
    return False


def insight_values(data):
    out = {}
    for m in (data or {}).get("data", []):
        v = None
        if m.get("values"):
            v = m["values"][0].get("value")
        elif m.get("total_value"):
            v = m["total_value"].get("value")
        out[m.get("name")] = v
    return out


def main():
    if not TOKEN:
        print("[ERROR] THREADS_ACCESS_TOKEN が空")
        sys.exit(1)

    # 0) トークンの確認
    ok, me = call("me", {"fields": "id,username"}, "0) /me 自分のアカウント")
    if not ok:
        print("トークンが使えないので終了")
        sys.exit(1)
    my_id, my_name = me.get("id"), me.get("username")
    print(f"  -> 自分: @{my_name}")

    ok, dbg = call("debug_token", {"input_token": TOKEN}, "0b) debug_token トークンの許可と期限")
    if ok:
        d = dbg.get("data", dbg)
        exp = d.get("expires_at")
        exp_s = datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%d") if isinstance(exp, int) and exp else exp
        print(f"  -> 有効: {d.get('is_valid')} / 許可: {d.get('scopes')} / 期限: {exp_s}")
        results["scopes"] = d.get("scopes")

    # 1) キーワード検索
    other_ids = []
    total_other = 0
    for q, mode in [("腸活", "KEYWORD"), ("便秘", "KEYWORD"), ("腸活", "TAG")]:
        for st in ["TOP", "RECENT"]:
            p = {"q": q, "search_type": st, "fields": BASIC, "limit": 100}
            if mode == "TAG":
                p["search_mode"] = "TAG"
            ok, data = call("keyword_search", p, f"1) 検索 q={q} {mode} {st}")
            if ok:
                items = data.get("data", [])
                others = [it for it in items if it.get("username") != my_name]
                authors = {it.get("username") for it in others}
                total_other += len(others)
                other_ids += [it["id"] for it in others if it.get("id")]
                print(f"  -> {len(items)}件（自分 {len(items) - len(others)}件／他人 {len(others)}件・{len(authors)}アカウント）"
                      f" 項目: {keys_of(items)} 次ページ: {'あり' if data.get('paging', {}).get('next') else 'なし'}")
    results["search_other"] = total_other

    ok, data = call("keyword_search", {"q": "腸活", "search_type": "TOP", "fields": BASIC + "," + RICH, "limit": 25},
                    "1b) 検索で表示回数・いいね数などの項目も要求")
    if ok:
        items = data.get("data", [])
        print(f"  -> {len(items)}件 返ってきた項目: {keys_of(items)}")

    # 2) 他人のプロフィール
    pf = "username,name,is_verified,follower_count,likes_count,quotes_count,reposts_count,replies_count,views_count"
    for u in ["threads", "zuck"]:
        ok, prof = call("profile_lookup", {"username": u, "fields": pf}, f"2) profile_lookup @{u}")
        if ok:
            print(f"  -> 返ってきた項目: {sorted(prof.keys())}"
                  f" / 直近7日の表示回数(views_count)の項目: {'あり' if 'views_count' in prof else 'なし'}"
                  f" / 1万以上か: {num_over(prof, ['views_count'])}")
            results[f"profile_{u}"] = "views_count" in prof

    # 3) 他人の投稿一覧
    ok, data = call("profile_posts", {"username": "threads", "fields": BASIC, "limit": 25}, "3) profile_posts @threads")
    if ok:
        items = data.get("data", [])
        print(f"  -> {len(items)}件 項目: {keys_of(items)}")
        other_ids = [it["id"] for it in items if it.get("id")] + other_ids
        results["profile_posts"] = len(items)
    ok, data = call("profile_posts", {"username": "threads", "fields": BASIC + "," + RICH, "limit": 5},
                    "3b) profile_posts で表示回数・いいね数などの項目も要求")
    if ok:
        print(f"  -> 項目: {keys_of(data.get('data', []))}")

    # 4) 他人の投稿1本の数字を取りにいく
    results["other_views"] = False
    if other_ids:
        mid = other_ids[0]
        ok, d = call(mid, {"fields": BASIC + "," + RICH}, "4a) 他人の投稿1本：項目指定で表示回数などを要求")
        if ok:
            print(f"  -> 項目: {sorted(d.keys())} / 1万以上の数字: {num_over(d, RICH.split(','))}")
            if any(k in d for k in ("views", "view_count")):
                results["other_views"] = True
        ok, d = call(f"{mid}/insights", {"metric": "views,likes,replies,reposts,quotes,shares"},
                     "4b) 他人の投稿1本：insights（表示回数）")
        if ok:
            vals = insight_values(d)
            print(f"  -> 返ってきた指標: {sorted(vals.keys())} / 表示回数あり: {vals.get('views') is not None}"
                  f" / 1万以上: {isinstance(vals.get('views'), int) and vals['views'] >= 10000}")
            if vals.get("views") is not None:
                results["other_views"] = True
        ok, d = call(mid, {"fields": "permalink"}, "4c) 他人の投稿1本：URLを取得（oEmbed用）")
        if ok and d.get("permalink"):
            ok2, o = call("oembed", {"url": d["permalink"]}, "4d) oEmbed（埋め込み用データ）")
            if ok2:
                html = o.get("html", "")
                print(f"  -> 項目: {sorted(o.keys())} / 埋め込みHTMLに『views』『表示』の文字: "
                      f"{'views' in html.lower() or '表示' in html}")
    else:
        print("\n4) 他人の投稿のIDが1件も取れなかったので、他人の投稿の数字は試せず")

    # 5) 比較：自分の投稿
    ok, data = call("me/threads", {"fields": "id,timestamp", "limit": 1}, "5) 自分の最新投稿")
    if ok and data.get("data"):
        ok2, d = call(f"{data['data'][0]['id']}/insights", {"metric": "views,likes"}, "5b) 自分の投稿の insights")
        if ok2:
            print(f"  -> 自分の投稿の表示回数: {insight_values(d).get('views')}")

    # 結論
    print("\n===== 結論 =====")
    print(f"トークンの許可: {results.get('scopes', '（debug_tokenで取れず）')}")
    print(f"他人の投稿がキーワード検索で取れたか: {'YES' if results.get('search_other') else 'NO'}（他人 {results.get('search_other', 0)}件）")
    print(f"他人（@threads）の投稿一覧が取れたか: {'YES' if results.get('profile_posts') else 'NO'}")
    print(f"一般アカウント（@zuck）のプロフィールが取れたか: {'YES' if 'profile_zuck' in results else 'NO'}")
    print(f"他人の投稿ごとの表示回数が取れたか: {'YES' if results.get('other_views') else 'NO'}")


if __name__ == "__main__":
    main()
