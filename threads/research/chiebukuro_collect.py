#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yahoo!知恵袋のカテゴリ一覧ページから、お腹・腸まわりの質問を集める（読み取り専用）。

守っていること:
  - robots.txt を毎回読み、許可されているパスだけ取る（/search・/api・/tag・並び替えURLは使わない）
  - User-Agent は Claude-User と正直に名乗る。ブロックされたら（403/429/連続切断）その場で止める
  - 1リクエストごとに 2.5 秒以上あける
  - 投稿者名は取らない。質問文・カテゴリ・回答数・日時だけ。お腹・腸に関係ない質問は本文を保存しない
  - 集めた本文は公開リポジトリに載せない（threads/research/*.sqlite は .gitignore 済み）

使い方:
  python3 threads/research/chiebukuro_collect.py            # 既定の8カテゴリ × 最大99ページ
"""
import hashlib, html, re, sqlite3, sys, time, urllib.error, urllib.request, urllib.robotparser
from datetime import datetime, timezone
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "choukatsu_research.sqlite")
UA = "Mozilla/5.0 (compatible; Claude-User/1.0; +https://support.anthropic.com/)"
BASE = "https://chiebukuro.yahoo.co.jp"
DELAY = 2.5
MAX_PAGES = 99
CATEGORIES = {
    "2078297872": "病気、症状",
    "2078297857": "健康、病気、病院",
    "2078297871": "病院、検査",
    "2079639833": "女性の病気",
    "2080159619": "ダイエット",
    "2080159616": "ダイエット、フィットネス",
    "2078297889": "子どもの病気とトラブル",
    "2078297965": "料理、食材",
}

GUT = re.compile(
    r"便秘|下痢|腸|おなら|オナラ|屁|ガス|腹痛|腹部|膨満|張る|張って|うんち|ウンチ|うんこ|排便|快便|宿便|軟便|"
    r"整腸|乳酸菌|ビフィズス|ヨーグルト|発酵|食物繊維|オリゴ糖|過敏性|IBS|痔|げっぷ|ゲップ|胃|消化|ぽっこり|下腹|"
    r"お腹|おなか|(?<![郵不])便(?!利|乗|箋|宜|り|名|数|座)")
NOT_GUT = re.compile(r"妊娠|妊婦|胎動|出産|つわり|宅配便|航空便|定期便|船便")

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_chiebukuro (
  qid_hash TEXT PRIMARY KEY, category_id TEXT, category_name TEXT, text TEXT,
  answer_count INTEGER, posted_md TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS qa_chiebukuro_scan (
  category_id TEXT, list_category TEXT, pages INTEGER, scanned INTEGER, gut INTEGER, fetched_at TEXT);
"""


def main():
    rp = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")
    rp.read()
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    log = lambda m: print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
    failures = 0
    for cid, cname in CATEGORIES.items():
        scanned = gut = pages = 0
        for page in range(1, MAX_PAGES + 1):
            url = f"{BASE}/category/{cid}/question/list" + (f"?page={page}" if page > 1 else "")
            if not rp.can_fetch("Claude-User", url):
                log(f"robots.txt で不許可のため停止: {url}")
                return
            time.sleep(DELAY)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                    h = r.read().decode("utf-8", "ignore")
                failures = 0
            except urllib.error.HTTPError as e:
                log(f"HTTP {e.code} で停止（相手側の拒否として扱う）: {url}")
                if e.code in (403, 429):
                    return
                break
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                failures += 1
                log(f"接続エラー {failures}回目: {type(e).__name__}")
                if failures >= 3:
                    log("連続で切断されたので全体を停止")
                    return
                time.sleep(30)
                continue
            items = re.findall(r'<a href="https://detail\.chiebukuro\.yahoo\.co\.jp/qa/question_detail/q(\d+)"'
                               r'[^>]*data-cl-params="([^"]*)"[^>]*>(.*?)</a>', h, re.S)
            if not items:
                break
            pages += 1
            for qid, params, body in items:
                body = re.sub(r"<svg.*?</svg>", "", body, flags=re.S)
                parts = [html.unescape(p.strip()) for p in re.sub(r"<[^>]+>", "\x00", body).split("\x00") if p.strip()]
                if len(parts) < 2:
                    continue
                scanned += 1
                text = parts[1]
                if not GUT.search(text) or NOT_GUT.search(text):
                    continue
                ca = re.search(r"ca_id:(\d+)", params)
                ans = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
                cur = db.execute("INSERT OR IGNORE INTO qa_chiebukuro VALUES (?,?,?,?,?,?,?)",
                                 (hashlib.sha1(qid.encode()).hexdigest()[:20], ca.group(1) if ca else cid, parts[0],
                                  text, ans, parts[3] if len(parts) > 3 else None,
                                  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
                gut += cur.rowcount
            if page % 20 == 0:
                db.commit()
                log(f"{cname} p{page}: 走査 {scanned} 件 / お腹・腸 {gut} 件")
        db.execute("INSERT INTO qa_chiebukuro_scan VALUES (?,?,?,?,?,?)",
                   (cid, cname, pages, scanned, gut, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        db.commit()
        log(f"完了 {cname}: {pages}ページ / 走査 {scanned} 件 / お腹・腸 {gut} 件")
    total = db.execute("SELECT COUNT(*) FROM qa_chiebukuro").fetchone()[0]
    log(f"全カテゴリ完了: お腹・腸の質問 合計 {total} 件")


if __name__ == "__main__":
    main()
