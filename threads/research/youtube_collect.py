#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
腸活系 YouTube 動画のコメントを公式 YouTube Data API v3 で集める（読み取り専用）。

認証（どちらか）:
  - YOUTUBE_API_KEY           … APIキー
  - GSC_SA_JSON               … サービスアカウントJSON（base64 または生JSON）
                                 commentThreads.list は OAuth だと youtube.force-ssl スコープ必須。呼ぶのは GET だけ

方針:
  - 企業・メーカー・テレビ局などの公式チャンネルは除外し、個人で発信しているチャンネルだけ残す
  - コメント投稿者の名前・チャンネルIDは保存しない（本文・いいね数・日付だけ）
  - 返信のうち「動画の投稿者本人の返信」かどうかだけ真偽値で持つ（悩みへの回答を拾うため）
  - YouTube API サービスの規約に合わせ、取得から30日を超えた生コメントは --purge-days で削除できる

クォータ目安（1日10,000ユニット）: search 100 / videos・channels・commentThreads 各1
使い方:
  python3 threads/research/youtube_collect.py --target-comments 20000 --max-units 7000
  python3 threads/research/youtube_collect.py --comments-only --pages-per-video 1 --target-comments 45000
                                                   # 検索し直さず、登録済み動画のコメントだけ広く追加
  python3 threads/research/youtube_collect.py --purge-days 30   # 古い生コメントの削除だけ
"""
import argparse, base64, hashlib, json, os, re, sqlite3, subprocess, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

API = "https://www.googleapis.com/youtube/v3"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "choukatsu_research.sqlite")

# 悩み・やり方・商品の話が出やすい検索語（relevance 順・期間指定なし）
QUERIES = [
    "腸活", "腸活 効果", "腸活 やり方", "腸活 ルーティン", "腸活 失敗", "腸活 意味ない",
    "腸活 サプリ", "腸活 食事", "腸内環境 整える", "腸内フローラ", "便秘 解消", "便秘 即効",
    "便秘薬 やめたい", "頑固な便秘", "便秘 マッサージ", "便秘 食べ物", "おなら 臭い", "おなら 止まらない",
    "お腹 張る ガス", "お腹が鳴る", "ぽっこりお腹 腸", "更年期 便秘", "過敏性腸症候群", "ヨーグルト 腸活 逆効果",
    "発酵食品 腸活", "酪酸菌", "もち麦 腸活", "腸もみ",
]
# 直近1年の人気動画（viewCount 順）で今の悩みも拾う
RECENT_QUERIES = ["腸活", "便秘 解消", "腸内環境", "おなら 臭い", "お腹 張る", "腸活 ルーティン", "腸活 サプリ", "更年期 便秘"]

# チャンネル名に出る「個人ではない」目印（説明文は「公式LINE」等の誤検知が多いので見ない。
# 「〇〇大学」「〇〇病院」は個人発信者の屋号に多いので目印にしない）
OFFICIAL_TITLE = re.compile(
    r"公式|オフィシャル|official|株式会社|有限会社|（株）|\(株\)|製薬|薬品|ニュース|news|放送|新聞|"
    r"NHK|TBS|日テレ|テレ朝|テレビ朝日|フジテレビ|テレビ東京|テレ東|ABEMA|FNN|ANN|JNN|NNN|"
    r"ヤクルト|明治|森永|雪印|メグミルク|小林製薬|大正製薬|ロート|花王|アサヒ|キリン|サントリー|カゴメ|"
    r"ビオフェルミン|ミヤリサン|DHC|ファンケル|FANCL|資生堂|味の素|ダノン|ネスレ|日清|伊藤園|森下仁丹|"
    r"わかさ生活|やずや|えがお|世田谷自然食品|キューサイ|キユーピー|グリコ|ロッテ|ハウス食品|ミツカン|"
    r"マルコメ|ハナマルキ|武田|タケダ|第一三共|エスエス製薬|佐藤製薬|興和|ツムラ|クラシエ|ライオン|"
    r"医療法人|学会|協会|財団|市役所|保健所|生協|コープ|イオン|楽天|Amazon|アマゾン|通販|ショップ",
    re.IGNORECASE,
)

# お腹・腸の動画かどうか（タイトルで判定。build_db.py と同じ基準）
RELEVANT_TITLE = re.compile(r"腸|便秘|便|うんち|うんこ|おなら|オナラ|屁|ガス|下痢|発酵|乳酸菌|ビフィズス|ヨーグルト|整腸|消化|快便|宿便|"
                            r"食物繊維|オリゴ糖|酪酸|ミヤ|エビオス|マグネシウム|梅流し|ぽっこり|ポッコリ|下腹|膨満|張り")

UNIT = {"search": 100, "videos": 1, "channels": 1, "commentThreads": 1}


class Quota(Exception):
    pass


class Client:
    def __init__(self, max_units):
        self.max_units, self.used = max_units, 0
        self.key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        self.token, self.token_exp = None, 0
        self.sa = None if self.key else load_service_account()
        if not self.key and not self.sa:
            sys.exit("[ERROR] YOUTUBE_API_KEY も GSC_SA_JSON もありません。どちらかを環境変数に入れてください。")

    def _bearer(self):
        if self.token and time.time() < self.token_exp - 60:
            return self.token
        self.token, self.token_exp = service_account_token(self.sa), time.time() + 3500
        return self.token

    def get(self, resource, params):
        cost = UNIT[resource]
        if self.used + cost > self.max_units:
            raise Quota()
        headers = {}
        if self.key:
            params = dict(params, key=self.key)
        else:
            headers["Authorization"] = f"Bearer {self._bearer()}"
        url = f"{API}/{resource}?" + urllib.parse.urlencode(params)
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                    self.used += cost
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                self.used += cost
                body = e.read().decode("utf-8", "ignore")
                reason = ""
                try:
                    reason = json.loads(body)["error"]["errors"][0]["reason"]
                except Exception:
                    pass
                if reason in ("quotaExceeded", "dailyLimitExceeded"):
                    raise Quota()
                if e.code in (500, 503) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                return {"_error": reason or f"HTTP {e.code}"}
            except (urllib.error.URLError, TimeoutError):
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                return {"_error": "network"}
        return {"_error": "retry"}


def load_service_account():
    raw = os.environ.get("GSC_SA_JSON", "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)))


def service_account_token(sa):
    """サービスアカウントでアクセストークンを取る（秘密鍵はディスクに書かない）。
    commentThreads.list は readonly スコープだと insufficientPermissions になるため force-ssl を使う。"""
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    now = int(time.time())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(json.dumps({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/youtube.force-ssl",
                             "aud": sa["token_uri"], "iat": now, "exp": now + 3600}).encode())
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write(f"{head}.{claims}")
        path = f.name
    try:
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", "/dev/stdin", path],
                             input=sa["private_key"].encode(), capture_output=True, check=True).stdout
    finally:
        os.unlink(path)
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{head}.{claims}.{b64(sig)}"}).encode()
    with urllib.request.urlopen(urllib.request.Request(sa["token_uri"], data=body), timeout=30) as r:
        return json.loads(r.read())["access_token"]


SCHEMA = """
CREATE TABLE IF NOT EXISTS yt_channels (
  channel_id TEXT PRIMARY KEY, title TEXT, subscriber_count INTEGER, video_count INTEGER,
  is_official INTEGER, official_reason TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS yt_videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT, channel_title TEXT, title TEXT, description TEXT,
  published_at TEXT, view_count INTEGER, like_count INTEGER, comment_count INTEGER,
  found_by_query TEXT, comments_fetched INTEGER DEFAULT 0, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS yt_comments (
  comment_key TEXT PRIMARY KEY, video_id TEXT, parent_key TEXT, is_reply INTEGER, is_creator INTEGER,
  text TEXT, like_count INTEGER, reply_count INTEGER, published_at TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS yt_runs (
  run_at TEXT, units_used INTEGER, videos INTEGER, comments INTEGER, note TEXT);
"""


def h(s):
    return hashlib.sha1(s.encode()).hexdigest()[:20]


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search_videos(cli, db, log):
    found = {}
    after = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
    plans = [(q, {"order": "relevance"}) for q in QUERIES] + \
            [(q, {"order": "viewCount", "publishedAfter": after}) for q in RECENT_QUERIES]
    for q, extra in plans:
        try:
            d = cli.get("search", dict({"part": "snippet", "q": q, "type": "video", "maxResults": 50,
                                        "regionCode": "JP", "relevanceLanguage": "ja"}, **extra))
        except Quota:
            log("[quota] 検索を打ち切り")
            break
        for it in d.get("items", []):
            vid = it.get("id", {}).get("videoId")
            if vid and vid not in found:
                found[vid] = (it["snippet"].get("channelId"), q)
        log(f"search '{q}' {extra.get('order')}: 累計 {len(found)} 本 / units {cli.used}")
    return found


def classify_channels(cli, db, channel_ids, log):
    ids, official = list(channel_ids), set()
    for i in range(0, len(ids), 50):
        d = cli.get("channels", {"part": "snippet,statistics", "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        for it in d.get("items", []):
            title = it["snippet"].get("title", "")
            m = OFFICIAL_TITLE.search(title)
            st = it.get("statistics", {})
            db.execute("INSERT OR REPLACE INTO yt_channels VALUES (?,?,?,?,?,?,?)",
                       (it["id"], title, int(st.get("subscriberCount", 0) or 0), int(st.get("videoCount", 0) or 0),
                        1 if m else 0, m.group(0) if m else None, now_iso()))
            if m:
                official.add(it["id"])
    db.commit()
    log(f"channels: {len(ids)} 件中 公式・企業系 {len(official)} 件を除外")
    return official


def load_videos(cli, db, found, official, log):
    vids = [v for v, (ch, _) in found.items() if ch not in official]
    for i in range(0, len(vids), 50):
        d = cli.get("videos", {"part": "snippet,statistics", "id": ",".join(vids[i:i + 50]), "maxResults": 50})
        for it in d.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            db.execute("""INSERT INTO yt_videos (video_id, channel_id, channel_title, title, description, published_at,
                          view_count, like_count, comment_count, found_by_query, fetched_at)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?)
                          ON CONFLICT(video_id) DO UPDATE SET view_count=excluded.view_count,
                          like_count=excluded.like_count, comment_count=excluded.comment_count, fetched_at=excluded.fetched_at""",
                       (it["id"], sn.get("channelId"), sn.get("channelTitle"), sn.get("title"), sn.get("description", "")[:4000],
                        sn.get("publishedAt"), int(st.get("viewCount", 0) or 0), int(st.get("likeCount", 0) or 0),
                        int(st.get("commentCount", 0) or 0), found[it["id"]][1], now_iso()))
    db.commit()
    log(f"videos: 個人チャンネルの動画 {len(vids)} 本を登録")


def fetch_comments(cli, db, target, pages_per_video, log):
    total = db.execute("SELECT COUNT(*) FROM yt_comments").fetchone()[0]
    rows = [(v, ch, cc) for v, ch, cc, title in db.execute(
        """SELECT video_id, channel_id, comment_count, title FROM yt_videos
           WHERE comments_fetched = 0 AND comment_count > 0 ORDER BY comment_count DESC""").fetchall()
        if RELEVANT_TITLE.search(title or "")]
    for n, (vid, ch, cc) in enumerate(rows, 1):
        if total >= target:
            break
        token, pages = None, 0
        while pages < pages_per_video and total < target:
            params = {"part": "snippet,replies", "videoId": vid, "maxResults": 100,
                      "order": "relevance", "textFormat": "plainText"}
            if token:
                params["pageToken"] = token
            try:
                d = cli.get("commentThreads", params)
            except Quota:
                log("[quota] コメント取得を打ち切り")
                return total
            if "_error" in d:
                break
            for th in d.get("items", []):
                top = th["snippet"]["topLevelComment"]
                s = top["snippet"]
                key = h(top["id"])
                is_creator = 1 if s.get("authorChannelId", {}).get("value") == ch else 0
                cur = db.execute("INSERT OR IGNORE INTO yt_comments VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (key, vid, None, 0, is_creator, s.get("textDisplay", ""), int(s.get("likeCount", 0)),
                                  int(th["snippet"].get("totalReplyCount", 0)), s.get("publishedAt"), now_iso()))
                total += cur.rowcount
                for rp in th.get("replies", {}).get("comments", []):
                    rs = rp["snippet"]
                    rc = 1 if rs.get("authorChannelId", {}).get("value") == ch else 0
                    cur = db.execute("INSERT OR IGNORE INTO yt_comments VALUES (?,?,?,?,?,?,?,?,?,?)",
                                     (h(rp["id"]), vid, key, 1, rc, rs.get("textDisplay", ""), int(rs.get("likeCount", 0)),
                                      0, rs.get("publishedAt"), now_iso()))
                    total += cur.rowcount
            pages += 1
            token = d.get("nextPageToken")
            if not token:
                break
        db.execute("UPDATE yt_videos SET comments_fetched = 1 WHERE video_id = ?", (vid,))
        if n % 25 == 0:
            db.commit()
            log(f"comments: {n}/{len(rows)} 本目 累計 {total} 件 / units {cli.used}")
    db.commit()
    return total


def purge(db, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = db.execute("DELETE FROM yt_comments WHERE fetched_at < ?", (cutoff,)).rowcount
    db.commit()
    print(f"取得から{days}日を超えた生コメント {n} 件を削除しました")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--target-comments", type=int, default=20000)
    ap.add_argument("--max-units", type=int, default=7000)
    ap.add_argument("--pages-per-video", type=int, default=3)
    ap.add_argument("--purge-days", type=int)
    ap.add_argument("--comments-only", action="store_true", help="検索せず登録済み動画のコメントだけ取る")
    a = ap.parse_args()

    db = sqlite3.connect(a.db, timeout=120)
    db.executescript(SCHEMA)
    if a.purge_days:
        purge(db, a.purge_days)
        return
    log = lambda m: print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
    cli = Client(a.max_units)
    log(f"認証: {'APIキー' if cli.key else 'サービスアカウント'} / 上限 {a.max_units} ユニット")

    if not a.comments_only:
        found = search_videos(cli, db, log)
        channels = {ch for ch, _ in found.values() if ch}
        official = classify_channels(cli, db, channels, log)
        load_videos(cli, db, found, official, log)
    total = fetch_comments(cli, db, a.target_comments, a.pages_per_video, log)

    v = db.execute("SELECT COUNT(*) FROM yt_videos").fetchone()[0]
    db.execute("INSERT INTO yt_runs VALUES (?,?,?,?,?)",
               (now_iso(), cli.used, v, total, "comments-only" if a.comments_only else "collect"))
    db.commit()
    log(f"完了: 動画 {v} 本 / コメント {total} 件 / 使用 {cli.used} ユニット")


if __name__ == "__main__":
    main()
