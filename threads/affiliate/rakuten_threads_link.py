#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
楽天アフィリエイトで商品を探し、Threads用のSNSリンク（a.r10.to/…）を作る。

- ログイン情報は環境変数 RAKUTEN_COOKIES から読むだけ。値はファイル・画面に出さない。
- 楽天アフィリエイトの画面と同じ窓口を、人が操作するのと同じ間隔（2.5秒以上）で使う。
- 医薬品（便秘薬・整腸剤・漢方）は選ばないこと（CLAUDE.md）。

使い方:
  python3 rakuten_threads_link.py search "めかぶ" "寒天 粉" --rmin 10      # 料率10%以上で検索（口コミ順に表示）
  python3 rakuten_threads_link.py link <shop_id> <item_id> <item_url>      # Threads用リンクを作って行き先を確認
"""
import base64, html, http.cookiejar, json, os, re, subprocess, sys, time
import urllib.parse, urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
THREADS_MEDIA_ID = "0103"  # 楽天アフィリエイト画面の「Threads」ボタンと同じ番号


def _opener():
    s = os.environ["RAKUTEN_COOKIES"].strip()
    if s.startswith("b64:"):
        s = s[4:]
    s += "=" * (-len(s) % 4)
    jar = http.cookiejar.CookieJar()
    for c in json.loads(base64.b64decode(s)):
        dom = c["domain"]
        jar.set_cookie(http.cookiejar.Cookie(0, c["name"], c["value"], None, False, dom, True, dom.startswith("."),
                                             c.get("path", "/"), True, c.get("secure", False),
                                             int(c.get("expirationDate") or time.time() + 86400), False, None, None, {}))
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "ja")]
    return op, jar


OP, JAR = _opener()


def _retry(f, n=4):
    for i in range(n):
        try:
            return f()
        except Exception as e:
            last = e
            time.sleep(4)
    raise last


def search(keyword, rmin=None, page=1):
    q = {"sitem": keyword, "pno": page}
    if rmin:
        q["rmin"] = rmin
    url = "https://affiliate.rakuten.co.jp/search?" + urllib.parse.urlencode(q)
    b = _retry(lambda: OP.open(url, timeout=40).read().decode("utf-8", "replace"))
    m = re.search(r"<search-view(.*?)>", b, re.S)
    attrs = dict((k, html.unescape(v)) for k, v in re.findall(r'\s:?([a-z_]+)="([^"]*)"', m.group(1)))
    if attrs.get("is_logged_in") != "true":
        sys.exit("楽天アフィリエイトにログインできていません（RAKUTEN_COOKIES の期限切れ）。ログインし直して入れ替えてください。")
    sr = attrs.get("search_results")
    return json.loads(sr)["data"] if sr and sr != "null" else []


def make_link(shop_id, item_id, item_url):
    u = (f"https://affiliate.rakuten.co.jp/link/pc/item?me_id=1{shop_id}&item_id={item_id}"
         f"&me_url={urllib.parse.quote(item_url, safe='')}")
    b = _retry(lambda: OP.open(u, timeout=40).read().decode("utf-8", "replace"))
    d = json.loads(html.unescape(re.search(r'\s:data="([^"]*)"', re.search(r"<item-view(.*?)>", b, re.S).group(1)).group(1)))
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', b)
    xsrf = [c.value for c in JAR if c.name == "XSRF-TOKEN"]
    body = {"item_data": d["item_data"], "special_merchant": d["item_settings"].get("special_merchant"),
            "pb_id": d["item_settings"].get("pb_id"), "rafmid": THREADS_MEDIA_ID}
    h = {"Content-Type": "application/json", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
         "Referer": u, "Origin": "https://affiliate.rakuten.co.jp"}
    if csrf:
        h["X-CSRF-TOKEN"] = csrf.group(1)
    if xsrf:
        h["X-XSRF-TOKEN"] = urllib.parse.unquote(xsrf[-1])
    req = urllib.request.Request("https://affiliate.rakuten.co.jp/api/link/item/shorturl",
                                 data=json.dumps(body).encode(), headers=h, method="POST")
    short = _retry(lambda: json.loads(OP.open(req, timeout=40).read()).get("item_short_url"))
    loc = ""
    for _ in range(3):
        loc = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{redirect_url}", short],
                             capture_output=True, text=True).stdout
        if loc:
            break
        time.sleep(2)
    m = re.search(r"pc=([^&]+)", loc)
    dest = urllib.parse.unquote(m.group(1)) if m else "確認できず"
    return short, dest, THREADS_MEDIA_ID in loc


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "search":
        args = sys.argv[2:]
        rmin = None
        if "--rmin" in args:
            i = args.index("--rmin"); rmin = args[i + 1]; args = args[:i] + args[i + 2:]
        seen = {}
        for kw in args:
            for x in search(kw, rmin):
                seen[(x["shop_id"], x["item_id"])] = x
            time.sleep(2.5)
        rows = sorted(seen.values(), key=lambda x: (-float(x["item_rate"].rstrip("%")) >= -19, -int(x["review_num"] or 0)))
        for x in sorted(seen.values(), key=lambda x: -int(x["review_num"] or 0))[:40]:
            a = x.get("affiliate") or {}
            print("\t".join(map(str, [x["item_rate"], str(a.get("aflrate_end_time", ""))[:10], x["price"],
                  f'送料{(x.get("shipping") or {}).get("price")}', f'口コミ{x["review_num"]}', x["review_ave"],
                  x["shop_name"][:14], x["item_name"][:60], x["shop_id"], x["item_id"], x["item_url"]])))
    elif sys.argv[1] == "link":
        short, dest, ok = make_link(*sys.argv[2:5])
        print(short, "→", dest, "Threads印あり" if ok else "印なし")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
