"""v4.2 analytics regression tests using synthetic AKUZ .log files only."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import unittest

import akuz_app as app
from akuz_analytics import recognize_error,refresh,connect,detail,read_js
from akuz_store import load_store,save_store,sha256


class ErrorAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=load_store(self.root)

    def report(self,name,text,path="/srv/akuz/akuz.log",base=date(2026,9,22)):
        raw=self.root/(name+".log")
        raw.write_text(text,encoding="utf-8")
        sources=[dict(name=raw.name,date=base.isoformat() if base else "",
                      sha256=sha256(raw),remote_path=path)]
        return app._publish(self.root,self.store,"key-"+name,raw,base,sources,
                            name,"single")

    @staticmethod
    def event(time,patient="1234",suffix=""):
        return (time+",AKUZ,session,user: "
                "System.Runtime.Serialization.SerializationException: Failed Patient "+
                patient+"\n at AKUZ.Serialize()\n"+suffix)

    def test_normalized_fingerprint_and_family(self):
        first=recognize_error(self.event("12:00:00.100", "1234"))
        second=recognize_error(self.event("13:00:00.100", "9876"))
        self.assertEqual(first["fp"],second["fp"])
        self.assertEqual(first["family"],"Сериализация")
        self.assertEqual(first["template"],"failed patient {n}")
        self.assertIsNone(recognize_error("12:00:00.100,AKUZ,s,user: Fine\n"))

    def test_snapshot_dedup_incremental_and_removed_report(self):
        old=self.event("12:00:00.100")+self.event("12:00:01.100","5555")
        self.report("first",old)
        info=refresh(self.root)
        self.assertEqual(info["distinct_errors"],2)
        self.assertEqual(info["report_count"],1)
        self.assertEqual(refresh(self.root)["distinct_errors"],2)

        self.report("second",old+self.event("13:00:00.100","7777"))
        info=refresh(self.root)
        self.assertEqual(info["distinct_errors"],3)
        self.assertEqual(info["report_count"],2)
        fp=info["groups"][0]["fp"]
        with connect(self.root) as c:
            snapshot=detail(c,fp)
        self.assertEqual(snapshot["days"],[("2026-09-22",3)])
        self.assertEqual(snapshot["hours"],[("2026-09-22 12",2),
                                           ("2026-09-22 13",1)])
        self.assertEqual(len(snapshot["items"]),3)
        self.assertTrue((self.root/"data"/("error_"+fp+".js")).is_file())
        self.assertTrue((self.root/"data"/"analytics.js").is_file())
        self.assertEqual(len(read_js(self.root/"data"/"analytics.js",
                                     "window.AKUZ_ANALYTICS=")["groups"]),1)

        first_id=next(k for k,v in self.store["reports"].items() if v["label"]=="first")
        self.store["reports"].pop(first_id)
        save_store(self.root,self.store)
        shutil.rmtree(self.root/"reports"/first_id)
        self.assertEqual(refresh(self.root)["distinct_errors"],3)
        second_id=next(iter(self.store["reports"]))
        self.store["reports"].clear()
        save_store(self.root,self.store)
        shutil.rmtree(self.root/"reports"/second_id)
        info=refresh(self.root)
        self.assertEqual(info["distinct_errors"],0)
        self.assertEqual(read_js(self.root/"data"/"analytics.js",
                                 "window.AKUZ_ANALYTICS=")["groups"],[])

    def test_conflicts_are_disclosed_and_excluded_from_graph(self):
        self.report("first",self.event("12:00:00.100"))
        self.report("other",self.event("12:00:00.100","different patient"))
        info=refresh(self.root)
        self.assertEqual(info["distinct_errors"],2)
        self.assertEqual(info["ambiguous"],2)
        with connect(self.root) as db:
            groups=[detail(db,g["fp"]) for g in info["groups"]]
        self.assertEqual(sum(x["dated"] for x in groups),0)

    def test_identical_bytes_with_inconsistent_date_are_not_plotted(self):
        sample=self.event("12:00:00.100")
        self.report("date_one",sample,base=date(2026,9,21))
        self.report("date_two",sample,base=date(2026,9,22))
        state=refresh(self.root)
        self.assertEqual(state["distinct_errors"],1)
        self.assertEqual(state["date_conflicts"],1)
        self.assertEqual(state["groups"][0]["dated"],0)
        with connect(self.root) as db:
            self.assertEqual(detail(db,state["groups"][0]["fp"])["days"],[])

    def test_unknown_date_never_assumed_from_file_mtime(self):
        self.report("undated",self.event("12:00:00.100"),base=None)
        info=refresh(self.root)
        self.assertEqual(info["distinct_errors"],1)
        self.assertEqual(info["groups"][0]["dated"],0)
        fp=info["groups"][0]["fp"]
        with connect(self.root) as db:
            group=detail(db,fp)
        self.assertEqual(group["days"],[])
        self.assertIsNone(group["items"][0]["day"])

    def test_provenance_date_correction_invalidates_analytics(self):
        report=self.report("correctable",self.event("12:00:00.100"))
        before=refresh(self.root)
        self.assertEqual(before["groups"][0]["dated"],1)
        rid=report["id"]
        # The HTML catalog did not change. Updating the operator's source
        # date in inventory MUST invalidate cached calendar aggregates.
        self.store["reports"][rid]["sources"][0]["date"]=""
        save_store(self.root,self.store)
        unknown=refresh(self.root)
        self.assertEqual(unknown["groups"][0]["dated"],0)
        self.assertEqual(unknown["distinct_errors"],1)
        self.store["reports"][rid]["sources"][0]["date"]="2026-09-22"
        save_store(self.root,self.store)
        restored=refresh(self.root)
        self.assertEqual(restored["groups"][0]["dated"],1)

    def test_catalog_base_date_without_source_date_is_not_proof(self):
        report=self.report("source_unknown",self.event("12:00:00.100"))
        self.store["reports"][report["id"]]["sources"][0]["date"]=""
        save_store(self.root,self.store)
        report_meta=read_js(self.root/"reports"/report["id"]/"data"/"catalog.js",
                            "window.AKUZ_DATA=")["meta"]
        self.assertEqual(report_meta["base_date"],"2026-09-22")
        summary=refresh(self.root)
        self.assertEqual(summary["groups"][0]["dated"],0)

    def test_date_is_no_longer_silently_inferred_from_mtime(self):
        script=(Path(__file__).resolve().parents[1]/"app_controls.js").read_text("utf-8")
        self.assertIn("Object.assign({checked:false,date:''},f)",script)
        self.assertIn("date.value=f.date??''",script)

    def test_two_hosts_same_path_and_content_are_distinct_sources(self):
        sample=self.event("12:00:00.100")
        a=self.report("host_a",sample,base=date(2026,9,21))
        b=self.report("host_b",sample,base=date(2026,9,22))
        self.store["reports"][a["id"]]["sources"][0]["host"]="linux-a"
        self.store["reports"][b["id"]]["sources"][0]["host"]="linux-b"
        save_store(self.root,self.store)
        result=refresh(self.root)
        self.assertEqual(result["distinct_errors"],2)
        self.assertEqual(result["date_conflicts"],0)
        fp=result["groups"][0]["fp"]
        with connect(self.root) as db:
            aggregate=detail(db,fp)
        self.assertEqual(aggregate["days"],
                         [("2026-09-21",1),("2026-09-22",1)])

    def test_two_paths_same_bytes_are_not_one_source(self):
        sample=self.event("12:00:00.100")
        self.report("path_a",sample,path="/srv/akuz/a.log")
        self.report("path_b",sample,path="/srv/akuz/b.log")
        summary=refresh(self.root)
        self.assertEqual(summary["distinct_errors"],2)
        self.assertEqual(summary["date_conflicts"],0)

    def test_local_html_api_serves_analytics_and_event_navigation(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        report=self.report("local_http",self.event("14:01:03.123"))
        shutil.copy2(app.ROOT/"errors.html",self.root/"errors.html")
        server=ThreadingHTTPServer(("127.0.0.1",0),
            app.make_handler(self.root,app.State(),0))
        server.RequestHandlerClass=app.make_handler(
            self.root,app.State(),server.server_port)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            try:
                conn.request("GET","/errors.html")
                page=conn.getresponse()
                html=page.read().decode("utf-8")
                self.assertEqual(page.status,200)
                self.assertIn("Аналитика ошибок",html)
                conn.request("GET","/data/analytics.js")
                listing=conn.getresponse()
                summary=listing.read().decode("utf-8")
                self.assertEqual(listing.status,200)
                self.assertIn("window.AKUZ_ANALYTICS=",summary)
                info=refresh(self.root)
                fp=info["groups"][0]["fp"]
                conn.request("GET","/data/error_"+fp+".js")
                group=conn.getresponse()
                self.assertEqual(group.status,200)
                self.assertIn("window.AKUZ_ERROR_DETAIL=",
                              group.read().decode("utf-8"))
                conn.request("GET","/reports/"+report["id"]+"/event.html?id=1")
                event=conn.getresponse()
                self.assertEqual(event.status,200)
                self.assertIn("AKUZ Log Explorer",event.read().decode("utf-8"))
            finally:
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_combined_report_does_not_recount_singles(self):
        single=self.event("12:00:00.100")
        first=self.report("first",single,path="/srv/akuz/a.log")
        raw=self.root/"first.log"
        other=self.root/"other.log"
        other.write_text(self.event("13:00:00.100","2222"),encoding="utf-8")
        selection=[]
        for src,path in ((raw,"/srv/akuz/a.log"),(other,"/srv/akuz/b.log")):
            selection.append(dict(local=src,remote=dict(name=src.name,
               path=path,mtime=1),sha=sha256(src),date="2026-09-22"))
        combined_file=self.root/"joined.jsonl"
        app._combine_sources(selection,combined_file,date(2026,9,22))
        sources=[dict(name=i["remote"]["name"],remote_path=i["remote"]["path"],
                      sha256=i["sha"],date=i["date"]) for i in selection]
        app._publish(self.root,self.store,"key-combined",combined_file,
                     date(2026,9,22),sources,"combined","combined")
        info=refresh(self.root)
        self.assertEqual(info["report_count"],2)
        self.assertEqual(info["distinct_errors"],2)

    def test_output_escapes_markup_and_event_navigation(self):
        self.report("xss",self.event("12:00:00.100",'<script>alert(1)</script>'))
        info=refresh(self.root)
        fp=info["groups"][0]["fp"]
        serialized=(self.root/"data"/("error_"+fp+".js")).read_text("utf-8")
        self.assertNotIn("<script>",serialized)
        rid=next(iter(self.store["reports"]))
        catalog=read_js(self.root/"reports"/rid/"data"/"catalog.js",
                        "window.AKUZ_DATA=")
        self.assertEqual(catalog["errorFingerprints"]["1"],fp)
        self.assertIn("errorFingerprints",
                      (self.root/"reports"/rid/"data"/"catalog.js").read_text("utf-8"))


if __name__=="__main__":
    unittest.main()
