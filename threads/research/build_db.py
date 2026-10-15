#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
腸活リサーチDB（threads/research/choukatsu_research.sqlite）を組み立てて、悩み・商品で分類する。

入れるもの:
  - ラッコキーワードで取得済みの質問・需要（raw/*.tsv）※今後ラッコのMCPは使わない
  - 自分の Threads 投稿の表示回数（raw/own_posts_insights.tsv / 公式APIで取得）
  - YouTube コメント（youtube_collect.py が yt_* テーブルに保存）
  - Yahoo!知恵袋の質問（chiebukuro_collect.py が qa_chiebukuro に保存）
やること:
  - どのデータにも同じ辞書で「悩みタグ」「商品タグ」「手応え（良かった/ダメだった）」を付ける
  - 集計ビューと CSV（exports/ 以下・Excelで開けるBOM付き）を書き出す

使い方: python3 threads/research/build_db.py [--db 別のDBファイル]
"""
import argparse, csv, os, re, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "exports")
DB = os.path.join(HERE, "choukatsu_research.sqlite")

# ---- 悩みの分類（ユーザーの言葉で。コメント・質問・検索語すべて同じ辞書で判定）----
PAINS = [
    ("C01", "効いてるの？（効果が分からない）",
     r"効いてる(の|か)|効いてない|効いてるのか|効果(が|は)?(ない|なし|出ない|感じない|わからない|分からない|ある(の|か))|"
     r"意味(が)?(ない|あるの|ある\?|ある？)|変わらない|変化(が|は)?(ない|なし)|実感(が)?(ない|できない|わかない)|"
     r"本当に(効|良い|いい|出る)|ほんとに(効|いい)|半信半疑|気休め|効かな"),
    ("C02", "やり方合ってる？（逆効果・NGが怖い）",
     r"合ってる(の|か)|あってる(の|か)|正しい(の|か|やり方|飲み方|食べ方)|間違って|逆効果|悪化|ダメ(なの|ですか|なんですか|だった)|"
     r"だめ(なの|ですか)|NG|ＮＧ|食べてはいけない|やってはいけない|合わない|向いてない|続けていい|良くない|よくない|に悪い"),
    ("C03", "いつ変わる？（期間）",
     r"何日|何ヶ月|何か月|何カ月|何週間|どれくらいで|どのくらいで|いつから|いつまで|"
     r"[0-9０-９一二三四五六七八九十]+(日|週間|ヶ月|か月|カ月|年)(続け|経って|たって|目|間)"),
    ("C04", "これって普通？（異常の境目）",
     r"やばい|ヤバい|ヤバイ|危険|異常|普通(なの|ですか|じゃない|でしょうか)|正常|何回|[0-9０-９一二三四五六七八九十]+日(も)?(出ない|出てない|出ていない|でない)|"
     r"病気(なの|か|では|でしょうか)|サイン"),
    ("C05", "結局どれが一番？（最強・比較）", r"最強|一番|いちばん|おすすめ|オススメ|ランキング|どっち|どちら|どれが|何がいい|何が良い"),
    ("C06", "毎日続けて大丈夫？（薬・依存が不安）",
     r"毎日(飲|食|使)|飲み続け|続けても|依存|癖になる|クセになる|やめ(たい|られない|ると)|減らしたい|手放せない|飲みすぎ|飲み過ぎ|頼って|頼り"),
    ("C07", "いつ・どう摂る？（タイミング）", r"朝と夜|朝か夜|夜と朝|いつ飲|いつ食|タイミング|寝る前|空腹時|食前|食後|時間帯"),
    ("C08", "人に言えない（音・におい・人目）",
     r"恥ずかし|職場|会社で|学校で|電車|人前|彼氏|彼女|旦那|夫が|妻が|バレ|デート|音が|音で|聞こえ|気まず"),
    ("C09", "見た目・体重（ぽっこり・肌）", r"痩せ|やせ|ダイエット|キロ|kg|体重|肌荒れ|肌が|ニキビ|ぽっこり|ポッコリ|下腹|太(る|った)"),
    ("C10", "薬・サプリの選び方", r"薬|サプリ|漢方|整腸剤|ビオフェルミン|ミヤ|マグネシウム|マグミット|センナ|コーラック|市販"),
    ("C11", "女性の体（生理・更年期・性）", r"生理|排卵|ホルモン|更年期|PMS|妊活|女性|女子|エッチ|えっち|セックス|性行為"),
    ("C12", "大きな病気が怖い（受診）", r"がん|ガン|癌|ポリープ|血便|出血|病院|受診|検査|大腸カメラ|内視鏡|医者|何科"),
    ("C13", "家族のこと（親・子ども）", r"(?<!酵)母|父|両親|親が|親の|祖母|祖父|高齢|介護|子供|子ども|娘|息子|赤ちゃん|夫の|主人"),
    ("C14", "におい（おなら・便・口）", r"臭|くさい|におい|匂い|ニオイ"),
]
SYMPTOMS = [
    ("便秘（出ない・硬い）", r"便秘|出ない|出にくい|硬い|かたい|コロコロ|残便|いきむ|踏ん張"),
    ("下痢・ゆるい", r"下痢|ゆるい|緩い|軟便|水っぽい"),
    ("張り・ガス", r"張る|張って|張り|パンパン|膨満|ガス|おなら|オナラ|屁|げっぷ|ゲップ"),
    ("腹痛", r"腹痛|お腹が痛|おなかが痛|キリキリ|差し込"),
    ("お腹が鳴る", r"鳴る|グー|ぐー|ギュルギュル|ぎゅるぎゅる"),
]
GOOD = r"出た|出ました|出ます|スッキリ|すっきり|快便|改善|良くなった|よくなった|楽になった|治った|効いた|効果あった|効果がありました|毎日出|びっくり|感動|ありがとう"
BAD = r"出ない|出なかった|効かない|効かなかった|変わらない|悪化|張って|苦し|痛く|下痢になった|合わなかった|逆効果|失敗|意味なかった|続かない|ダメだった"

# ---- 商品・食材（楽天で扱えるか／薬機法の注意度も一緒に持つ）----
# kind: food=食品 / goods=雑貨 / supplement=健康食品 / medicine=医薬品 / service=検査等 / method=やり方
PRODUCTS = [
    ("ヨーグルト", r"ヨーグルト", "food"), ("納豆", r"納豆", "food"), ("キムチ", r"キムチ", "food"),
    ("味噌・味噌汁", r"味噌|みそ汁", "food"), ("甘酒・酒粕", r"甘酒|酒粕", "food"), ("ぬか漬け", r"ぬか漬|糠漬", "food"),
    ("オリゴ糖", r"オリゴ糖", "food"), ("イヌリン・菊芋", r"イヌリン|菊芋|キクイモ", "food"),
    ("難消化性デキストリン", r"デキストリン|賢者の食卓|イージーファイバー", "food"),
    ("グアーガム（サンファイバー等）", r"グアーガム|サンファイバー", "food"), ("サイリウム（オオバコ）", r"サイリウム|オオバコ", "food"),
    ("もち麦・大麦", r"もち麦|大麦|押麦", "food"), ("オートミール", r"オートミール|オーツ", "food"), ("玄米・雑穀", r"玄米|雑穀", "food"),
    ("ごぼう・ごぼう茶", r"ごぼう|ゴボウ", "food"), ("海藻（わかめ・めかぶ・寒天）", r"わかめ|ワカメ|めかぶ|もずく|海藻|寒天", "food"),
    ("きのこ", r"きのこ|キノコ|えのき|しめじ|まいたけ|なめこ", "food"), ("キウイ", r"キウイ", "food"), ("バナナ", r"バナナ", "food"),
    ("プルーン・ドライフルーツ", r"プルーン|ドライフルーツ|デーツ", "food"), ("さつまいも・干し芋", r"さつまいも|サツマイモ|干し芋", "food"),
    ("白湯・水", r"白湯|常温の水|水を飲", "food"), ("コーヒー", r"コーヒー|珈琲", "food"), ("ココア", r"ココア|カカオ", "food"),
    ("オイル（オリーブ・アマニ・えごま）", r"オリーブオイル|アマニ油|亜麻仁油|えごま油|MCT", "food"),
    ("梅干し・梅流し", r"梅干|梅流し", "food"), ("青汁", r"青汁", "food"), ("プロテイン", r"プロテイン", "food"),
    ("酪酸菌（ミヤリサン等）", r"酪酸菌|ミヤリサン|ミヤBM|宮入菌", "medicine"),
    ("ビフィズス菌", r"ビフィズス|ビフィーナ|ビオスリー", "supplement"),
    ("乳酸菌・ヤクルト", r"乳酸菌|ヤクルト|R-1|LG21|ラブレ|L-92|ガセリ", "food"),
    ("ビオフェルミン", r"ビオフェルミン", "medicine"), ("エビオス（ビール酵母）", r"エビオス|ビール酵母", "medicine"),
    ("酸化マグネシウム", r"酸化マグネシウム|マグミット|マグネシウム", "medicine"),
    ("刺激性下剤（センナ・アロエ等）", r"センナ|センノシド|コーラック|ビサコジル|スルーラック|アロエ|大黄", "medicine"),
    ("漢方", r"漢方|ツムラ|大建中湯|防風通聖散|麻子仁丸|桂枝加芍薬|大黄甘草湯|潤腸湯", "medicine"),
    ("処方の便秘薬", r"リンゼス|アミティーザ|グーフィス|モビコール|ラキソベロン", "medicine"),
    ("浣腸・座薬", r"浣腸|座薬|坐薬", "medicine"), ("整腸剤（全般）", r"整腸剤", "medicine"),
    ("腸内フローラ検査", r"フローラ検査|腸内細菌検査|腸内環境検査", "service"),
    ("腹巻き・温活", r"腹巻|湯たんぽ|カイロ|温活", "goods"), ("トイレ踏み台", r"踏み台|足台|トイレステップ", "goods"),
    ("腸もみ・マッサージ", r"腸もみ|マッサージ|のの字", "method"), ("運動・ストレッチ", r"ストレッチ|ヨガ|ウォーキング|スクワット|腹筋", "method"),
    ("断食・ファスティング", r"断食|ファスティング|デトックス", "method"), ("四毒抜き・小麦抜き", r"四毒|小麦|グルテン", "method"),
    ("腸内洗浄", r"腸内洗浄", "service"),
]
# 動画タイトルがお腹・腸の話かどうか（「お腹」単体は妊娠・筋トレも拾うので入れない）
VIDEO_RELEVANT = r"腸|便秘|便|うんち|うんこ|おなら|オナラ|屁|ガス|下痢|発酵|乳酸菌|ビフィズス|ヨーグルト|整腸|消化|快便|宿便|" \
                 r"食物繊維|オリゴ糖|酪酸|ミヤ|エビオス|マグネシウム|梅流し|ぽっこり|ポッコリ|下腹|膨満|張り"

KIND_NOTE = {
    "food": "食品。紹介OK（効能をうたわず体験談＋【PR】）",
    "goods": "雑貨。紹介しやすい（効能表現なし）",
    "supplement": "健康食品。届出表示の範囲外は言えない。原則見送り",
    "medicine": "医薬品。このアカウントではPRしない（話題にするなら受診・相談を促すだけ）",
    "service": "検査キット等。診断ではない旨を明記すれば紹介可",
    "method": "やり方。商品ではない（本文ネタ）",
}


def compile_all():
    return ([(c, n, re.compile(p)) for c, n, p in PAINS], [(n, re.compile(p)) for n, p in SYMPTOMS],
            [(n, re.compile(p), k) for n, p, k in PRODUCTS], re.compile(GOOD), re.compile(BAD))


def read_tsv(name):
    path = os.path.join(RAW, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def hook_family(t):
    rules = [("エロ系", r"エロい話|エッチぃ話|エッチな話|ベッドの上の話|下品な話"), ("生々しい", r"生々しい"),
             ("恥ずかしい/人に言えない", r"恥ずかしい話|人には言えない話|女性にしか分からない"),
             ("ブロック/炎上覚悟", r"ブロックされる覚悟|炎上覚悟"), ("9割が勘違い", r"^9割|９割"),
             ("〇〇のサインです", r"^[^。]{0,12}サインです"), ("断言", r"^断言"), ("ぶっちゃけ", r"^ぶっちゃけ"),
             ("教えたくない/こっそり", r"教えたくない|こっそり"), ("広まってほしい/これだけは", r"広まってほしい|これだけは"),
             ("数字始まり", r"^[0-9０-９]|^月[0-9]|^1本|^3ヶ月")]
    for name, p in rules:
        if re.search(p, t[:40]):
            return name
    return "その他"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    db = sqlite3.connect(ap.parse_args().db, timeout=120)
    pains, symptoms, products, good, bad = compile_all()

    # ---- 取り込み（毎回作り直す表）----
    db.executescript("""
    DROP TABLE IF EXISTS rakko_questions; DROP TABLE IF EXISTS rakko_cluster_counts; DROP TABLE IF EXISTS ec_demand;
    DROP TABLE IF EXISTS own_posts; DROP TABLE IF EXISTS pain_dict; DROP TABLE IF EXISTS product_dict; DROP TABLE IF EXISTS tags;
    CREATE TABLE rakko_questions (id INTEGER PRIMARY KEY, seed TEXT, demand INTEGER, seen TEXT, question TEXT, cluster_hint TEXT, file TEXT);
    CREATE TABLE rakko_cluster_counts (seed TEXT, cluster TEXT, total_matches INTEGER, seed_universe INTEGER, note TEXT);
    CREATE TABLE ec_demand (keyword TEXT, monthly_volume INTEGER, engines TEXT);
    CREATE TABLE own_posts (id INTEGER PRIMARY KEY, date_jst TEXT, hour INTEGER, views INTEGER, likes INTEGER, replies INTEGER,
                            reposts INTEGER, quotes INTEGER, shares INTEGER, text TEXT, hook_family TEXT);
    CREATE TABLE pain_dict (code TEXT PRIMARY KEY, name TEXT, pattern TEXT);
    CREATE TABLE product_dict (name TEXT PRIMARY KEY, pattern TEXT, kind TEXT, pr_note TEXT);
    CREATE TABLE tags (source TEXT, ref TEXT, tag_type TEXT, tag TEXT);
    """)
    for f in ("questions_1_choukatsu_kankyo.tsv", "questions_2_benpi_onara.tsv", "questions_3_hari_yogurt_konenki_ibs.tsv",
              "questions_4_cluster_samples.tsv"):
        for r in read_tsv(f):
            db.execute("INSERT INTO rakko_questions (seed, demand, seen, question, cluster_hint, file) VALUES (?,?,?,?,?,?)",
                       (r["kw"], int(r["demand"]), r["seen"], r["question"], r.get("cluster"), f))
    for r in read_tsv("cluster_counts.tsv"):
        db.execute("INSERT INTO rakko_cluster_counts VALUES (?,?,?,?,?)",
                   (r["seed"], r["cluster"], int(r["total_matches"]), int(r["seed_universe"]), r["note"]))
    for r in read_tsv("ec_demand_rakuten_amazon.tsv"):
        db.execute("INSERT INTO ec_demand VALUES (?,?,?)", (r["keyword"], int(r["monthly_volume"]), r["engines"]))
    for r in read_tsv("own_posts_insights.tsv"):
        n = lambda k: int(r[k]) if r[k] else None
        db.execute("INSERT INTO own_posts (date_jst, hour, views, likes, replies, reposts, quotes, shares, text, hook_family) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?)", (r["date_jst"], int(r["date_jst"][11:13]), n("views"), n("likes"), n("replies"),
                                                  n("reposts"), n("quotes"), n("shares"), r["text"], hook_family(r["text"])))
    db.executemany("INSERT INTO pain_dict VALUES (?,?,?)", PAINS)
    db.executemany("INSERT INTO product_dict VALUES (?,?,?,?)", [(n, p, k, KIND_NOTE[k]) for n, p, k in PRODUCTS])

    # ---- タグ付け（全ソース同じ辞書）----
    def tag(source, ref, text):
        out = []
        for code, _, rx in pains:
            if rx.search(text):
                out.append((source, ref, "pain", code))
        for name, rx in symptoms:
            if rx.search(text):
                out.append((source, ref, "symptom", name))
        for name, rx, _ in products:
            if rx.search(text):
                out.append((source, ref, "product", name))
        g, b = bool(good.search(text)), bool(bad.search(text))
        if g or b:
            out.append((source, ref, "tone", "good" if g and not b else "bad" if b and not g else "mixed"))
        return out

    batch = []
    has = lambda t: db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone()
    for rid, text in db.execute("SELECT id, question FROM rakko_questions").fetchall():
        batch += tag("rakko", str(rid), text)
    for rid, text in db.execute("SELECT id, text FROM own_posts").fetchall():
        batch += tag("own_post", str(rid), text)
    if has("yt_comments"):
        cols = [r[1] for r in db.execute("PRAGMA table_info(yt_videos)")]
        if "relevant" not in cols:
            db.execute("ALTER TABLE yt_videos ADD COLUMN relevant INTEGER")
        rel = re.compile(VIDEO_RELEVANT)
        for vid, title in db.execute("SELECT video_id, title FROM yt_videos").fetchall():
            db.execute("UPDATE yt_videos SET relevant=? WHERE video_id=?", (1 if rel.search(title or "") else 0, vid))
        # 視聴者のコメント＝悩み・体験、発信者本人のコメント＝回答（別ソースで数える）
        for key, text, creator in db.execute("""SELECT c.comment_key, c.text, c.is_creator FROM yt_comments c
                                                JOIN yt_videos v ON v.video_id = c.video_id WHERE v.relevant = 1""").fetchall():
            batch += tag("youtube_creator" if creator else "youtube", key, text or "")
        for vid, title, desc in db.execute("SELECT video_id, title, description FROM yt_videos WHERE relevant = 1").fetchall():
            batch += [t for t in tag("yt_video", vid, f"{title} {desc}") if t[2] == "product"]
    if has("qa_chiebukuro"):
        for key, text in db.execute("SELECT qid_hash, text FROM qa_chiebukuro").fetchall():
            batch += tag("chiebukuro", key, text or "")
    db.executemany("INSERT INTO tags VALUES (?,?,?,?)", batch)
    db.execute("CREATE INDEX IF NOT EXISTS ix_tags ON tags(source, tag_type, tag)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_tags_ref ON tags(ref)")

    # ---- 集計ビュー ----
    db.executescript("""
    DROP VIEW IF EXISTS v_pain_by_source;
    CREATE VIEW v_pain_by_source AS
      SELECT t.source, t.tag AS code, d.name, COUNT(*) AS n FROM tags t JOIN pain_dict d ON d.code = t.tag
      WHERE t.tag_type = 'pain' GROUP BY t.source, t.tag;
    DROP VIEW IF EXISTS v_product_by_source;
    CREATE VIEW v_product_by_source AS
      SELECT t.source, t.tag AS product, p.kind, p.pr_note, COUNT(*) AS n,
             SUM(EXISTS (SELECT 1 FROM tags x WHERE x.source=t.source AND x.ref=t.ref AND x.tag_type='tone' AND x.tag='good')) AS good,
             SUM(EXISTS (SELECT 1 FROM tags x WHERE x.source=t.source AND x.ref=t.ref AND x.tag_type='tone' AND x.tag='bad')) AS bad
      FROM tags t JOIN product_dict p ON p.name = t.tag WHERE t.tag_type = 'product' GROUP BY t.source, t.tag;
    DROP VIEW IF EXISTS v_pain_product;
    CREATE VIEW v_pain_product AS
      SELECT a.source, a.tag AS pain, b.tag AS product, COUNT(*) AS n FROM tags a JOIN tags b ON a.source=b.source AND a.ref=b.ref
      WHERE a.tag_type='pain' AND b.tag_type='product' GROUP BY a.source, a.tag, b.tag;
    """)
    if has("yt_comments"):
        db.executescript("""
        DROP VIEW IF EXISTS v_creator_answers;
        CREATE VIEW v_creator_answers AS
          SELECT v.channel_title, v.title AS video_title, q.text AS question, a.text AS creator_answer, q.like_count AS question_likes
          FROM yt_comments a JOIN yt_comments q ON q.comment_key = a.parent_key JOIN yt_videos v ON v.video_id = a.video_id
          WHERE a.is_reply = 1 AND a.is_creator = 1 AND q.is_creator = 0 AND v.relevant = 1 AND length(a.text) >= 15;
        """)
    db.commit()

    # ---- CSV 書き出し（Excelで文字化けしないよう BOM 付き）----
    os.makedirs(OUT, exist_ok=True)
    def dump(name, sql):
        cur = db.execute(sql)
        with open(os.path.join(OUT, name), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in cur.description])
            w.writerows(cur.fetchall())
    dump("悩み×ソース別件数.csv", "SELECT * FROM v_pain_by_source ORDER BY source, n DESC")
    dump("商品×ソース別件数.csv", "SELECT * FROM v_product_by_source ORDER BY source, n DESC")
    dump("悩み×商品.csv", "SELECT * FROM v_pain_product WHERE n >= 3 ORDER BY source, pain, n DESC")
    dump("自分の投稿_表示回数.csv", "SELECT date_jst, views, likes, replies, reposts, hook_family, text FROM own_posts ORDER BY views DESC")
    dump("ラッコ_質問.csv", "SELECT seed, demand, seen, question, cluster_hint FROM rakko_questions ORDER BY seed, demand DESC")
    dump("ラッコ_悩みの大きさ.csv", "SELECT * FROM rakko_cluster_counts")
    dump("楽天Amazon_月間検索数.csv", "SELECT * FROM ec_demand ORDER BY monthly_volume DESC")
    if has("yt_comments"):
        dump("YouTube_悩みコメント.csv", """
          SELECT d.name AS 悩み, c.like_count AS いいね, substr(c.published_at,1,10) AS 日付, v.channel_title AS チャンネル, c.text AS コメント
          FROM tags t JOIN yt_comments c ON c.comment_key = t.ref JOIN yt_videos v ON v.video_id = c.video_id
          JOIN pain_dict d ON d.code = t.tag
          WHERE t.source='youtube' AND t.tag_type='pain' ORDER BY d.code, c.like_count DESC""")
        dump("YouTube_発信者の回答.csv", "SELECT * FROM v_creator_answers ORDER BY question_likes DESC")
        dump("YouTube_動画一覧.csv", """SELECT v.channel_title, v.title, v.view_count, v.comment_count, v.relevant AS お腹腸の動画,
             (SELECT COUNT(*) FROM yt_comments c WHERE c.video_id = v.video_id) AS 取得コメント数, substr(v.published_at,1,10) AS published,
             'https://www.youtube.com/watch?v=' || v.video_id AS url FROM yt_videos v ORDER BY v.view_count DESC""")
    if has("qa_chiebukuro"):
        dump("知恵袋_お腹腸の質問.csv", """
          SELECT q.category_name AS カテゴリ, q.posted_md AS 日時, q.answer_count AS 回答数,
                 (SELECT group_concat(d.name, ' / ') FROM tags t JOIN pain_dict d ON d.code=t.tag
                  WHERE t.source='chiebukuro' AND t.ref=q.qid_hash AND t.tag_type='pain') AS 悩み, q.text AS 質問
          FROM qa_chiebukuro q ORDER BY q.fetched_at""")
    counts = {s: db.execute("SELECT COUNT(DISTINCT ref) FROM tags WHERE source=?", (s,)).fetchone()[0]
              for s in ("rakko", "own_post", "youtube", "youtube_creator", "chiebukuro")}
    print("タグ付け済み（何かしらのタグが付いた件数）:", counts)
    print("書き出し先:", OUT)


if __name__ == "__main__":
    main()
