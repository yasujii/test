#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Threads の長期アクセストークン（60日で期限切れ）を延長し、GitHub の Secret を新しい値に書き換える。

- GET /v1.0/me で、今のトークンが使えるかを確認（使えなければ止まる）
- GET /refresh_access_token?grant_type=th_refresh_token で延長（新しい60日のトークンが返る）
  ※ 発行から24時間以上たっていて、まだ期限が切れていないトークンだけ延長できる
- 新しいトークンで GET /v1.0/me が通ることを確かめてから、gh secret set で Secret を上書き
- トークンはログに一切出さない（GitHub のマスク機能にも登録する）
- 投稿・削除などの書き込みは一切しない（Threads に対しては GET のみ）

環境変数:
  THREADS_ACCESS_TOKEN  今のトークン（Secret から渡す）
  GH_TOKEN              Secret を書き換えるための GitHub トークン（Secret「GH_SECRETS_PAT」から渡す）
  DRY_RUN               "true" なら確認だけ（延長も書き換えもしない）
  SECRET_NAME           書き換える Secret の名前（省略時 THREADS_ACCESS_TOKEN）
"""
import json, os, subprocess, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

BASE = "https://graph.threads.net"
TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() == "true"
SECRET_NAME = os.environ.get("SECRET_NAME", "THREADS_ACCESS_TOKEN").strip() or "THREADS_ACCESS_TOKEN"
REPO = os.environ.get("GITHUB_REPOSITORY", "")
JST = timezone(timedelta(hours=9))
HIDE = [TOKEN, GH_TOKEN]


def mask(s):
    for t in HIDE:
        if t:
            s = s.replace(t, "<TOKEN>")
    return s


def get(path, params):
    """GET を叩いて (ok, data_or_errtext) を返す。URL（トークン入り）は表示しない。"""
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
            return True, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, mask(f"HTTP {e.code} {e.read().decode('utf-8', 'ignore')[:400]}")
    except Exception as e:
        return False, mask(f"{type(e).__name__}: {e}")


def summary(lines):
    """Actions の実行結果ページに、日本語のまとめを出す。"""
    print("\n".join(lines))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def fail(msg):
    summary(["## Threadsトークン自動更新：失敗", "", msg])
    sys.exit(1)


def main():
    if not TOKEN:
        fail("Secret「THREADS_ACCESS_TOKEN」が空です。リポジトリの Settings → Secrets and variables → Actions で入れてください。")

    ok, me = get("v1.0/me", {"fields": "id,username", "access_token": TOKEN})
    if not ok:
        fail("今のトークンが使えません（期限切れ・取り消しなど）。延長はできないので、"
             "Meta の開発者画面で長期トークンを作り直し、Secret「THREADS_ACCESS_TOKEN」に入れ直してください。\n\n"
             f"エラー: {me}")
    user = me.get("username", "?")
    print(f"今のトークン: @{user} で使えます")

    if DRY_RUN:
        summary(["## Threadsトークン自動更新：確認だけ", "",
                 f"- 今のトークン: @{user} で使えます（延長はしていません）",
                 f"- Secret を書き換える用の GH_SECRETS_PAT: {'設定あり' if GH_TOKEN else '未設定（延長するには必要）'}"])
        return

    if not GH_TOKEN:
        fail("Secret「GH_SECRETS_PAT」が未設定なので、延長しても新しいトークンを保存できません（延長はしていません）。"
             "SETUP.md の「トークンの自動更新」の手順で作って入れてください。")
    if not REPO:
        fail("GITHUB_REPOSITORY が分かりません（GitHub Actions の中で動かしてください）。")

    ok, data = get("refresh_access_token", {"grant_type": "th_refresh_token", "access_token": TOKEN})
    if not ok:
        fail("延長に失敗しました。発行から24時間たっていないトークンは延長できません（次の週に自動で再挑戦します）。"
             "期限が切れている場合は作り直しが必要です。\n\n"
             f"エラー: {data}")
    new = (data.get("access_token") or "").strip()
    try:
        expires_in = int(data.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if not new:
        fail("延長の返事に新しいトークンが入っていませんでした。Secret は書き換えていません。")
    # 新しいトークンをログに出さないよう、最初にマスク登録する（この行自体はログに表示されない）
    print(f"::add-mask::{new}")
    HIDE.append(new)

    ok, me2 = get("v1.0/me", {"fields": "id,username", "access_token": new})
    if not ok or me2.get("id") != me.get("id"):
        fail(f"新しいトークンの動作確認に失敗したので、Secret は書き換えていません。\n\nエラー: {me2}")

    r = subprocess.run(["gh", "secret", "set", SECRET_NAME, "--repo", REPO],
                       input=new, text=True, capture_output=True)
    if r.returncode != 0:
        fail("Secret の書き換えに失敗しました。GH_SECRETS_PAT の権限（このリポジトリの Secrets: Read and write）"
             "と有効期限を確認してください。\n\n"
             f"エラー: {mask((r.stderr or r.stdout or '').strip())[:400]}")

    now = datetime.now(JST)
    lines = ["## Threadsトークン自動更新：成功", "",
             f"- アカウント: @{user}",
             f"- Secret「{SECRET_NAME}」を新しいトークンに書き換えました（値は表示しません）"]
    if expires_in:
        until = now + timedelta(seconds=expires_in)
        lines.append(f"- 新しい期限: {until.strftime('%Y-%m-%d %H:%M')}（日本時間・あと約{expires_in // 86400}日）")
    lines.append("- 次の自動更新: 来週の月曜 4:13（日本時間）")
    summary(lines)


if __name__ == "__main__":
    main()
