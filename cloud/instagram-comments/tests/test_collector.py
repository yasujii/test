import base64, json, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector as m

class FakeClient:
    public_requests_count = 1
    last_public_response = None
    def __init__(self, pages=None, error=None):
        self.pages = pages or [([{"pk":"1", "text":"sample comment", "created_at":1700000000,
                                  "user":{"username":"do-not-store"}}], "")]
        self.error = error; self.calls = 0
    def media_comments_public_gql_chunk(self, code, end_cursor=""):
        self.calls += 1
        if self.error: raise self.error
        return self.pages[min(self.calls-1, len(self.pages)-1)]
    def media_pk_from_code(self, code): return "123"
    def media_info_gql(self, pk): return SimpleNamespace(caption_text="sample caption", taken_at=1700000000)

class Tests(unittest.TestCase):
    def cfg(self, **kwargs): return {"urls":[m.SAMPLE], "fetch_caption":True, **kwargs}
    def data(self): return m.collect(self.cfg(), FakeClient(), lambda _:None)
    def test_normalize(self):
        self.assertEqual(m.url_and_code(m.SAMPLE+"?igsh=tracking")[0], m.SAMPLE)
    def test_reject_hosts(self):
        for value in ("http://www.instagram.com/p/abcde/", "https://instagram.com.evil/p/abcde/", "https://u:p@instagram.com/p/abcde/", "https://instagram.com:123/p/abcde/", "https://instagram.com/a_profile/"):
            with self.assertRaises(ValueError): m.url_and_code(value)
    def test_bounds(self):
        for change in ({"urls":[]},{"urls":[m.SAMPLE]*6},{"max_comments":1000},{"max_pages":False},{"job_id":"../x"}):
            with self.assertRaises(ValueError): m.validate(self.cfg(**change))
    def test_sample_flag(self):
        with self.assertRaises(ValueError): m.validate({"urls":["https://www.instagram.com/p/ABCdef123/"],"test_sample":True})
    def test_text_and_caption(self):
        d=self.data(); self.assertEqual(d["report"]["comments_received"],1)
        self.assertEqual(d["posts"][0]["caption"],"sample caption")
    def test_no_author_data(self): self.assertNotIn("do-not-store",m.js(self.data()))
    def test_empty_not_success(self):
        d=m.collect(self.cfg(), FakeClient(pages=[([],"")]), lambda _:None)
        self.assertFalse(d["report"]["real_comment_text_received"])
    def test_page_limit(self):
        c=FakeClient(pages=[([{"id":"1","text":"a"}],"next")])
        d=m.collect(self.cfg(max_pages=1),c,lambda _:None)
        self.assertEqual(c.calls,1); self.assertFalse(d["report"]["all_comments_guaranteed"])
    def test_deduplicate(self):
        pages=[([{"id":"1","text":"a"}],"next"),([{"id":"1","text":"a"},{"id":"2","text":"b"}],"")]
        d=m.collect(self.cfg(),FakeClient(pages=pages),lambda _:None)
        self.assertEqual(len(d["comments"]),2)
    def test_max_comments(self):
        c=FakeClient(pages=[([{"id":str(i),"text":"x"} for i in range(50)],"next")])
        d=m.collect(self.cfg(max_comments=3),c,lambda _:None)
        self.assertEqual(len(d["comments"]),3); self.assertEqual(c.calls,1)
    def test_repeated_cursor(self):
        c=FakeClient(pages=[([{"id":"1","text":"a"}],"same")])
        d=m.collect(self.cfg(max_pages=3),c,lambda _:None)
        self.assertEqual(c.calls,2); self.assertEqual(d["report"]["targets"][0]["pagination_stop"],"repeated_cursor")
    def test_rate_limit_stop(self):
        class ClientThrottledError(Exception): pass
        c=FakeClient(error=ClientThrottledError("private error data"))
        d=m.collect(self.cfg(urls=[m.SAMPLE,"https://www.instagram.com/p/ABCdef123/"]),c,lambda _:None)
        self.assertEqual(c.calls,1); self.assertNotIn("private error data",m.js(d))
    def test_csv_formula(self):
        text=m.csv_bytes([{"text":" =1+1"}],("text",)).decode("utf-8-sig")
        self.assertIn("' =1+1",text)
    def test_csv_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            m.save(self.data(),Path(t)/"out")
            self.assertEqual(sorted(p.name for p in (Path(t)/"out").iterdir()),sorted(m.FILES))
            self.assertEqual(json.loads((Path(t)/"out/data.json").read_text())["comments"][0]["text"],"sample comment")
    def test_timestamp(self): self.assertIn("2023",m.stamp(1700000000))
    def test_summary_has_no_text(self):
        self.assertNotIn("sample comment",m.js(m.public_summary(self.data()["report"])))
    def test_log_parse(self):
        self.assertEqual(m.envelope_from_logs("2026-09-24T00:00:00Z IG_EXPORT_START\n2026-09-24T00:00:00Z YWJj\n2026-09-24T00:00:00Z IG_EXPORT_END\n"),"YWJj")
    def test_no_envelope(self):
        with self.assertRaises(ValueError): m.envelope_from_logs("nothing")
    def test_encrypt_roundtrip(self):
        with tempfile.TemporaryDirectory() as t:
            pub=m.make_keys(Path(t)/"key")
            cipher=m.seal(self.data(),pub)
            self.assertNotIn("sample comment",cipher)
            report=m.unseal(cipher,Path(t)/"key/private.pem",Path(t)/"out")
            self.assertEqual(report["comments_received"],1)
    def test_tampered_cipher(self):
        from cryptography.exceptions import InvalidTag
        with tempfile.TemporaryDirectory() as t:
            pub=m.make_keys(Path(t)/"key"); env=json.loads(base64.b64decode(m.seal(self.data(),pub)))
            ct=bytearray(base64.b64decode(env["data"])); ct[-1]^=1
            env["data"]=base64.b64encode(ct).decode()
            broken=base64.b64encode(json.dumps(env).encode()).decode()
            with self.assertRaises(InvalidTag): m.unseal(broken,Path(t)/"key/private.pem",Path(t)/"out")
    def test_private_key_not_overwritten(self):
        with tempfile.TemporaryDirectory() as t:
            m.make_keys(Path(t)/"key")
            with self.assertRaises(m.SetupError): m.make_keys(Path(t)/"key")

if __name__ == "__main__": unittest.main()
