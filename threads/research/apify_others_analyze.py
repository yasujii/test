#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apify（futurizerush/meta-threads-scraper）で集めた「他アカウントの投稿」を集計する。

入れるもの: threads/research/raw/apify/*.json（Apify のデータセットをそのまま保存した JSON 配列）
出すもの（どれも raw/ の中＝公開リポジトリには載らない）:
  - raw/threads_others_YYYYMMDD.csv           … 投稿の表（BOM付きUTF-8。表示回数1万回以上だけ）
  - raw/threads_others_all_YYYYMMDD.csv       … 集めた投稿ぜんぶ（参考）
  - raw/threads_others_accounts_YYYYMMDD.csv  … アカウントごとの投稿ペース・表示回数・マネタイズ判定
  - 画面に集計（書き出しの言い回し上位20・テーマ・時間帯・38番の20ワードとの比較）→ 40番の材料

守ること:
  - 自分（@sakura_cyoukatsu）の投稿は除く
  - メールアドレス・電話番号などの個人の連絡先は保存しない
  - 分析ファイルには他人の本文を丸写ししない（ここでも例は20文字までに切る）

使い方: python3 threads/research/apify_others_analyze.py [--days 30] [--min-views 10000] [--me sakura_cyoukatsu]
"""
import argparse, csv, glob, json, os, re, statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
JST = timezone(timedelta(hours=9))

# ---- 38番「伸びるワード20」＋避けるワード（1枚目にこの言い回しがあるか）----
WORDS38 = [
    (1, "ちょっとエロい話するけど", r"ちょっとエロ"),
    (2, "ちょっとエッチぃ話するけど", r"エッチぃ"),
    (3, "ちょっとエッチな話なんだけど", r"エッチな話"),
    (4, "朝イチの、ちょっと生々しい話", r"生々しい"),
    (5, "〇〇のサインです", r"サイン"),
    (6, "ブロックされる覚悟で書くけど", r"ブロック(され[るた])?覚悟"),
    (7, "9割の人が勘違いしてる", r"[9９九]割"),
    (8, "恥ずかしい話／人には言えない話", r"恥ずかしい話|人に(は)?言えない"),
    (9, "女性にしか分からない話", r"女性にしか"),
    (10, "〜って誰にも習ったことなくない？", r"(習|教わ)ったこと(が)?な|なくない[？?]"),
    (11, "大人になってから誰にも直されてない", r"直されてな|誰にも(直|注意)"),
    (12, "〜って怒られたことない？", r"怒られたこと"),
    (13, "母が消化器内科で聞いてきた（先生の話）", r"消化器内科|胃腸科|先生(に|が)(聞|言)"),
    (14, "トイレの所作（拭き方・座り方・スマホ）", r"拭き方|座り方|踏ん張|いきみ方|トイレで(スマホ|座)"),
    (15, "何日出ないとやばい？", r"何日.{0,4}(出ない|出てない)|[0-9０-９一二三四五六七]日(も)?出(ない|てない)"),
    (16, "本当に効いてるの？", r"効いてる(の|か)|効果(ある|ない|が出)"),
    (17, "やり方合ってる？", r"合ってる|間違って|逆効果"),
    (18, "毎日飲んで大丈夫？", r"毎日.{0,6}大丈夫|飲み続け"),
    (19, "えっちのあと出なくなる", r"(えっち|エッチ)(の|した)(あと|後)|行為の(あと|後)"),
    (20, "おなら（回数・におい）", r"おなら|オナラ|屁"),
]
AVOID = [
    ("断言（絶対・必ず）", r"絶対|必ず"),
    ("悲報／言いにくいけど", r"悲報|言いにくい"),
    ("こっそり／本当は教えたくない", r"こっそり|教えたくない"),
    ("数字で始める", r"^[0-9０-９]"),
]
THEMES = [
    ("便秘・出ない", r"便秘|出ない|出てない|でない|詰まり|コロコロ|硬い便"),
    ("張り・ガス・おなら", r"張る|張り|パンパン|ガス|おなら|オナラ|膨満"),
    ("下痢・ゆるい", r"下痢|ゆるい|緩い|軟便"),
    ("におい", r"臭|におい|匂い|ニオイ"),
    ("腸内環境・菌", r"腸内環境|腸内細菌|善玉|悪玉|菌|フローラ"),
    ("食べ物（発酵・食物繊維）", r"ヨーグルト|納豆|発酵|キムチ|味噌|食物繊維|オリゴ|もち麦|きのこ|海藻|バナナ|キウイ"),
    ("飲み物・水", r"白湯|水分|コーヒー|お茶|ココア|甘酒"),
    ("トイレの所作", r"トイレ|拭き|座り方|踏ん張|いきみ|うんち|うんこ|便座"),
    ("女性の体（生理・更年期・性）", r"生理|更年期|ホルモン|妊娠|産後|エッチ|えっち|エロ|夫婦"),
    ("見た目・体重・肌", r"ぽっこり|痩せ|体重|ダイエット|肌|ニキビ|むくみ"),
    ("メンタル・睡眠", r"睡眠|眠|ストレス|メンタル|自律神経|イライラ"),
    ("薬・サプリ", r"薬|サプリ|マグネシウム|酸化マグネシウム|整腸剤|漢方|乳酸菌サプリ"),
    ("病気・受診", r"病院|受診|大腸がん|がん|ポリープ|血便|内視鏡|検査|医師|先生"),
]
# ---- マネタイズの印 ----
LINK_KINDS = [
    ("楽天", r"rakuten\.co\.jp|(^|\.)r10\.to|hb\.afl\.rakuten|room\.rakuten"),
    ("Amazon", r"amazon\.co\.jp|amazon\.com|amzn\.to|amzn\.asia|link\.amazon"),
    ("ASP（A8・もしも等）", r"a8\.net|moshimo\.com|valuecommerce|afi-b\.com|accesstrade|felmat|rentracks|linksynergy"),
    ("有料コンテンツ（Brain等）", r"brain-market|tips\.jp|mond\.how|coconala|booth\.pm|udemy"),
    ("note", r"note\.com|note\.mu"),
    ("ネットショップ", r"stores\.jp|base\.shop|thebase\.in|shopify|myshopify|minne|creema|mercari"),
    ("LINE", r"lin\.ee|line\.me|liff\.line\.me|lmes\.jp|lstep|l-step|utage"),
    ("無料配布ページ（Notion等）", r"notion\.site|notion\.so|app\.notion\.com"),
    ("リンク集", r"lit\.link|linktr\.ee|linkco\.re|profu\.link|potofu\.me|bio\.link|instabio|tap\.bio"),
    ("他のSNS", r"instagram\.com|youtube\.com|youtu\.be|tiktok\.com|x\.com|twitter\.com"),
]
TEXT_SIGNS = [
    ("PR表記", r"【PR】|#PR|＃PR|\bPR\b|広告|プロモーション"),
    ("楽天ROOM・アフィ", r"楽天ROOM|楽天ルーム|アフィ"),
    ("プロフ・固定への誘導", r"プロフ(ィール)?(の|から|に)|固定(ポスト|投稿|の)|ハイライト"),
    ("LINE・特典への誘導", r"LINE(登録|で|に)|公式LINE|無料(で)?プレゼント|限定(配布|公開)|特典"),
    ("商品の紹介", r"買って(よかった|良かった)|愛用|リピ|購入|おすすめ(の|です)|クーポン|セール"),
]
PAID_KINDS = {"楽天", "Amazon", "ASP（A8・もしも等）", "有料コンテンツ（Brain等）", "ネットショップ"}
LEAD_KINDS = {"LINE", "リンク集", "note", "無料配布ページ（Notion等）"}


def link_kinds(urls):
    kinds = set()
    for u in urls or []:
        host = (urlparse(u).netloc or u).lower()
        full = u.lower()
        for name, pat in LINK_KINDS:
            if re.search(pat, host) or re.search(pat, full):
                kinds.add(name)
                break
        else:
            if host:
                kinds.add("その他のサイト")
    return kinds


def text_signs(text):
    return {name for name, pat in TEXT_SIGNS if re.search(pat, text or "")}


def opening(text, n=30):
    t = re.sub(r"\s+", "", text or "")
    return t[:n]


def jst(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(JST)
    except Exception:
        return None


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return int(statistics.median(xs)) if xs else None


def _overlap(a, b, n=2):
    """a と b が2文字以上の同じ並びを持つか（1文字ずつずれた同じ文を見分ける）"""
    return a in b or b in a or any(a[i:i + n] in b for i in range(len(a) - n + 1))


def ngram_top(openings, views, top=20, nmin=3, nmax=20, min_df=3):
    """書き出し（先頭30文字）に何度も出てくる言い回しを数える（分かち書きなしのN文字の並び）。"""
    df = Counter()
    where = defaultdict(set)
    for i, op in enumerate(openings):
        seen = set()
        for n in range(nmin, nmax + 1):
            for j in range(0, max(0, len(op) - n + 1)):
                g = op[j:j + n]
                # 記号・数字だけの並びと、句読点や小さい「っ」などで始まる切れ端は数えない
                if re.fullmatch(r"[\W_0-9０-９ー]+", g) or re.match(r"[\W_っゃゅょぁぃぅぇぉんー、。！？]", g) or g in seen:
                    continue
                seen.add(g)
        for g in seen:
            df[g] += 1
            where[g].add(i)
    cands = [(g, c) for g, c in df.items() if c >= min_df]
    # 回数が多い順・同じなら長い順に採用し、ほぼ同じ投稿に出てくる重なった言い回しは1つにまとめる
    cands.sort(key=lambda x: (-x[1], -len(x[0])))
    kept = []
    for g, c in cands:
        dup = False
        for idx, (k, kc) in enumerate(kept):
            common = len(where[g] & where[k])
            if common >= 0.8 * c and _overlap(g, k):
                dup = True
                # 長い言い回しがほぼ同じ本数あるなら、長い方に置き換える（「ちょっと」→「ちょっとエロい話」）
                if len(g) > len(k) and c >= 0.8 * kc:
                    kept[idx] = (g, c)
                break
        if not dup and len(kept) < top:
            kept.append((g, c))
    kept.sort(key=lambda x: (-x[1], -len(x[0])))
    out = []
    for g, c in kept[:top]:
        vs = [views[i] for i in where[g]]
        out.append({"言い回し": g, "本数": c, "表示回数の中央値": med(vs)})
    return out


def load_items():
    items = []
    for f in sorted(glob.glob(os.path.join(RAW, "apify", "*.json"))):
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data = data.get("items") or data.get("data") or [data]
        for it in data:
            it["_file"] = os.path.basename(f)
            items.append(it)
    return items


def write_csv(path, rows, cols):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-views", type=int, default=10000)
    ap.add_argument("--me", default="sakura_cyoukatsu")
    ap.add_argument("--today", default=datetime.now(JST).strftime("%Y-%m-%d"))
    a = ap.parse_args()
    today = datetime.strptime(a.today, "%Y-%m-%d").replace(tzinfo=JST)
    since = today - timedelta(days=a.days)
    stamp = today.strftime("%Y%m%d")

    items = load_items()
    profiles = {}
    posts = {}
    replies = defaultdict(list)
    for it in items:
        u = (it.get("username") or it.get("requested_username") or "").lower()
        if not u or u == a.me.lower():
            continue
        # アカウント情報（投稿の行にも付いてくることがある）
        p = profiles.setdefault(u, {"followers": None, "bio": "", "bio_links": set(), "verified": None})
        if it.get("followers_count") is not None:
            p["followers"] = max(p["followers"] or 0, it.get("followers_count"))
        # 投稿ペース用：アカウント指定で取った最新の投稿（固定投稿を除く・ツリーも含む）だけを使う
        # （検索で拾った古い投稿を混ぜると期間が伸びてペースが低く出るため）
        if it.get("requested_username") and it.get("post_code") and not it.get("is_pinned") and it.get("created_at"):
            p.setdefault("_times", set()).add((it["post_code"], it["created_at"], bool(it.get("is_reply"))))
        if it.get("bio"):
            p["bio"] = it.get("bio")
        for l in (it.get("bio_links") or []) + (it.get("external_links") or []):
            p["bio_links"].add(l if isinstance(l, str) else (l.get("url") or ""))
        if it.get("is_verified") is not None:
            p["verified"] = it.get("is_verified")
        if it.get("record_type") == "profile" or not it.get("post_code"):
            continue
        if it.get("is_reply"):
            replies[u].append(it)   # ツリーの2枚目以降・コメント返信（リンクの有無だけ見る）
            continue
        code = it["post_code"]
        cur = posts.get(code)
        kws = set(it.get("search_keywords") or ([it["search_keyword"]] if it.get("search_keyword") else []))
        sorts = {it["search_filter"]} if it.get("search_filter") else set()
        if cur:
            cur["_kws"] |= kws
            cur["_sorts"] |= sorts
            continue
        it["_kws"], it["_sorts"], it["_u"] = kws, sorts, u
        posts[code] = it

    rows = []
    for it in posts.values():
        t = jst(it.get("created_at") or "")
        views = it.get("view_count") if it.get("view_count_status") == "available" else None
        kinds = link_kinds(it.get("urls"))
        signs = text_signs(it.get("text_content"))
        if it.get("is_paid_partnership"):
            signs.add("タイアップ表示")
        rows.append({
            "アカウント名": "@" + it["_u"],
            "フォロワー数": profiles.get(it["_u"], {}).get("followers"),
            "投稿日時（日本時間）": t.strftime("%Y-%m-%d %H:%M") if t else "",
            "時": t.hour if t else "",
            "URL": it.get("post_url", ""),
            "1枚目の本文": (it.get("text_content") or "").replace("\r", " "),
            "表示回数": views if views is not None else "",
            "表示回数の状態": it.get("view_count_status", ""),
            "いいね": it.get("like_count"), "返信": it.get("reply_count"),
            "再投稿": it.get("repost_count"), "引用": it.get("quote_count"), "シェア": it.get("share_count"),
            "リンクの種類": "・".join(sorted(kinds)), "マネタイズの印": "・".join(sorted(signs)),
            "検索ワード": "・".join(sorted(k for k in it["_kws"] if k)), "並び順": "・".join(sorted(it["_sorts"])),
            "_t": t, "_views": views,
        })
    recent = [r for r in rows if r["_t"] and r["_t"] >= since]
    hit = [r for r in recent if (r["_views"] or 0) >= a.min_views]
    hit.sort(key=lambda r: -r["_views"])
    cols = ["アカウント名", "フォロワー数", "投稿日時（日本時間）", "URL", "1枚目の本文", "表示回数", "表示回数の状態",
            "いいね", "返信", "再投稿", "引用", "シェア", "リンクの種類", "マネタイズの印", "検索ワード", "並び順"]
    os.makedirs(RAW, exist_ok=True)
    write_csv(os.path.join(RAW, f"threads_others_{stamp}.csv"), hit, cols)
    write_csv(os.path.join(RAW, f"threads_others_all_{stamp}.csv"),
              sorted(rows, key=lambda r: -(r["_views"] or 0)), cols)

    # ---- アカウントごと ----
    by_u = defaultdict(list)
    for r in rows:
        by_u[r["アカウント名"][1:]].append(r)
    acc_rows = []
    for u, rs in by_u.items():
        tl = sorted(profiles.get(u, {}).get("_times", set()), key=lambda x: x[1])
        tt = [jst(x[1]) for x in tl if jst(x[1])]
        roots = sum(1 for x in tl if not x[2])
        span = max(1.0, (tt[-1] - tt[0]).total_seconds() / 86400) if len(tt) >= 5 else None
        vs = [r["_views"] for r in rs if r["_views"] is not None]
        p = profiles.get(u, {})
        post_kinds = set()
        for r in rs:
            post_kinds |= set(filter(None, r["リンクの種類"].split("・")))
        reply_kinds = set()
        for rp in replies.get(u, []):
            reply_kinds |= link_kinds(rp.get("urls"))
        bio_kinds = link_kinds(p.get("bio_links"))
        signs = set()
        for r in rs:
            signs |= set(filter(None, r["マネタイズの印"].split("・")))
        signs |= text_signs(p.get("bio"))
        all_kinds = post_kinds | reply_kinds | bio_kinds
        if all_kinds & PAID_KINDS or "タイアップ表示" in signs or "PR表記" in signs:
            judge = "している（売上につながるリンク・PR表記あり）"
        elif all_kinds & LEAD_KINDS or signs & {"LINE・特典への誘導", "プロフ・固定への誘導"}:
            judge = "導線あり（LINE・note・無料配布などで集客）"
        else:
            judge = "見当たらない"
        acc_rows.append({
            "アカウント名": "@" + u, "フォロワー数": p.get("followers"), "集めた投稿数": len(rs),
            "1日あたりの投稿数（目安・1枚目だけ）": round(roots / span, 1) if span else "",
            "表示回数の中央値": med(vs), "表示回数の最高": max(vs) if vs else "",
            "1万回超えの本数": sum(1 for v in vs if v >= a.min_views),
            "プロフのリンク": "・".join(sorted(bio_kinds)), "投稿のリンク": "・".join(sorted(post_kinds)),
            "ツリー・返信のリンク": "・".join(sorted(reply_kinds)), "マネタイズの印": "・".join(sorted(signs)),
            "マネタイズ判定": judge,
        })
    acc_rows.sort(key=lambda r: -(r["表示回数の中央値"] or 0))
    write_csv(os.path.join(RAW, f"threads_others_accounts_{stamp}.csv"), acc_rows, list(acc_rows[0].keys()) if acc_rows else ["アカウント名"])

    # ---- 集計（40番の材料）----
    base = hit if hit else sorted(recent, key=lambda r: -((r["いいね"] or 0)))[:max(1, len(recent) // 5)]
    base_label = f"表示回数{a.min_views:,}回以上" if hit else "（表示回数が取れないため）いいね上位20%"
    ops = [opening(r["1枚目の本文"]) for r in base]
    vws = [r["_views"] if r["_views"] is not None else (r["いいね"] or 0) for r in base]
    summary = {
        "集めた行数": len(items), "他人の投稿（1枚目）": len(rows), f"直近{a.days}日": len(recent),
        "表示回数が取れた投稿": sum(1 for r in recent if r["_views"] is not None),
        "対象": base_label, "対象の本数": len(base), "アカウント数": len(by_u),
        "書き出しの言い回し上位20": ngram_top(ops, vws),
        "テーマ": sorted(({"テーマ": n, "本数": sum(1 for r in base if re.search(p, r["1枚目の本文"])),
                         "表示回数の中央値": med([v for r, v in zip(base, vws) if re.search(p, r["1枚目の本文"])])}
                        for n, p in THEMES), key=lambda x: -x["本数"]),
        "時間帯（日本時間）": sorted(Counter(r["時"] for r in base if r["時"] != "").items()),
        "38番の20ワード": [],
        "避けるワード": [],
        "マネタイズ判定": Counter(r["マネタイズ判定"] for r in acc_rows),
    }
    all_v = [r["_views"] for r in recent if r["_views"] is not None]
    for no, name, pat in WORDS38:
        in_hit = [r for r in base if re.search(pat, opening(r["1枚目の本文"], 60))]
        in_all = [r for r in recent if re.search(pat, opening(r["1枚目の本文"], 60))]
        summary["38番の20ワード"].append({
            "番号": no, "ワード": name, "伸びた投稿での本数": len(in_hit),
            "集めた全体での本数": len(in_all), "全体での表示回数の中央値": med([r["_views"] for r in in_all]),
        })
    for name, pat in AVOID:
        in_hit = [r for r in base if re.search(pat, opening(r["1枚目の本文"], 60))]
        summary["避けるワード"].append({"ワード": name, "伸びた投稿での本数": len(in_hit)})
    summary["全体の表示回数の中央値"] = med(all_v)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=list))


if __name__ == "__main__":
    main()
