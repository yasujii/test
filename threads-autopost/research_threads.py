#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Threads API 実地調査（読み取り専用・投稿は一切しない）。

目的: GitHub Secret の THREADS_ACCESS_TOKEN を使って、Threads Graph API で
「他人のバズ投稿（いいね100以上を100件）」が実際に取得できるのかを empirically に確かめる。
推測ではなく、APIが今日返す生の結果／エラーをそのままログに出す。

試すこと:
  1) /me でトークンの有効性とアカウント確認
  2) keyword_search (search_type=TOP) を主要キーワードで実行 → 何が返るか
  3) 返ったメディアに like_count 等の指標が付くか（他人の投稿のいいね数が取れるか）
  4) 取れる/取れないを結論として出力
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error, time

API = "https://graph.threads.net/v1.0"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
KEYWORDS = ["腸活", "便秘", "発酵食品", "消化器内科", "更年期 腸活"]
PER_KW = 15


def call(path, params, label):
    """GET を叩いて (ok, data_or_errtext) を返す。生の結果を必ずログに出す。"""
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    safe = url.replace(TOKEN, "<TOKEN>")
    print(f"\n----- {label} -----")
    print(f"GET {safe}")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))
            print("[HTTP 200]")
            return True, body
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "ignore")
        print(f"[HTTP {e.code}] {txt[:600]}")
        return False, txt
    except Exception as e:
        print(f"[EXC] {type(e).__name__}: {e}")
        return False, str(e)


def main():
    if not TOKEN:
        print("[ERROR] THREADS_ACCESS_TOKEN 未設定")
        sys.exit(1)

    # 1) トークン確認
    ok, me = call("me", {"fields": "id,username", "access_token": TOKEN}, "1) /me トークン確認")
    if ok:
        print(f"  -> user_id={me.get('id')} username=@{me.get('username','?')}")

    # 2) & 3) キーワード検索と、他人投稿の指標が取れるか
    got_any = False
    got_likes = False
    for kw in KEYWORDS:
        # まず欲張って like_count 等の指標フィールドを要求してみる（取れれば理想）
        fields_rich = "id,username,text,permalink,timestamp,like_count"
        ok, data = call(
            "keyword_search",
            {"q": kw, "search_type": "TOP", "fields": fields_rich,
             "limit": PER_KW, "access_token": TOKEN},
            f"2) keyword_search TOP q='{kw}' (指標つきで要求)",
        )
        if not ok:
            # 指標フィールドが原因で弾かれた可能性 → 最小フィールドで再試行
            ok2, data2 = call(
                "keyword_search",
                {"q": kw, "search_type": "TOP",
                 "fields": "id,username,text,permalink,timestamp",
                 "limit": PER_KW, "access_token": TOKEN},
                f"2b) keyword_search TOP q='{kw}' (最小フィールドで再試行)",
            )
            data = data2 if ok2 else data
            ok = ok2
        if ok and isinstance(data, dict):
            items = data.get("data", [])
            print(f"  -> {len(items)} 件ヒット")
            for it in items[:PER_KW]:
                got_any = True
                like = it.get("like_count")
                if like is not None:
                    got_likes = True
                text = (it.get("text") or "").replace("\n", " ")[:80]
                print(f"     @{it.get('username','?')} likes={like} | {text}")
        time.sleep(1)

    # 4) 結論
    print("\n===== 結論 =====")
    print(f"他人の投稿がキーワード検索で取れたか: {'YES' if got_any else 'NO'}")
    print(f"他人の投稿の like_count（いいね数）が取れたか: {'YES' if got_likes else 'NO'}")
    if got_any and got_likes:
        print("→ APIだけで『いいね100以上を100件』の一次収集が可能。次はページングで100件収集＋100以上フィルタを実装する。")
    elif got_any and not got_likes:
        print("→ 投稿本文は取れるが、いいね数がAPIに付かない。『いいね100以上』の厳密フィルタはAPIでは不可。TOP検索の人気順を代理指標に使うか、ブラウザ収集（threads-topic-research）が必要。")
    else:
        print("→ keyword_search 自体が使えない（権限不足/未承認の可能性）。上のエラー本文が原因。トークンに threads_keyword_search 権限＋アプリ審査が必要か、ブラウザ収集に切り替える。")


if __name__ == "__main__":
    main()
