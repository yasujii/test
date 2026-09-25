#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube の「個人の腸活インフルエンサー」を日本と海外（英語圏）で集める（公式 YouTube Data API v3・読み取り専用）。

条件:
  - 個人で発信しているチャンネルだけ（企業・メーカー・公式・テレビ局・病院法人・通販は除外）
  - 登録者 1万人以上
  - コメント 500件以上の「お腹・腸の動画」を1本以上持つ
  - 日本は最大100チャンネル、海外は最大40チャンネル

認証・API呼び出し・公式チャンネル除外は youtube_collect.py を流用（GSC_SA_JSON。値は表示も保存もしない）。
コメントした人の名前・IDは保存しない（本文・いいね数・日付・「動画投稿者本人か」だけ）。
生データは threads/research/raw/youtube/（.gitignore 済み。公開リポジトリには載せない）。

クォータ: search=100、channels/videos/playlistItems/commentThreads=1。既定の上限 8,000 ユニット。
使い方:
  python3 threads/research/youtube_influencers.py --max-units 8000     # 集める
  python3 threads/research/youtube_influencers.py --analyze            # 集計して analysis.json を書く
"""
import argparse, json, os, re, sqlite3, sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import youtube_collect as yc  # noqa: E402
from youtube_collect import Client, Quota, OFFICIAL_TITLE, h, now_iso  # noqa: E402

yc.UNIT["playlistItems"] = 1

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "raw", "youtube")
DB_PATH = os.path.join(OUT_DIR, "influencers.sqlite")

MIN_SUBS, MIN_COMMENTS = 10000, 500
MAX_CH = {"jp": 100, "en": 40}
TARGET_COMMENTS = {"jp": 15000, "en": 8000}

# ---- 検索語 -------------------------------------------------------------
JP_QUERIES = [
    ("腸活", "relevance"), ("腸活", "viewCount"), ("腸活 ルーティン", "relevance"), ("腸活 効果", "relevance"),
    ("腸活 食事", "relevance"), ("腸活 レシピ", "relevance"), ("腸活 ダイエット", "viewCount"),
    ("便秘 解消", "relevance"), ("便秘 解消", "viewCount"), ("頑固な便秘", "relevance"), ("便秘 食べ物", "relevance"),
    ("腸内環境 整える", "relevance"), ("腸内フローラ", "relevance"), ("おなら 臭い", "relevance"),
    ("おなら 止まらない", "relevance"), ("お腹の張り 解消", "relevance"), ("お腹 張る ガス", "relevance"),
    ("発酵食品 腸活", "relevance"), ("発酵食品 手作り", "viewCount"), ("腸もみ", "relevance"), ("腸もみ マッサージ", "viewCount"),
    ("宿便 出す", "relevance"), ("更年期 便秘", "relevance"), ("便秘 ヨガ ストレッチ", "relevance"),
    ("ぬか漬け 腸活", "relevance"), ("甘酒 腸活", "relevance"),
]
EN_QUERIES = [
    ("gut health", "relevance"), ("gut health", "viewCount"), ("gut health tips", "relevance"),
    ("constipation relief", "relevance"), ("how to relieve constipation fast", "viewCount"),
    ("bloating", "relevance"), ("how to get rid of bloating", "viewCount"), ("gut microbiome", "relevance"),
    ("fiber challenge", "relevance"), ("fibermaxxing", "relevance"), ("fermented foods", "relevance"),
    ("leaky gut", "relevance"), ("IBS diet", "relevance"), ("low FODMAP", "relevance"),
    ("kefir gut health", "relevance"), ("kombucha gut health", "relevance"), ("psyllium husk", "relevance"),
    ("heal your gut", "viewCount"), ("what I eat in a day gut health", "relevance"), ("poop better", "relevance"),
]

# ---- 判定用 -------------------------------------------------------------
JP_RELEVANT = re.compile(r"腸|便秘|便通|うんち|うんこ|おなら|オナラ|下痢|発酵|乳酸菌|ビフィズス|ヨーグルト|整腸|快便|宿便|"
                         r"食物繊維|オリゴ糖|酪酸|梅流し|ぽっこり|ポッコリ|膨満|お腹の張り|お腹が張|ガス溜|ぬか漬|甘酒|納豆|味噌汁|キムチ")
EN_RELEVANT = re.compile(r"\bgut|constipat|bloat|poop|bowel|\bIBS\b|microbiome|probiotic|prebiotic|\bfib(er|re)|ferment|kefir|"
                         r"kombucha|sauerkraut|kimchi|digest|colon|leaky|FODMAP|stool|laxative|psyllium|flatulen|\bfart|SIBO",
                         re.IGNORECASE)
EN_OFFICIAL = re.compile(
    r"official|\binc\b|\bllc\b|\bltd\b|news|clinic|hospital|health system|medical center|medicine\b|university|institute|"
    r"foundation|magazine|\btv\b|network|channel 4|\bbbc\b|\bcnn\b|\bnbc\b|\bcbs\b|\babc\b|\bfox\b|today show|insider|"
    r"healthline|webmd|mayo|cleveland|hopkins|harvard|stanford|\bnhs\b|\bzoe\b|activia|yakult|culturelle|metamucil|"
    r"seed health|bon app|tasty|buzzfeed|osmosis|nucleus|medscape|verywell|everyday health|men's health|women's health|"
    r"shop|store|supplements?\b|nutrition co|brand|company|labs?\b|kitchen aid|times\b|post\b|journal|academy",
    re.IGNORECASE)
EN_COUNTRIES = {"US", "GB", "CA", "AU", "NZ", "IE"}
JP_CHARS = re.compile(r"[぀-ヿ一-鿿]")

# 目で確認して「個人ではない」と判断したチャンネル（名前の一部。実行後の目視確認で追加）
MANUAL_EXCLUDE = re.compile(r"$^")


def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS yi_channels (
  channel_id TEXT PRIMARY KEY, region TEXT, title TEXT, description TEXT, country TEXT,
  subscriber_count INTEGER, view_count INTEGER, video_count INTEGER, uploads_pl TEXT,
  is_official INTEGER, official_reason TEXT, lang_ok INTEGER, scanned INTEGER DEFAULT 0,
  qualified INTEGER DEFAULT 0, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS yi_videos (
  video_id TEXT PRIMARY KEY, channel_id TEXT, region TEXT, title TEXT, description TEXT, tags TEXT,
  published_at TEXT, view_count INTEGER, like_count INTEGER, comment_count INTEGER,
  relevant INTEGER, source TEXT, comments_fetched INTEGER DEFAULT 0, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS yi_comments (
  comment_key TEXT PRIMARY KEY, video_id TEXT, region TEXT, parent_key TEXT, is_reply INTEGER, is_creator INTEGER,
  text TEXT, like_count INTEGER, reply_count INTEGER, published_at TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS yi_runs (run_at TEXT, units_used INTEGER, note TEXT);
"""


def relevant(region, title, desc=""):
    rx = JP_RELEVANT if region == "jp" else EN_RELEVANT
    return 1 if rx.search(title or "") else 0


def search_all(cli, db, region, queries, units_cap):
    found = {}
    for q, order in queries:
        if cli.used + 100 > units_cap:
            log(f"[{region}] 検索の予算に達したので打ち切り")
            break
        params = {"part": "snippet", "q": q, "type": "video", "maxResults": 50, "order": order}
        params.update({"regionCode": "JP", "relevanceLanguage": "ja"} if region == "jp"
                      else {"regionCode": "US", "relevanceLanguage": "en"})
        try:
            d = cli.get("search", params)
        except Quota:
            log("[quota] 検索を打ち切り")
            break
        if "_error" in d:
            log(f"[{region}] search '{q}' エラー: {d['_error']}")
            if d["_error"] in ("forbidden", "insufficientPermissions", "HTTP 401", "HTTP 403"):
                sys.exit("認証または権限のエラーで停止しました")
            continue
        for it in d.get("items", []):
            vid = it.get("id", {}).get("videoId")
            if vid and vid not in found:
                found[vid] = (it["snippet"].get("channelId"), q)
        log(f"[{region}] search '{q}' ({order}): 累計 {len(found)} 本 / units {cli.used}")
    return found


def load_channels(cli, db, region, ids):
    ids = [i for i in ids if i and not db.execute("SELECT 1 FROM yi_channels WHERE channel_id=?", (i,)).fetchone()]
    for i in range(0, len(ids), 50):
        d = cli.get("channels", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50]), "maxResults": 50})
        for it in d.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            title, desc, country = sn.get("title", ""), sn.get("description", ""), sn.get("country")
            if region == "jp":
                m = OFFICIAL_TITLE.search(title)
                lang_ok = 1 if (country in (None, "JP")) else 0
            else:
                m = EN_OFFICIAL.search(title) or OFFICIAL_TITLE.search(title)
                lang_ok = 1 if (country in EN_COUNTRIES or (country is None and not JP_CHARS.search(title))) else 0
            db.execute("INSERT OR REPLACE INTO yi_channels (channel_id, region, title, description, country, subscriber_count,"
                       " view_count, video_count, uploads_pl, is_official, official_reason, lang_ok, fetched_at)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (it["id"], region, title, desc[:3000], country, int(st.get("subscriberCount", 0) or 0),
                        int(st.get("viewCount", 0) or 0), int(st.get("videoCount", 0) or 0),
                        it.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads"),
                        1 if m else 0, m.group(0) if m else None, lang_ok, now_iso()))
    db.commit()


def load_videos(cli, db, region, vids, source):
    vids = [v for v in vids if not db.execute("SELECT 1 FROM yi_videos WHERE video_id=?", (v,)).fetchone()]
    for i in range(0, len(vids), 50):
        d = cli.get("videos", {"part": "snippet,statistics", "id": ",".join(vids[i:i + 50]), "maxResults": 50})
        for it in d.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            db.execute("INSERT OR IGNORE INTO yi_videos (video_id, channel_id, region, title, description, tags, published_at,"
                       " view_count, like_count, comment_count, relevant, source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (it["id"], sn.get("channelId"), region, sn.get("title"), sn.get("description", "")[:5000],
                        json.dumps(sn.get("tags", [])[:30], ensure_ascii=False), sn.get("publishedAt"),
                        int(st.get("viewCount", 0) or 0), int(st.get("likeCount", 0) or 0),
                        int(st.get("commentCount", 0) or 0), relevant(region, sn.get("title", "")), source, now_iso()))
    db.commit()


def candidates(db, region):
    """個人・登録1万以上・言語OK のチャンネル。見つかった腸動画の最大コメント数が多い順。"""
    return db.execute("""
      SELECT c.channel_id, c.uploads_pl, c.scanned,
             COALESCE(MAX(CASE WHEN v.relevant=1 THEN v.comment_count END), 0) AS best
      FROM yi_channels c LEFT JOIN yi_videos v ON v.channel_id = c.channel_id
      WHERE c.region=? AND c.is_official=0 AND c.lang_ok=1 AND c.subscriber_count>=?
      GROUP BY c.channel_id ORDER BY best DESC""", (region, MIN_SUBS)).fetchall()


def scan_uploads(cli, db, region, ch, pl, pages):
    """チャンネルの投稿動画（新しい順に最大 pages×50 本）から腸の動画を探す。"""
    token, vids = None, []
    for _ in range(pages):
        p = {"part": "contentDetails,snippet", "playlistId": pl, "maxResults": 50}
        if token:
            p["pageToken"] = token
        d = cli.get("playlistItems", p)
        if "_error" in d:
            break
        for it in d.get("items", []):
            t = it["snippet"].get("title", "")
            if relevant(region, t):
                vids.append(it["contentDetails"]["videoId"])
        token = d.get("nextPageToken")
        if not token:
            break
    load_videos(cli, db, region, vids, "uploads")
    db.execute("UPDATE yi_channels SET scanned=1 WHERE channel_id=?", (ch,))
    db.commit()


def mark_qualified(db, region):
    db.execute("UPDATE yi_channels SET qualified=0 WHERE region=?", (region,))
    rows = db.execute("""
      SELECT c.channel_id, c.title, MAX(v.comment_count) best FROM yi_channels c JOIN yi_videos v ON v.channel_id=c.channel_id
      WHERE c.region=? AND c.is_official=0 AND c.lang_ok=1 AND c.subscriber_count>=? AND v.relevant=1 AND v.comment_count>=?
      GROUP BY c.channel_id ORDER BY best DESC""", (region, MIN_SUBS, MIN_COMMENTS)).fetchall()
    rows = [r for r in rows if not MANUAL_EXCLUDE.search(r[1] or "")]
    if region == "jp":  # 日本の動画タイトルが日本語であること
        rows = [r for r in rows if db.execute("SELECT 1 FROM yi_videos WHERE channel_id=? AND relevant=1 AND comment_count>=? "
                                              "AND title GLOB '*[ぁ-んァ-ン一-龥]*'", (r[0], MIN_COMMENTS)).fetchone()]
    for cid, _, _ in rows[:MAX_CH[region]]:
        db.execute("UPDATE yi_channels SET qualified=1 WHERE channel_id=?", (cid,))
    db.commit()
    return len(rows[:MAX_CH[region]])


def fetch_comments(cli, db, region, units_cap, per_channel, per_video_pages, max_videos):
    total = db.execute("SELECT COUNT(*) FROM yi_comments WHERE region=?", (region,)).fetchone()[0]
    chans = db.execute("SELECT channel_id FROM yi_channels WHERE region=? AND qualified=1 ORDER BY subscriber_count DESC",
                       (region,)).fetchall()
    for (ch,) in chans:
        if total >= TARGET_COMMENTS[region]:
            break
        got = db.execute("SELECT COUNT(*) FROM yi_comments cm JOIN yi_videos v ON v.video_id=cm.video_id WHERE v.channel_id=?",
                         (ch,)).fetchone()[0]
        vids = db.execute("""SELECT video_id FROM yi_videos WHERE channel_id=? AND relevant=1 AND comments_fetched=0
                             AND comment_count>=? ORDER BY comment_count DESC LIMIT ?""", (ch, MIN_COMMENTS, max_videos)).fetchall()
        for (vid,) in vids:
            if got >= per_channel or total >= TARGET_COMMENTS[region]:
                break
            token = None
            for _ in range(per_video_pages):
                if cli.used + 1 > units_cap:
                    return total
                p = {"part": "snippet,replies", "videoId": vid, "maxResults": 100, "order": "relevance", "textFormat": "plainText"}
                if token:
                    p["pageToken"] = token
                try:
                    d = cli.get("commentThreads", p)
                except Quota:
                    return total
                if "_error" in d:
                    break
                for th in d.get("items", []):
                    top = th["snippet"]["topLevelComment"]
                    s = top["snippet"]
                    key = h(top["id"])
                    isc = 1 if s.get("authorChannelId", {}).get("value") == ch else 0
                    n = db.execute("INSERT OR IGNORE INTO yi_comments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (key, vid, region, None, 0, isc, s.get("textDisplay", ""), int(s.get("likeCount", 0)),
                                    int(th["snippet"].get("totalReplyCount", 0)), s.get("publishedAt"), now_iso())).rowcount
                    total += n; got += n
                    for rp in th.get("replies", {}).get("comments", []):
                        rs = rp["snippet"]
                        rc = 1 if rs.get("authorChannelId", {}).get("value") == ch else 0
                        n = db.execute("INSERT OR IGNORE INTO yi_comments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                       (h(rp["id"]), vid, region, key, 1, rc, rs.get("textDisplay", ""),
                                        int(rs.get("likeCount", 0)), 0, rs.get("publishedAt"), now_iso())).rowcount
                        total += n; got += n
                token = d.get("nextPageToken")
                if not token or got >= per_channel:
                    break
            db.execute("UPDATE yi_videos SET comments_fetched=1 WHERE video_id=?", (vid,))
            db.commit()
        log(f"[{region}] comments: 累計 {total} 件 / units {cli.used}")
    return total


def collect(a):
    os.makedirs(OUT_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    cli = Client(a.max_units)
    log(f"認証: {'APIキー' if cli.key else 'サービスアカウント'} / 上限 {a.max_units} ユニット")
    # 予算: 検索 JP 2,600 + EN 2,000、残りをチャンネル調査とコメントに
    for region, queries, cap in (("jp", JP_QUERIES, 2700), ("en", EN_QUERIES, 4700)):
        if a.skip_search:
            break
        found = search_all(cli, db, region, queries, cap)
        load_channels(cli, db, region, {c for c, _ in found.values()})
        load_videos(cli, db, region, list(found), "search")
        log(f"[{region}] 候補チャンネル {len(candidates(db, region))} 件 / units {cli.used}")

    comment_reserve = 450
    for region in ("jp", "en"):
        scan_cap = a.max_units - comment_reserve - (a.en_scan_reserve if region == "jp" else 0)
        for ch, pl, scanned, best in candidates(db, region):
            if mark_qualified(db, region) >= MAX_CH[region] and best < MIN_COMMENTS:
                break
            if scanned or not pl:
                continue
            if cli.used + 6 > scan_cap:
                log(f"[{region}] チャンネル調査の予算に達したので打ち切り")
                break
            try:
                scan_uploads(cli, db, region, ch, pl, a.scan_pages)
            except Quota:
                break
        log(f"[{region}] 条件を満たすチャンネル {mark_qualified(db, region)} 件 / units {cli.used}")

    for region, per_ch, pages in (("jp", 250, 2), ("en", 300, 2)):
        cap = a.max_units - (150 if region == "jp" else 0)
        n = fetch_comments(cli, db, region, cap, per_ch, pages, 3)
        log(f"[{region}] コメント合計 {n} 件")
    db.execute("INSERT INTO yi_runs VALUES (?,?,?)", (now_iso(), cli.used, "collect"))
    db.commit()
    log(f"完了: 使用 {cli.used} ユニット")


# ======================================================================
#  集計
# ======================================================================
# アイテム・方法の辞書（日本語／英語）。分類 = food(食品) / goods(雑貨) / habit(習慣) / drug(医薬品。Threadsでは紹介しない)
ITEMS = [
    # key, 表示名, 分類, 日本語パターン, 英語パターン
    ("yogurt", "ヨーグルト", "food", r"ヨーグルト", r"yog(h)?urt"),
    ("kefir", "ケフィア", "food", r"ケフィア", r"kefir"),
    ("natto", "納豆", "food", r"納豆", r"natto"),
    ("miso", "味噌・味噌汁", "food", r"味噌|みそ汁|みそ玉", r"\bmiso"),
    ("nukazuke", "ぬか漬け", "food", r"ぬか漬|糠漬|ぬか床", r"nukazuke|rice bran pickle"),
    ("amazake", "甘酒", "food", r"甘酒", r"amazake"),
    ("kimchi", "キムチ", "food", r"キムチ", r"kimchi"),
    ("sauerkraut", "ザワークラウト", "food", r"ザワークラウト", r"sauerkraut"),
    ("kombucha", "コンブチャ", "food", r"コンブチャ|紅茶キノコ", r"kombucha"),
    ("fermented", "発酵食品（全般）", "food", r"発酵食品|発酵", r"ferment"),
    ("kiwi", "キウイ", "food", r"キウイ", r"kiwi"),
    ("prune", "プルーン", "food", r"プルーン", r"prune"),
    ("banana", "バナナ", "food", r"バナナ", r"banana"),
    ("greenbanana", "グリーンバナナ・難消化性でんぷん", "food", r"グリーンバナナ|レジスタントスターチ|難消化性", r"green banana|resistant starch|plantain flour"),
    ("oatmeal", "オートミール", "food", r"オートミール|オーツ", r"oat(meal|s)\b|overnight oats"),
    ("mochimugi", "もち麦・大麦", "food", r"もち麦|大麦|スーパー大麦", r"barley"),
    ("brownrice", "玄米", "food", r"玄米", r"brown rice"),
    ("seaweed", "海藻（わかめ・めかぶ等）", "food", r"海藻|わかめ|ワカメ|めかぶ|もずく|寒天|ひじき", r"seaweed|agar"),
    ("kinoko", "きのこ", "food", r"きのこ|キノコ|えのき|しいたけ", r"mushroom"),
    ("burdock", "ごぼう", "food", r"ごぼう|ゴボウ", r"burdock"),
    ("sweetpotato", "さつまいも", "food", r"さつまいも|サツマイモ|焼き芋", r"sweet potato"),
    ("beans", "豆類", "food", r"大豆|豆類|ひよこ豆|レンズ豆|おから", r"\bbeans\b|lentil|chickpea"),
    ("chia", "チアシード", "food", r"チアシード", r"chia"),
    ("flax", "アマニ（亜麻仁）", "food", r"アマニ|亜麻仁", r"flax"),
    ("psyllium", "サイリウム（オオバコ）", "food", r"サイリウム|オオバコ", r"psyllium|metamucil"),
    ("inulin", "イヌリン・水溶性食物繊維粉", "food", r"イヌリン|難消化性デキストリン|グアーガム|水溶性食物繊維", r"inulin|guar|dextrin|fiber supplement|benefiber"),
    ("fiber", "食物繊維（全般）", "food", r"食物繊維|繊維", r"\bfib(er|re)"),
    ("oligo", "オリゴ糖", "food", r"オリゴ糖", r"oligosacchar|\bFOS\b|\bGOS\b"),
    ("olive", "オリーブオイル・油", "food", r"オリーブオイル|えごま油|アマニ油|MCT", r"olive oil|MCT oil"),
    ("water", "水・白湯", "habit", r"白湯|水を飲|水分", r"drink (more )?water|hydrat|warm water"),
    ("coffee", "コーヒー", "food", r"コーヒー", r"coffee"),
    ("honey", "はちみつ", "food", r"はちみつ|ハチミツ|蜂蜜", r"honey"),
    ("vinegar", "酢・りんご酢", "food", r"りんご酢|黒酢|お酢", r"apple cider vinegar|\bACV\b"),
    ("ginger", "しょうが・スパイス", "food", r"生姜|しょうが|ショウガ", r"ginger|peppermint|fennel"),
    ("bonebroth", "ボーンブロス・スープ", "food", r"ボーンブロス|スープ", r"bone broth"),
    ("30plants", "週30種類の植物", "habit", r"30種類|30品目", r"30 plants|30 different plants|plant diversity|plant points"),
    ("kiwi2", "キウイ1日2個", "habit", r"キウイ.{0,6}2個", r"(two|2) kiwis?"),
    ("fibermax", "ファイバーマキシング（食物繊維を最大化）", "habit", r"ファイバーマキシング", r"fib(er|re)\s?maxx?ing|fiber challenge"),
    ("probiotic", "乳酸菌・ビフィズス菌サプリ（食品）", "food", r"乳酸菌|ビフィズス|プロバイオティクス|サプリ", r"probiotic|supplement"),
    ("butyric", "酪酸菌", "food", r"酪酸", r"butyrate|butyric"),
    ("aloe", "アロエ・青汁", "food", r"アロエ|青汁", r"aloe"),
    ("smoothie", "スムージー", "food", r"スムージー", r"smoothie"),
    ("umenagashi", "梅流し（大根）", "habit", r"梅流し", r"$^"),
    ("fasting", "ファスティング・断食", "habit", r"ファスティング|断食|16時間", r"fasting"),
    ("lowfodmap", "低FODMAP食", "habit", r"FODMAP|フォドマップ", r"FODMAP"),
    ("glutenfree", "グルテンフリー・小麦抜き", "habit", r"グルテンフリー|小麦", r"gluten"),
    ("dairyfree", "乳製品抜き", "habit", r"乳製品(を)?(やめ|抜)", r"dairy[- ]free|cut (out )?dairy"),
    ("sugar", "砂糖を控える", "habit", r"砂糖(を)?(やめ|控え|抜)", r"cut (out )?sugar|no sugar"),
    ("massage", "腸もみ・お腹マッサージ", "habit", r"腸もみ|マッサージ|お腹を(もむ|揉)", r"massage"),
    ("yoga", "ヨガ・ストレッチ", "habit", r"ヨガ|ストレッチ|ツボ", r"yoga|stretch"),
    ("walk", "運動・ウォーキング", "habit", r"運動|ウォーキング|散歩|筋トレ|スクワット", r"walk|exercise|workout"),
    ("squatty", "足台（トイレでの姿勢）", "goods", r"踏み台|足台|トイレの姿勢|前かがみ|ロダンの考える人", r"squatty|squat(ting)? potty|toilet stool|foot ?stool|elevate your feet"),
    ("sleep", "睡眠・朝のリズム", "habit", r"睡眠|早起き|朝日", r"sleep"),
    ("breakfast", "朝ごはん・朝の習慣", "habit", r"朝ごはん|朝食|朝の習慣|モーニングルーティン", r"breakfast|morning routine"),
    ("warm", "お腹を温める（腹巻き・カイロ）", "goods", r"腹巻|カイロ|湯たんぽ|温め", r"heating pad|hot water bottle"),
    ("chew", "よく噛む・食べ方", "habit", r"よく噛|噛む回数|咀嚼", r"chew"),
    ("diary", "記録（便日記・アプリ）", "habit", r"記録|日記|アプリ", r"journal|track(ing)?\b"),
    ("stress", "ストレス・自律神経ケア", "habit", r"ストレス|自律神経|深呼吸|呼吸", r"stress|vagus|breath"),
    ("magnesium", "酸化マグネシウム（医薬品）", "drug", r"酸化マグネシウム|マグミット|マグネシウム", r"magnesium|milk of magnesia|miralax|miralax"),
    ("laxative", "便秘薬・下剤（医薬品）", "drug", r"便秘薬|下剤|コーラック|センナ|ピコスル|リンゼス|アミティーザ|グーフィス|モビコール", r"laxative|senna|dulcolax|linzess|colace|stool softener"),
    ("kanpo", "漢方（医薬品）", "drug", r"漢方|大黄|麻子仁|防風通聖|桂枝加芍薬", r"$^"),
    ("seicho", "整腸剤（医薬品）", "drug", r"整腸剤|ビオフェルミン|ミヤBM|ミヤリサン|ビオスリー|新ビオフェルミン|強ミヤリサン", r"$^"),
    ("enema", "浣腸（医薬品）", "drug", r"浣腸|イチジク", r"enema"),
]

POS = {"jp": re.compile(r"出(まし|ました|た！|た!|たー|てき|るよう|るように)|スッキリ|すっきり|快便|良くな|改善|楽にな|効果(が)?(あ|出)|"
                        r"ぺたんこ|ペタンコ|へこ|凹|毎日出|バナナうんち|感動|おすすめ|オススメ|続けてい|最高|治まり|おさま|減りました|減った"),
       "en": re.compile(r"\b(it )?worked\b|works (great|wonders)|helped|help(s|ed) me|game ?changer|life ?changing|"
                        r"finally (pooped|went)|went (to the bathroom|within)|so much better|no more bloat|less bloat|"
                        r"regular now|changed my life|amazing results|highly recommend|saved me|feel (so much )?better")}
NEG = {"jp": re.compile(r"効かな|効果(が)?な|出ない(まま|です)|変わらな|悪化|逆に|余計に|合わな|張って(しま|きた)|痛くな|下痢にな|ダメ|だめで|意味(が)?な"),
       "en": re.compile(r"didn'?t (work|help)|doesn'?t (work|help)|made (it|me|things) worse|no (results|change|difference)|"
                        r"worse|didn'?t do anything|nothing (worked|helps)|gave me (gas|diarrhea|cramps)|more bloated")}
TRIED = {"jp": re.compile(r"試し|やってみ|飲んでみ|食べてみ|続けて|始めて|飲み始め|食べ始め|毎日|を飲んで|を食べて|取り入れ|実践|したら|したところ|てから"),
         "en": re.compile(r"\bi (tried|started|have been|'ve been|take|took|eat|ate|drink|drank|added|did|do)\b|since i|after (i|a week|2 weeks)|"
                          r"i've tried|been taking|been eating|been drinking", re.IGNORECASE)}
WORRIES = [
    ("出ない（便秘が続く）", r"出ない|出なく|でない|便秘|何日も|3日|4日|5日|一週間|1週間|コロコロ", r"constipat|can'?t (poop|go)|haven'?t pooped|days without|hard stool"),
    ("お腹が張る・ガス", r"張る|張って|張り|ガス|膨満|パンパン|ぽっこり|ポッコリ", r"bloat|gas\b|gassy|distend|swollen"),
    ("おならのにおい・回数", r"おなら|オナラ|屁|臭い|くさい|におい|匂い", r"fart|flatulen|smell|stink"),
    ("下痢・ゆるい便", r"下痢|ゆるい|緩い|軟便", r"diarrh|loose stool|IBS-D"),
    ("便秘と下痢をくり返す・過敏性腸", r"過敏性|IBS|交互", r"\bIBS\b|irritable bowel"),
    ("効いているか不安・分からない", r"効いてるのか|効いているのか|分からな|わからな|どれくらいで|いつから|本当に|意味ある", r"does (it|this) (really )?work|how long|is it normal|not sure|should i|is this normal"),
    ("薬がやめられない・薬への不安", r"薬(が|を)(やめ|止め|手放|飲まない)|薬に頼|下剤(が|を)(やめ|止め)|薬依存|薬なし|薬を(飲んで|使って)", r"laxative|dependent on|off (the )?laxatives|rely on"),
    ("お腹の痛み", r"腹痛|お腹が痛|おなかが痛|痛い", r"pain|cramp|hurt"),
    ("痔・出血", r"痔|出血|血が", r"hemorrhoid|blood in|bleeding"),
    ("肌荒れ・体重・むくみ", r"肌荒れ|ニキビ|体重|むくみ|痩せ", r"acne|skin|weight|puffy"),
    ("更年期・ホルモン・生理", r"更年期|生理|ホルモン|妊娠|産後", r"menopaus|period|hormon|pregnan|postpartum"),
]
CLAIM = {"jp": re.compile(r"(\d+|一|二|三|四|五|六|七|八|九|十)\s*(日|週間|ヶ月|か月|カ月|ヵ月|年)|毎日|続けた|変化|結果|激変|ぺたんこ|ペタンコ|-\d|減|出た|スッキリ|快便"),
         "en": re.compile(r"\b(\d+|one|two|three|four|five|seven|ten|thirty)\s*(day|week|month|year)s?\b|results|changed|transform|before (and|&) after|healed|fixed|cured|i tried", re.IGNORECASE)}


def compile_items(region):
    idx = 3 if region == "jp" else 4
    flags = 0 if region == "jp" else re.IGNORECASE
    return [(it[0], it[1], it[2], re.compile(it[idx], flags)) for it in ITEMS]


def analyze(a):
    db = sqlite3.connect(DB_PATH)
    out = {}
    for region in ("jp", "en"):
        items = compile_items(region)
        worries = [(n, re.compile(p if region == "jp" else e, 0 if region == "jp" else re.IGNORECASE)) for n, p, e in WORRIES]
        R = {}
        # A. チャンネル一覧
        chans = db.execute("""SELECT c.channel_id, c.title, c.subscriber_count, c.country FROM yi_channels c
                              WHERE c.region=? AND c.qualified=1 ORDER BY c.subscriber_count DESC""", (region,)).fetchall()
        rows = []
        for cid, title, subs, country in chans:
            n_target = db.execute("SELECT COUNT(*) FROM yi_videos WHERE channel_id=? AND relevant=1 AND comment_count>=?",
                                  (cid, MIN_COMMENTS)).fetchone()[0]
            top = db.execute("SELECT title, comment_count, view_count FROM yi_videos WHERE channel_id=? AND relevant=1 "
                             "ORDER BY comment_count DESC LIMIT 1", (cid,)).fetchone()
            n_rel = db.execute("SELECT COUNT(*) FROM yi_videos WHERE channel_id=? AND relevant=1", (cid,)).fetchone()[0]
            ncom = db.execute("SELECT COUNT(*) FROM yi_comments cm JOIN yi_videos v ON v.video_id=cm.video_id WHERE v.channel_id=?",
                              (cid,)).fetchone()[0]
            rows.append({"channel": title, "subs": subs, "country": country, "target_videos": n_target, "gut_videos_seen": n_rel,
                         "top_title": top[0], "top_comments": top[1], "top_views": top[2], "comments_collected": ncom})
        R["channels"] = rows
        # B. 本人の「試したこと」と主張：対象チャンネルの腸動画のタイトル・説明文・本人返信
        texts = db.execute("""SELECT c.title, v.title, v.description, v.comment_count, v.view_count FROM yi_videos v
                              JOIN yi_channels c ON c.channel_id=v.channel_id
                              WHERE c.qualified=1 AND v.region=? AND v.relevant=1""", (region,)).fetchall()
        creator_item = Counter(); creator_item_ch = defaultdict(set); claims = []
        for ch, t, desc, cc, vc in texts:
            blob = f"{t}\n{(desc or '')[:1500]}"
            for k, name, cat, rx in items:
                if rx.search(t or ""):
                    creator_item[name] += 1; creator_item_ch[name].add(ch)
            if CLAIM[region].search(t or ""):
                claims.append({"channel": ch, "title": t, "comments": cc, "views": vc})
        creator_replies = db.execute("""SELECT c.title, cm.text FROM yi_comments cm JOIN yi_videos v ON v.video_id=cm.video_id
                                        JOIN yi_channels c ON c.channel_id=v.channel_id WHERE cm.region=? AND cm.is_creator=1""",
                                     (region,)).fetchall()
        R["creator_items_in_titles"] = [(n, c, len(creator_item_ch[n])) for n, c in creator_item.most_common(40)]
        R["claim_titles"] = sorted(claims, key=lambda x: -x["comments"])[:80]
        R["creator_replies_n"] = len(creator_replies)
        R["creator_replies_sample"] = [r for r in creator_replies if len(r[1]) > 25][:120]
        # C. コメント欄
        comments = db.execute("SELECT text, like_count FROM yi_comments WHERE region=? AND is_creator=0", (region,)).fetchall()
        stat = defaultdict(lambda: {"mention": 0, "tried": 0, "pos": 0, "neg": 0})
        samples = defaultdict(lambda: {"pos": [], "neg": []})
        worry_n = Counter(); exp_n = 0
        for text, likes in comments:
            tried = bool(TRIED[region].search(text)); pos = bool(POS[region].search(text)); neg = bool(NEG[region].search(text))
            if tried and (pos or neg):
                exp_n += 1
            for k, name, cat, rx in items:
                if rx.search(text):
                    s = stat[(name, cat)]; s["mention"] += 1
                    if tried:
                        s["tried"] += 1
                        if pos and not neg:
                            s["pos"] += 1
                            if len(samples[name]["pos"]) < 6 and 20 < len(text) < 400:
                                samples[name]["pos"].append((likes, text))
                        elif neg:
                            s["neg"] += 1
                            if len(samples[name]["neg"]) < 4 and 20 < len(text) < 400:
                                samples[name]["neg"].append((likes, text))
            for n, rx in worries:
                if rx.search(text):
                    worry_n[n] += 1
        R["comments_total"] = len(comments)
        R["experience_comments"] = exp_n
        R["item_stats"] = sorted([{"item": n, "cat": c, **v} for (n, c), v in stat.items()], key=lambda x: -x["tried"])
        R["samples"] = samples
        R["worries"] = worry_n.most_common()
        out[region] = R
    # F. 数字
    out["counts"] = {
        "runs": db.execute("SELECT * FROM yi_runs").fetchall(),
        "channels_seen": db.execute("SELECT region, COUNT(*) FROM yi_channels GROUP BY region").fetchall(),
        "official_excluded": db.execute("SELECT region, COUNT(*) FROM yi_channels WHERE is_official=1 GROUP BY region").fetchall(),
        "lang_excluded": db.execute("SELECT region, COUNT(*) FROM yi_channels WHERE is_official=0 AND lang_ok=0 GROUP BY region").fetchall(),
        "under_10k": db.execute("SELECT region, COUNT(*) FROM yi_channels WHERE is_official=0 AND lang_ok=1 AND subscriber_count<? "
                                "GROUP BY region", (MIN_SUBS,)).fetchall(),
        "scanned": db.execute("SELECT region, COUNT(*) FROM yi_channels WHERE scanned=1 GROUP BY region").fetchall(),
        "videos": db.execute("SELECT region, COUNT(*), SUM(relevant) FROM yi_videos GROUP BY region").fetchall(),
        "comments": db.execute("SELECT region, COUNT(*), SUM(is_reply), SUM(is_creator) FROM yi_comments GROUP BY region").fetchall(),
    }
    path = os.path.join(OUT_DIR, "analysis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=list)
    log(f"集計を書きました: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-units", type=int, default=8000)
    ap.add_argument("--scan-pages", type=int, default=2, help="1チャンネルあたり新しい順に何ページ（50本/ページ）調べるか")
    ap.add_argument("--en-scan-reserve", type=int, default=900, help="日本の調査中に残しておく海外調査用ユニット")
    ap.add_argument("--skip-search", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args()
    if a.analyze:
        analyze(a)
    else:
        collect(a)


if __name__ == "__main__":
    main()
