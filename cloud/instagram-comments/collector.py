#!/usr/bin/env python3
"""Bounded, anonymous, read-only Instagram research; public logs contain no text."""
from __future__ import annotations
import argparse, base64, contextlib, csv, hashlib, importlib.metadata, io, json
import logging, os, re, socket, sys, time, uuid, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

PIN = "3.0.13"
FORMAT = "ig-comments-envelope-v1"
SAMPLE = "https://www.instagram.com/p/CjPUjEvDKT4/"
FILES = ("posts.csv", "comments.csv", "data.json", "run_report.json")
POST_FIELDS = ("source_url", "shortcode", "caption", "published_at", "retrieved_at")
COMMENT_FIELDS = ("source_url", "shortcode", "comment_id", "parent_comment_id", "text", "created_at", "retrieved_at")
MAX_BYTES = 2 * 1024 * 1024

class SetupError(RuntimeError): pass

def now(): return datetime.now(timezone.utc).isoformat()

def url_and_code(value):
    p = urlsplit(str(value).strip())
    if p.scheme != "https" or p.hostname not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("Only HTTPS Instagram post/reel URLs are accepted")
    if p.username or p.password or p.port not in (None, 443):
        raise ValueError("Credentials and custom ports are not allowed")
    m = re.fullmatch(r"/(p|reel|tv)/([A-Za-z0-9_-]{5,32})/?", p.path)
    if not m: raise ValueError("Use a post/reel URL, not a profile or share link")
    return f"https://www.instagram.com/{m[1]}/{m[2]}/", m[2]

def validate(config):
    if not isinstance(config, dict): raise ValueError("Request must be an object")
    urls = config.get("urls")
    if not isinstance(urls, list) or not 1 <= len(urls) <= 5:
        raise ValueError("Specify 1 to 5 public post URLs")
    result = dict(config)
    result["urls"] = list(dict.fromkeys(url_and_code(u)[0] for u in urls))
    for k, default, lo, hi in (("max_comments", 30, 1, 100), ("max_pages", 2, 1, 3)):
        n = result.get(k, default)
        if type(n) is not int or not lo <= n <= hi: raise ValueError(f"Invalid {k}")
        result[k] = n
    for k, default in (("fetch_caption", True), ("test_sample", False)):
        if type(result.get(k, default)) is not bool: raise ValueError(f"Invalid {k}")
        result[k] = result.get(k, default)
    if result["test_sample"] and result["urls"] != [SAMPLE]:
        raise ValueError("The sample flag is reserved for the explicit sample URL")
    result["job_id"] = str(result.get("job_id", uuid.uuid4().hex))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", result["job_id"]): raise ValueError("Invalid job_id")
    return result

def private_dir(path):
    path = Path(path)
    if path.is_symlink(): raise SetupError("Refusing symlink output directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path

def write_private(path, data):
    path = Path(path); private_dir(path.parent)
    if path.is_symlink(): raise SetupError("Refusing symlink output file")
    path.write_bytes(data if isinstance(data, bytes) else str(data).encode("utf-8"))
    path.chmod(0o600)

def js(value): return json.dumps(value, ensure_ascii=False, indent=2)

def get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

def stamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        try: return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError): return ""
    return str(value or "")

def new_client():
    from instagrapi import Client
    if importlib.metadata.version("instagrapi") != PIN:
        raise SetupError("Install the pinned requirements before live collection")
    logging.disable(logging.CRITICAL)
    c = Client(public_request_retries_count=1, session_retry_total=0)
    c.read_timeout = 15
    c.request_timeout = 2
    c.handle_exception = lambda _, exc: (_ for _ in ()).throw(exc)
    return c

def failure(exc, client):
    response = getattr(exc, "response", None)
    if response is None: response = getattr(client, "last_public_response", None)
    status = getattr(response, "status_code", None)
    name = type(exc).__name__
    stop = status in (401, 403, 429) or any(x in name for x in
        ("Challenge", "Captcha", "Thrott", "Login", "Unauthorized", "Forbidden", "PleaseWait", "Feedback"))
    return {"exception_type": name, "http_status": status, "stop_for_access_restriction": stop}

def collect(config, client=None, sleep=time.sleep):
    cfg = validate(config)
    c = client or new_client()
    posts, comments, details, seen = [], [], [], set()
    report = {"job_id": cfg["job_id"], "started_at": now(), "environment":
        "github_actions" if os.getenv("GITHUB_ACTIONS") == "true" else ("mac" if sys.platform == "darwin" else "other_python_runtime"),
        "route": "unofficial_public_graphql", "instagram_login_used": False,
        "meta_token_used": False, "test_sample": cfg["test_sample"],
        "all_comments_guaranteed": False, "replies_guaranteed": False, "targets": details}
    for url in cfg["urls"]:
        url, code = url_and_code(url)
        entry = {"source_url": url, "comments_received": 0, "pages_requested": 0,
                 "caption_status": "not_requested", "status": "NOT_TESTED"}
        post = {"source_url": url, "shortcode": code, "caption": "", "published_at": "", "retrieved_at": now()}
        cursor, cursors, restricted = "", set(), False
        try:
            for page in range(cfg["max_pages"]):
                if page: sleep(3)
                entry["pages_requested"] += 1
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    rows, next_cursor = c.media_comments_public_gql_chunk(code, end_cursor=cursor)
                if not isinstance(rows, list): raise ValueError("Unexpected comment response")
                for row in rows:
                    text = get(row, "text", "")
                    if not isinstance(text, str) or not text.strip(): continue
                    identifier = str(get(row, "pk", get(row, "id", "")) or "")
                    key = (code, identifier or hashlib.sha256((text + str(get(row, "created_at", ""))).encode()).hexdigest())
                    if key in seen: continue
                    seen.add(key)
                    comments.append({"source_url": url, "shortcode": code, "comment_id": identifier,
                        "parent_comment_id": "", "text": text,
                        "created_at": stamp(get(row, "created_at", get(row, "created_at_utc", ""))), "retrieved_at": now()})
                    entry["comments_received"] += 1
                    if entry["comments_received"] >= cfg["max_comments"]: break
                entry["more_pages_advertised"] = bool(next_cursor)
                if entry["comments_received"] >= cfg["max_comments"]: break
                if not rows or not next_cursor: break
                if next_cursor in cursors:
                    entry["pagination_stop"] = "repeated_cursor"; break
                cursors.add(next_cursor); cursor = next_cursor
            entry["status"] = "COMMENTS_RECEIVED" if entry["comments_received"] else "NO_COMMENT_TEXT_RETURNED"
        except Exception as exc:
            entry["error"] = failure(exc, c); restricted = entry["error"]["stop_for_access_restriction"]
            entry["status"] = "PARTIAL_COMMENTS" if entry["comments_received"] else "REQUEST_FAILED"
        if cfg["fetch_caption"] and not restricted:
            try:
                sleep(3)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    media = c.media_info_gql(c.media_pk_from_code(code))
                post["caption"] = str(get(media, "caption_text", "") or "")
                post["published_at"] = stamp(get(media, "taken_at"))
                entry["caption_status"] = "RECEIVED" if post["caption"] else "EMPTY_CAPTION"
            except Exception as exc:
                entry["caption_status"] = "REQUEST_FAILED"
                entry["caption_error"] = failure(exc, c)
                restricted = entry["caption_error"]["stop_for_access_restriction"]
        posts.append(post); details.append(entry)
        if restricted:
            report["stop_reason"] = "access_restriction_no_retry"; break
        if len(details) < len(cfg["urls"]): sleep(3)
    report.update({"finished_at": now(), "requested_posts": len(cfg["urls"]), "processed_posts": len(posts),
        "comments_received": len(comments), "posts_with_caption": sum(bool(p["caption"]) for p in posts),
        "real_comment_text_received": bool(comments), "target_selection_verified": False,
        "result": "COMMENTS_RECEIVED" if comments else "NO_COMMENT_TEXT_VERIFIED",
        "comment_data_sha256": hashlib.sha256(js(comments).encode()).hexdigest(),
        "public_requests": getattr(c, "public_requests_count", None)})
    return {"posts": posts, "comments": comments, "report": report}

def csv_bytes(rows, fields):
    f = io.StringIO(newline="")
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader()
    for row in rows:
        safe = {}
        for key in fields:
            value = row.get(key, "")
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")): value = "'" + value
            safe[key] = value
        w.writerow(safe)
    return f.getvalue().encode("utf-8-sig")

def file_map(data):
    return {"posts.csv": csv_bytes(data["posts"], POST_FIELDS),
            "comments.csv": csv_bytes(data["comments"], COMMENT_FIELDS),
            "data.json": js(data).encode(), "run_report.json": js(data["report"]).encode()}

def save(data, output):
    for name, value in file_map(data).items(): write_private(Path(output) / name, value)

def seal(data, public_key_b64):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pub = serialization.load_pem_public_key(base64.b64decode(public_key_b64, validate=True))
    if not isinstance(pub, rsa.RSAPublicKey) or not 2048 <= pub.key_size <= 8192: raise ValueError("Invalid RSA key")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        for name, value in file_map(data).items(): z.writestr(name, value)
    if len(raw.getvalue()) > MAX_BYTES: raise ValueError("Export too large")
    key, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    b64 = lambda b: base64.b64encode(b).decode()
    encrypted = AESGCM(key).encrypt(nonce, raw.getvalue(), FORMAT.encode())
    wrapped = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return b64(json.dumps({"format": FORMAT, "key": b64(wrapped), "nonce": b64(nonce), "data": b64(encrypted)}).encode())

def make_keys(directory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    directory = private_dir(directory)
    if (directory / "private.pem").exists(): raise SetupError("Refusing to overwrite a private key")
    k = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    write_private(directory / "private.pem", k.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    write_private(directory / "public.b64", base64.b64encode(public))
    return base64.b64encode(public).decode()

def unseal(value, key_path, output):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    b64 = lambda x: base64.b64decode(x, validate=True)
    env = json.loads(b64(value))
    if env.get("format") != FORMAT: raise ValueError("Unknown envelope format")
    private = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    key = private.decrypt(b64(env["key"]), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    raw = AESGCM(key).decrypt(b64(env["nonce"]), b64(env["data"]), FORMAT.encode())
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if sorted(z.namelist()) != sorted(FILES) or sum(i.file_size for i in z.infolist()) > MAX_BYTES * 10:
            raise ValueError("Unexpected export files or size")
        for name in FILES: write_private(Path(output) / name, z.read(name))
    return json.loads((Path(output) / "run_report.json").read_text())

def envelope_from_logs(text):
    blocks, current = [], None
    for line in text.splitlines():
        line = re.sub(r"^\d{4}-\d{2}-\d{2}T\S+\s+", "", line).strip()
        if line == "IG_EXPORT_START": current = []
        elif line == "IG_EXPORT_END" and current is not None:
            blocks.append("".join(current)); current = None
        elif current is not None and re.fullmatch(r"[A-Za-z0-9+/=]+", line): current.append(line)
    if not blocks: raise ValueError("No complete encrypted export in logs")
    return blocks[-1]

def public_summary(report):
    return {k: report.get(k) for k in ("job_id", "environment", "route", "test_sample", "result", "comments_received",
        "posts_with_caption", "real_comment_text_received", "all_comments_guaranteed", "processed_posts", "stop_reason")}

def discover(username, ig_id, limit):
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username) or not re.fullmatch(r"\d+", ig_id):
        raise ValueError("Invalid Instagram identifier")
    token = os.environ.get("IG_GRAPH_TOKEN", "")
    if not token: raise SetupError("Fresh IG_GRAPH_TOKEN must be supplied through a secret environment variable")
    if not 1 <= limit <= 5: raise ValueError("limit must be 1 to 5")
    fields = f"business_discovery.username({username}){{username,media.limit({limit}){{id,caption,permalink,timestamp,comments_count}}}}"
    request = Request(f"https://graph.facebook.com/v26.0/{ig_id}?" + urlencode({"fields": fields}),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response: value = json.load(response)
    return value.get("business_discovery", {}).get("media", {}).get("data", [])

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "runner"):
        p = sub.add_parser(name); p.add_argument("--request", type=Path, required=True)
        p.add_argument("--out", type=Path, default=Path("output") / datetime.now().strftime("%Y%m%d-%H%M%S"))
    p = sub.add_parser("keygen"); p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("decrypt"); p.add_argument("--logs", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("discover"); p.add_argument("--username", required=True)
    p.add_argument("--ig-id", default=os.getenv("IG_GRAPH_USER_ID", "")); p.add_argument("--limit", type=int, default=3)
    p.add_argument("--out", type=Path, required=True)
    sub.add_parser("doctor")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = {"python": sys.version.split()[0], "platform": sys.platform}
            for package in ("instagrapi", "cryptography"):
                try: result[package] = importlib.metadata.version(package)
                except importlib.metadata.PackageNotFoundError: result[package] = "missing"
            for host in ("api.github.com", "www.instagram.com", "pypi.org"):
                try: socket.getaddrinfo(host, 443); result[host] = "dns_ok"
                except OSError: result[host] = "dns_failed"
            print(js(result)); return 0
        if args.command == "keygen":
            make_keys(args.out); print("Private key created locally; never commit or upload private.pem"); return 0
        if args.command == "decrypt":
            report = unseal(envelope_from_logs(args.logs.read_text(encoding="utf-8-sig")), args.key, args.out)
            print(js(public_summary(report))); return 0
        if args.command == "discover":
            rows = discover(args.username.lstrip("@"), args.ig_id, args.limit)
            write_private(args.out, js(rows)); print(js({"posts_found": len(rows)})); return 0
        config = validate(json.loads(args.request.read_text(encoding="utf-8-sig")))
        data = collect(config)
        if args.command == "collect": save(data, args.out)
        print(js(public_summary(data["report"])))
        if args.command == "runner":
            recipient = config.get("recipient_public_key_b64", "")
            if recipient:
                encrypted = seal(data, recipient)
                print("IG_EXPORT_START")
                for i in range(0, len(encrypted), 1000): print(encrypted[i:i+1000])
                print("IG_EXPORT_END")
            else:
                print("PROBE_ONLY: no public key supplied; comment text is not published or persisted")
        return 0 if data["report"]["real_comment_text_received"] else 3
    except Exception as exc:
        print(js({"result": "STOPPED", "exception_type": type(exc).__name__}), file=sys.stderr)
        return 2

if __name__ == "__main__": raise SystemExit(main())
