#!/usr/bin/env python3
"""One authorized login; read public posts; export ciphertext, never credentials."""
from __future__ import annotations
import base64
import contextlib
import csv
import io
import json
import logging
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PIN = "3.0.13"
FORMAT = "ig-auth-research-v1"
NEEDED = ("IG_RESEARCH_LOGIN", "IG_RESEARCH_PASSWORD")
class HumanVerificationRequired(RuntimeError): pass

def now(): return datetime.now(timezone.utc).isoformat()
def field(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
def refuse_verification(*args, **kwargs): raise HumanVerificationRequired()

def validate(config):
    if config.get("authorize_password_login") is not True: raise ValueError("Login not authorized")
    urls = config.get("urls", [])
    if not isinstance(urls, list) or not 1 <= len(urls) <= 5: raise ValueError("Expected 1-5 URLs")
    out = []
    for url in urls:
        p = urlsplit(url)
        if p.scheme != "https" or p.hostname not in ("instagram.com", "www.instagram.com"):
            raise ValueError("Invalid host")
        if p.username or p.password or p.port not in (None, 443): raise ValueError("Invalid authority")
        m = re.fullmatch(r"/(p|reel|tv)/([A-Za-z0-9_-]{5,32})/?", p.path)
        if not m: raise ValueError("Expected a post URL")
        item = (f"https://www.instagram.com/{m[1]}/{m[2]}/", m[2])
        if item not in out: out.append(item)
    for k, high in (("max_comments", 30), ("max_total", 100), ("max_pages", 2)):
        if type(config.get(k)) is not int or not 1 <= config[k] <= high: raise ValueError("Invalid limit")
    return out

def error_info(exc):
    status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    verification = any(s in name.lower() for s in ("challenge", "captcha", "twofactor", "verification"))
    stop = verification or status in (401,403,429) or any(s in name.lower() for s in
        ("thrott", "login", "forbidden", "unauthorized", "pleasewait", "feedback"))
    return {"type": name, "http_status": status, "verification_required": verification, "stop": stop}

def stamp(v):
    if isinstance(v, datetime): return v.isoformat()
    if isinstance(v, (int,float)):
        try: return datetime.fromtimestamp(v, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError): return ""
    return str(v or "")

def collect(config, client, login, password, code="", sleep=time.sleep):
    targets = validate(config)
    report = {"started_at": now(), "login_attempted": False, "login_succeeded": False,
        "route": "authenticated_unofficial_mobile_api", "all_comments_guaranteed": False,
        "targets": [], "result": "NOT_STARTED"}
    data = {"posts": [], "comments": [], "report": report}
    client.challenge_resolve = refuse_verification
    client.challenge_code_handler = refuse_verification
    client.change_password_handler = refuse_verification
    client.handle_exception = lambda _, exc: (_ for _ in ()).throw(exc)
    # No cookie extraction, automatic verification, password changes or login retries.
    report["login_attempted"] = True
    try:
        ok = client.login(login, password, verification_code=code)
        if not ok or not client.user_id: raise RuntimeError("No authenticated session")
        report["login_succeeded"] = True
    except Exception as exc:
        report.update(result="LOGIN_FAILED", error=error_info(exc), finished_at=now())
        return data
    seen = set()
    for url, shortcode in targets:
        item = {"url": url, "status": "NOT_TESTED", "count": 0}
        report["targets"].append(item)
        try:
            sleep(3)
            media = client.media_info_v1(client.media_pk_from_code(shortcode))
            owner = field(media, "user")
            owner_id = str(field(owner, "pk", ""))
            if not owner_id: raise ValueError("Missing owner")
            if owner_id == str(client.user_id):
                item["status"] = "OWN_POST_SKIPPED"
                continue
            sleep(3)
            author = client.user_info_v1(owner_id)
            if field(author, "is_private") is not False:
                item["status"] = "PRIVATE_OR_UNKNOWN_SKIPPED"
                continue
            data["posts"].append({"source_url": url, "caption": field(media,"caption_text", "") or "",
                "published_at": stamp(field(media,"taken_at")), "retrieved_at": now()})
            min_id, max_id, cursors = "", "", set()
            for page in range(config["max_pages"]):
                sleep(3)
                rows, next_min, next_max = client.media_comments_v1_chunk(
                    str(field(media,"id") or field(media,"pk")), min_id=min_id, max_id=max_id)
                if not isinstance(rows, list): raise ValueError("Invalid comments response")
                for row in rows:
                    text = field(row,"text", "")
                    if not isinstance(text,str) or not text.strip(): continue
                    if str(field(field(row,"user"),"pk", "")) in (owner_id, str(client.user_id)): continue
                    identifier = str(field(row,"pk", field(row,"id", "")) or "")
                    key = (shortcode, identifier or text)
                    if key in seen: continue
                    seen.add(key)
                    data["comments"].append({"source_url":url,"comment_id":identifier,"text":text,
                        "created_at":stamp(field(row,"created_at_utc", field(row,"created_at"))),"retrieved_at":now()})
                    item["count"] += 1
                    if item["count"] >= config["max_comments"] or len(data["comments"]) >= config["max_total"]: break
                if item["count"] >= config["max_comments"] or len(data["comments"]) >= config["max_total"]: break
                pair = (str(next_min or ""), str(next_max or ""))
                if not rows or not any(pair) or pair in cursors: break
                cursors.add(pair)
                min_id, max_id = pair
            item["status"] = "COMMENTS_RECEIVED" if item["count"] else "NO_COMMENT_TEXT"
        except Exception as exc:
            item.update(status="FAILED", error=error_info(exc))
            if item["error"]["stop"]:
                report["stop_reason"] = "access_restriction"
                break
        if len(data["comments"]) >= config["max_total"]: break
    report.update(finished_at=now(), comments_received=len(data["comments"]),
        result="COMMENTS_RECEIVED" if data["comments"] else "NO_COMMENT_TEXT")
    return data

def csv_data(rows, headers):
    s = io.StringIO(newline=""); w = csv.DictWriter(s, fieldnames=headers); w.writeheader()
    for row in rows:
        values = {}
        for k in headers:
            v = str(row.get(k, ""))
            values[k] = "'"+v if v.lstrip().startswith(("=","+","-","@")) else v
        w.writerow(values)
    return s.getvalue().encode("utf-8-sig")

def seal(data, public_b64):
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import padding,rsa
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pub = serialization.load_pem_public_key(base64.b64decode(public_b64,validate=True))
    if not isinstance(pub,rsa.RSAPublicKey) or pub.key_size < 3072: raise ValueError("Invalid export key")
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.json",json.dumps(data,ensure_ascii=False,indent=2))
        z.writestr("run_report.json",json.dumps(data["report"],ensure_ascii=False,indent=2))
        z.writestr("posts.csv",csv_data(data["posts"],("source_url","caption","published_at","retrieved_at")))
        z.writestr("comments.csv",csv_data(data["comments"],("source_url","comment_id","text","created_at","retrieved_at")))
    key,nonce=AESGCM.generate_key(bit_length=256),os.urandom(12)
    b64=lambda x:base64.b64encode(x).decode()
    return {"format":FORMAT,"key":b64(pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))),
        "nonce":b64(nonce),"data":b64(AESGCM(key).encrypt(nonce,raw.getvalue(),FORMAT.encode()))}

def main():
    missing=[k for k in NEEDED if not os.environ.get(k)]
    if missing:
        print(json.dumps({"result":"SECRETS_MISSING","login_attempted":False,"missing":missing}))
        return 2
    try:
        config=json.loads(Path(sys.argv[1]).read_text())
        validate(config)
        # Validate the recipient BEFORE sending any authentication request.
        seal({"posts":[],"comments":[],"report":{}},config["recipient_public_key_b64"])
        import importlib.metadata
        if importlib.metadata.version("instagrapi") != PIN: raise ValueError("Version mismatch")
        logging.disable(logging.CRITICAL)
        with open(os.devnull,"w") as null, contextlib.redirect_stdout(null),contextlib.redirect_stderr(null):
            from instagrapi import Client
            client=Client(public_request_retries_count=1,session_retry_total=0)
            client.read_timeout=20
            data=collect(config,client,os.environ[NEEDED[0]],os.environ[NEEDED[1]],os.environ.get("IG_RESEARCH_2FA_CODE", ""))
        envelope=seal(data,config["recipient_public_key_b64"])
        p=Path("authenticated-export.json")
        p.write_text(json.dumps(envelope)); p.chmod(0o600)
        r=data["report"]
        print(json.dumps({k:r.get(k) for k in ("result","login_attempted","login_succeeded","comments_received","stop_reason")},ensure_ascii=False))
        if r.get("error"): print(json.dumps(r["error"]))
        return 0 if r.get("comments_received",0)>0 else 3
    except Exception as exc:
        print(json.dumps({"result":"PREPARATION_OR_EXPORT_FAILED","exception_type":type(exc).__name__}))
        return 4
if __name__ == "__main__": raise SystemExit(main())
