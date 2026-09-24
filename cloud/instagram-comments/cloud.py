#!/usr/bin/env python3
"""One command: prepare key, push request, wait for Actions, decrypt, save CSV/JSON.
No Instagram or Meta credential is used. GitHub auth comes only from GH_TOKEN or gh.
The connector-only path uses --prepare-only and is documented in HANDOFF.md.
"""
from __future__ import annotations
import argparse, base64, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, urlopen
import collector as c
ROOT=Path(__file__).resolve().parent
REPO="yasujii/test"
BRANCH="codex/instagram-cloud-probe-20260924"
REQUEST_PATH="cloud/instagram-comments/request.json"
WORKFLOW=".github/workflows/instagram-comments.yml"

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def token():
    value=os.getenv("GH_TOKEN", "") or os.getenv("GITHUB_TOKEN", "")
    if value: return value
    if shutil.which("gh"):
        result=subprocess.run(["gh","auth","token"],capture_output=True,text=True,timeout=15)
        if result.returncode==0 and result.stdout.strip(): return result.stdout.strip()
    raise c.SetupError("Use the authorized GitHub connector, or provide GH_TOKEN securely; never paste it in chat")

def api(path, auth, method="GET", payload=None):
    if not path.startswith("/repos/"): raise ValueError("Unsupported GitHub path")
    request=Request("https://api.github.com"+path,method=method,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Accept":"application/vnd.github+json", "Authorization":"Bearer "+auth,
                 "X-GitHub-Api-Version":"2026-03-10", "Content-Type":"application/json"})
    with build_opener(NoRedirect).open(request,timeout=30) as response:
        raw=response.read(); return json.loads(raw) if raw else {}

def get_logs(repo, job_id, auth):
    request=Request(f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
                    headers={"Authorization":"Bearer "+auth,"Accept":"application/vnd.github+json"})
    try:
        with build_opener(NoRedirect).open(request,timeout=30) as response: return response.read().decode("utf-8-sig")
    except HTTPError as exc:
        if exc.code not in (301,302,303,307): raise
        target=exc.headers.get("Location","")
        p=urlsplit(target)
        allowed=(".githubusercontent.com",".blob.core.windows.net",".actions.githubusercontent.com")
        if p.scheme!="https" or not p.hostname or not any(p.hostname.endswith(s) for s in allowed):
            raise c.SetupError("Unexpected GitHub log redirect")
        # Never forward GitHub credentials to the signed download URL.
        with urlopen(Request(target),timeout=30) as response: return response.read().decode("utf-8-sig")

def prepare(urls, max_comments=30, fetch_caption=True):
    job=uuid.uuid4().hex; state=ROOT/".state"/job
    pub=c.make_keys(state)
    config=c.validate({"job_id":job,"urls":urls or [c.SAMPLE],"max_comments":max_comments,
        "max_pages":2,"fetch_caption":fetch_caption,"test_sample":not bool(urls),"recipient_public_key_b64":pub})
    c.write_private(state/"request.json",c.js(config))
    return state,config

def run(repo,branch,state,config,timeout=480):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo): raise ValueError("Invalid repository")
    auth=token(); meta=api(f"/repos/{repo}",auth)
    if meta.get("private"): raise c.SetupError("This zero-fee route intentionally refuses private repository runner costs")
    path=f"/repos/{repo}/contents/{REQUEST_PATH}"
    try: old=api(path+"?"+urlencode({"ref":branch}),auth)
    except HTTPError as exc:
        if exc.code!=404: raise
        old={}
    payload={"message":"research: request anonymous Instagram comments "+config["job_id"],"branch":branch,
             "content":base64.b64encode(c.js(config).encode()).decode()}
    if old.get("sha"): payload["sha"]=old["sha"]
    committed=api(path,auth,"PUT",payload); sha=committed["commit"]["sha"]
    c.write_private(state/"github.json",c.js({"repo":repo,"branch":branch,"commit":sha}))
    deadline=time.monotonic()+timeout; run_info=None
    while time.monotonic()<deadline:
        runs=api(f"/repos/{repo}/actions/runs?"+urlencode({"head_sha":sha,"per_page":20}),auth).get("workflow_runs",[])
        run_info=next((r for r in runs if r.get("path")==WORKFLOW),None)
        if run_info and run_info.get("status")=="completed": break
        time.sleep(8)
    else: raise c.SetupError("Timed out; keep the private key and resume reading this run without starting new requests")
    jobs=api(f"/repos/{repo}/actions/runs/{run_info['id']}/jobs",auth).get("jobs",[])
    logs="\n".join(get_logs(repo,j["id"],auth) for j in jobs if j.get("name")=="collect")
    c.write_private(state/"job.log",logs)
    out=ROOT/"output"/config["job_id"]
    report=c.unseal(c.envelope_from_logs(logs),state/"private.pem",out)
    print(c.js({**c.public_summary(report),"saved_to":str(out),"workflow_run":run_info["html_url"]}))
    return 0 if report["real_comment_text_received"] else 3

def main():
    p=argparse.ArgumentParser(); p.add_argument("--url",action="append",default=[])
    p.add_argument("--urls-file",type=Path); p.add_argument("--repo",default=REPO); p.add_argument("--branch",default=BRANCH)
    p.add_argument("--max-comments",type=int,default=30); p.add_argument("--prepare-only",action="store_true")
    p.add_argument("--local",action="store_true"); p.add_argument("--skip-caption",action="store_true")
    a=p.parse_args()
    try:
        urls=list(a.url)
        if a.urls_file: urls += [s.strip() for s in a.urls_file.read_text().splitlines() if s.strip() and not s.lstrip().startswith("#")]
        if a.local:
            cfg=c.validate({"urls":urls or [c.SAMPLE],"max_comments":a.max_comments,"fetch_caption":not a.skip_caption,"test_sample":not bool(urls)})
            data=c.collect(cfg); out=ROOT/"output"/cfg["job_id"]; c.save(data,out)
            print(c.js({**c.public_summary(data["report"]),"saved_to":str(out)})); return 0 if data["comments"] else 3
        state,cfg=prepare(urls,a.max_comments,not a.skip_caption)
        if a.prepare_only:
            print(c.js({"request_file":str(state/"request.json"),"private_key_file":str(state/"private.pem"),
                "connector_destination":REQUEST_PATH,"branch":a.branch,"repo":a.repo})); return 0
        return run(a.repo,a.branch,state,cfg)
    except Exception as exc:
        print(c.js({"result":"STOPPED","exception_type":type(exc).__name__,
            "next":"Read HANDOFF.md; do not expose credentials or blindly repeat requests"}),file=sys.stderr); return 2

if __name__=="__main__": raise SystemExit(main())
