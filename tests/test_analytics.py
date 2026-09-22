"""v4.2 analytics regression tests using synthetic AKUZ .log files only."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import sqlite3
from contextlib import closing
import unittest

import akuz_app as app
from akuz_analytics import recognize_error,refresh,connect,detail,read_js,source_inventory,update_source_date
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

    def test_legacy_schema_rebuilds_even_if_already_marked_current(self):
        report=self.report("migration",self.event("23:59:00.100")+
                           self.event("00:01:00.100","5678"))
        expected=refresh(self.root)
        catalog=self.root/"reports"/report["id"]/"data"/"catalog.js"
        original=catalog.read_bytes()
        inventory=(self.root/"cache"/"inventory.json").read_bytes()
        for version in ("3","4"):
            with self.subTest(version=version):
                # Recreate the actual pre-relative_day table, retaining index
                # stamps to ensure migration forces re-ingestion of reports.
                with closing(sqlite3.connect(self.root/"cache"/"error_analytics.sqlite")) as db:
                    db.executescript("""
                        DROP TABLE errors;
                        CREATE TABLE errors(
                            fp TEXT,exception TEXT,family TEXT,template TEXT,method TEXT,
                            source_key TEXT,line_no INTEGER,end_line INTEGER,raw_sha TEXT,
                            day TEXT,clock TEXT,report_id TEXT,event_id INTEGER,
                            ambiguous INTEGER DEFAULT 0,
                            UNIQUE(source_key,line_no,end_line,raw_sha));
                    """)
                    db.execute("UPDATE meta SET value=? WHERE key='schema'",(version,))
                    db.commit()
                actual=refresh(self.root)
                self.assertEqual(actual,expected)
                with closing(connect(self.root)) as db:
                    result=detail(db,actual["groups"][0]["fp"])
                    self.assertEqual(result["relative_days"],[("D+0",1),("D+1",1)])
                    self.assertEqual(result["days"],[("2026-09-22",1),("2026-09-23",1)])
                self.assertEqual(refresh(self.root),actual)
                self.assertEqual(catalog.read_bytes(),original)
                self.assertEqual((self.root/"cache"/"inventory.json").read_bytes(),inventory)

    def test_schema_upgrade_refreshes_empty_static_snapshot(self):
        refresh(self.root)
        snapshot=self.root/"data"/"analytics.js"
        snapshot.write_text('window.AKUZ_ANALYTICS={"schema":3};',encoding="utf-8")
        with closing(sqlite3.connect(self.root/"cache"/"error_analytics.sqlite")) as db:
            db.execute("UPDATE meta SET value='3' WHERE key='schema'")
            db.commit()
        actual=refresh(self.root)
        self.assertEqual(read_js(snapshot,"window.AKUZ_ANALYTICS="),actual)

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

    def test_date_override_in_merged_report_shifts_only_selected_source(self):
        first=self.root/"a.log"
        second=self.root/"b.log"
        first.write_text(self.event("15:00:00.100","11"),encoding="utf-8")
        second.write_text(self.event("16:00:00.100","22"),encoding="utf-8")
        selected=[]
        for file,day in ((first,"2026-09-21"),(second,"2026-09-22")):
            selected.append(dict(local=file,remote=dict(name=file.name,
                         path="/srv/akuz/"+file.name,mtime=1),
                         sha=sha256(file),date=day))
        joined=self.root/"joined.jsonl"
        app._combine_sources(selected,joined,date(2026,9,21))
        sources=[dict(name=x["remote"]["name"],date=x["date"],
                      sha256=x["sha"],remote_path=x["remote"]["path"],
                      host="host-a") for x in selected]
        report=app._publish(self.root,self.store,"merged-date-test",
                            joined,date(2026,9,21),sources,"combined","combined")
        first_summary=refresh(self.root)
        group=first_summary["groups"][0]
        with connect(self.root) as db:
            initial=detail(db,group["fp"])
            self.assertEqual(initial["days"],
                             [("2026-09-21",1),("2026-09-22",1)])
            self.assertEqual(initial["relative_days"],[("D+0",2)])
        original_catalog=(self.root/"reports"/report["id"]/"data"/"catalog.js").read_bytes()
        target=next(x for x in source_inventory(self.root) if x["name"]=="b.log")
        update_source_date(self.root,target["id"],"2026-09-23")
        with connect(self.root) as db:
            self.assertEqual(detail(db,group["fp"])["days"],
                             [("2026-09-21",1),("2026-09-23",1)])
        self.assertEqual((self.root/"reports"/report["id"]/"data"/"catalog.js").read_bytes(),
                         original_catalog)

    def test_html_has_readable_groups_and_date_controls(self):
        root=Path(__file__).resolve().parents[1]
        page=(root/"errors.html").read_text("utf-8")
        style=(root/"style.css").read_text("utf-8")
        script=(root/"errors.js").read_text("utf-8")
        self.assertIn('id="group-mode"',page)
        self.assertIn('id="date-sources"',page)
        self.assertIn('id="date-toggle"',page)
        self.assertIn('button.analytics-group{display:flex',style)
        self.assertIn("state.temporal==='relative'",script)
        self.assertIn("/api/analytics/source-date",script)

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
                source_id=source_inventory(self.root)[0]["id"]
                conn.request("GET","/api/analytics/sources")
                sources_response=conn.getresponse()
                self.assertEqual(sources_response.status,200)
                self.assertIn(source_id,sources_response.read().decode("utf-8"))
                import json
                conn.request("POST","/api/analytics/source-date",
                    body=json.dumps({"id":source_id,"date":"2026-09-21"}),
                    headers={"Origin":"http://127.0.0.1:"+str(server.server_port),
                             "Content-Type":"application/json"})
                saved=conn.getresponse()
                self.assertEqual(saved.status,200)
                self.assertEqual(json.loads(saved.read().decode("utf-8"))["updated_reports"],1)
                self.assertEqual(source_inventory(self.root)[0]["date"],"2026-09-21")
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

    def test_analytics_groups_by_type_without_losing_exact_groups(self):
        sample=self.event("12:00:00.100","1234")+(
            "12:30:00.100,AKUZ,session,user: "
            "System.Runtime.Serialization.SerializationException: Failed export 55\\n"
            " at AKUZ.Serialize()\\n")
        self.report("two_errors",sample)
        state=refresh(self.root)
        self.assertEqual(state["distinct_errors"],2)
        self.assertEqual(len(state["groups"]),2)
        self.assertEqual(len(state["types"]),1)
        self.assertEqual(state["types"][0]["total"],2)
        with connect(self.root) as db:
            kind=state["types"][0]
            group=detail(db,kind["fp"],"type",(kind["exception"],kind["family"]))
        self.assertEqual(group["total"],2)
        self.assertEqual(group["relative_hours"],[("D+0 12",2)])
        self.assertTrue((self.root/"data"/("type_"+kind["fp"]+".js")).exists())

    def test_undated_errors_have_relative_day_and_hour_graph(self):
        sample=(self.event("23:59:00.100","1")+
                self.event("00:01:00.100","2"))
        self.report("night",sample,base=None)
        state=refresh(self.root)
        self.assertEqual(state["groups"][0]["dated"],0)
        fp=state["groups"][0]["fp"]
        with connect(self.root) as db:
            result=detail(db,fp)
        self.assertEqual(result["relative_days"],[("D+0",1),("D+1",1)])
        self.assertEqual(result["relative_hours"],
                         [("D+0 23",1),("D+1 00",1)])
        self.assertEqual(result["days"],[])

    def test_assign_date_to_existing_undated_report_without_reparsing(self):
        report=self.report("old_undated",self.event("13:00:00.100"),base=None)
        before=refresh(self.root)
        self.assertEqual(before["groups"][0]["dated"],0)
        sources=source_inventory(self.root)
        self.assertEqual(len(sources),1)
        self.assertEqual(sources[0]["date"],"")
        catalog=self.root/"reports"/report["id"]/"data"/"catalog.js"
        original=catalog.read_bytes()
        result=update_source_date(self.root,sources[0]["id"],"2026-09-22")
        self.assertEqual(result["updated_reports"],1)
        self.assertEqual(result["overview"]["groups"][0]["dated"],1)
        self.assertEqual(catalog.read_bytes(),original)
        self.assertEqual(refresh(self.root)["groups"][0]["dated"],1)
        updated=source_inventory(self.root)
        self.assertEqual(updated[0]["date"],"2026-09-22")
        with self.assertRaises(ValueError):
            update_source_date(self.root,updated[0]["id"],"22.09.2026")

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
