#!/usr/bin/env python3
"""
桜腸活 Threads 直接投稿スクリプト（GitHub Actions から定期実行する用）

やること:
  - threads_posts.json を読み、いま公開すべき投稿（予約時刻を過ぎたが未投稿のもの）を
    Threads Graph API で公開する。
  - type=tree はスレッド（2枚目以降を返信で連結）、single は1投稿。
  - 二重投稿を防ぐため、公開済みIDを posted_log.json に記録する。

トークンの渡し方（重要・コードに直書きしない）:
  - 環境変数 THREADS_ACCESS_TOKEN から読む。GitHub Actions では「Secrets」で渡す。
  - ユーザーIDは環境変数 THREADS_USER_ID があれば使う。なければトークンから自動取得する。

安全:
  - 予約時刻より前のものは投稿しない（未来分は待つ）。
  - 予約時刻から CATCH_UP_HOURS を過ぎた古い投稿は投稿しない（取りこぼしの誤爆防止）。
"""

import json
import os
import sys
import time
import datetime as dt
import urllib.parse
import urllib.request
import urllib.error

API_BASE = "https://graph.threads.net/v1.0"
JST = dt.timezone(dt.timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
POSTS_FILE = os.path.join(HERE, "threads_posts.json")
LOG_FILE = os.path.join(HERE, "posted_log.json")

# 予約時刻を過ぎてから、何時間以内なら投稿するか（Actionsの遅延・取りこぼし救済の窓）
CATCH_UP_HOURS = 6
# コンテナ作成から公開までの待機秒（Threads推奨。テキストは短くてよい）
PUBLISH_WAIT_SEC = 5

TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
USER_ID = os.environ.get("THREADS_USER_ID", "").strip()


def die(msg):
    print(f"[ERROR] {msg}")
    sys.exit(1)


def api_get(path, params):
    url = f"{API_BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path, params):
    url = f"{API_BASE}/{path}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_user_id():
    global USER_ID
    if USER_ID:
        return USER_ID
    me = api_get("me", {"fields": "id,username", "access_token": TOKEN})
    USER_ID = str(me["id"])
    print(f"[info] user_id を自動取得: {USER_ID} (@{me.get('username','?')})")
    return USER_ID


def create_container(text, reply_to_id=None):
    params = {"media_type": "TEXT", "text": text, "access_token": TOKEN}
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    res = api_post(f"{USER_ID}/threads", params)
    return res["id"]


def publish(creation_id):
    res = api_post(f"{USER_ID}/threads_publish",
                   {"creation_id": creation_id, "access_token": TOKEN})
    return res["id"]


def post_one_thread(slides):
    """slides を順番に投稿。2枚目以降は直前の投稿への返信にする。先頭の公開IDを返す。"""
    prev_id = None
    first_id = None
    for i, text in enumerate(slides):
        cid = create_container(text, reply_to_id=prev_id)
        time.sleep(PUBLISH_WAIT_SEC)
        mid = publish(cid)
        if i == 0:
            first_id = mid
        prev_id = mid
        print(f"    [{i+1}/{len(slides)}] published id={mid}")
        if i + 1 < len(slides):
            time.sleep(2)
    return first_id


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main():
    if not TOKEN:
        die("環境変数 THREADS_ACCESS_TOKEN が未設定です。GitHub の Secrets に登録してください。")

    data = load_json(POSTS_FILE, {"posts": []})
    log = load_json(LOG_FILE, {"posted_ids": []})
    posted = set(log.get("posted_ids", []))

    now = dt.datetime.now(JST)
    print(f"[info] now(JST)={now.isoformat()}  投稿候補を判定します")

    due = []
    for p in data["posts"]:
        if p["id"] in posted:
            continue
        when = dt.datetime.fromisoformat(p["datetime_jst"])
        age = (now - when).total_seconds()
        if 0 <= age <= CATCH_UP_HOURS * 3600:
            due.append(p)

    if not due:
        print("[info] いま投稿すべきものはありません。終了します。")
        return

    resolve_user_id()

    ok = 0
    for p in sorted(due, key=lambda x: x["datetime_jst"]):
        kind = "tree" if p["type"] == "tree" else "single"
        print(f"[post] {p['id']} ({kind}) 投稿します: {p['slides'][0][:24]}...")
        try:
            post_one_thread(p["slides"])
            posted.add(p["id"])
            ok += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            print(f"[NG] {p['id']} HTTP {e.code}: {body[:300]}")
        except Exception as e:
            print(f"[NG] {p['id']} {type(e).__name__}: {e}")

    # 公開済みログを保存（GitHub Actions側でコミットして次回に引き継ぐ）
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"posted_ids": sorted(posted)}, f, ensure_ascii=False, indent=2)

    print(f"[done] 投稿成功 {ok} 件 / 候補 {len(due)} 件")
    if ok < len(due):
        sys.exit(1)  # 失敗があればActionsを赤くして気づけるように


if __name__ == "__main__":
    main()
