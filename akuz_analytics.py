"""AKUZ-only error analytics from previously generated HTML reports."""
from __future__ import annotations
from collections import Counter
from datetime import date,timedelta
import hashlib,json,re,sqlite3,threading
from pathlib import Path
from akuz_store import load_store, source_date
LOCK=threading.RLock()
VERSION=5
REPORT_ID=re.compile(r"^v4_[A-Za-z0-9_-]{1,74}$")
EXCEPTION=re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)*([A-Z][A-Za-z0-9_]*(?:Exception|Error))\b\s*:?",re.I)
SERIAL=re.compile(r"ошибк[а-я]*\s+сериализац[а-я]*|serialization\s+(?:failed|error)|сбой\s+сериализац[а-я]*",re.I)
ERROR=re.compile(r"\b(?:exception|failed|failure|error|timeout|timed out)\b|ошибк[а-я]*|тайм.?аут",re.I)
FRAME=re.compile(r"(?m)^\s*(?:at|в)\s+([\w.+<>]+)\s*\(")
GUID=re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",re.I)
EMAIL=re.compile(r"\b[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}\b")
IP=re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
NUMBER=re.compile(r"\b\d+\b")
HEX=re.compile(r"\b0x[0-9a-f]+\b",re.I)
WS=re.compile(r"\s+")
def js_json(value):
    return (json.dumps(value,ensure_ascii=False,separators=(",",":"))
            .replace("&","\\u0026").replace("<","\\u003c")
            .replace(">","\\u003e").replace("\u2028","\\u2028").replace("\u2029","\\u2029"))
def normalize(message):
    text=message[:450]
    for rx,replacement in ((GUID,"{GUID}"),(EMAIL,"{EMAIL}"),(IP,"{IP}"),
                           (HEX,"{HEX}"),(NUMBER,"{N}")):
        text=rx.sub(replacement,text)
    return WS.sub(" ",text).strip().casefold()[:200] or "(без описания)"
def recognize_error(raw):
    """A fingerprint groups matching exception type, normalized message and top frame."""
    text=raw[:24000]
    match=EXCEPTION.search(text)
    serial=SERIAL.search(text)
    if not match and not serial and not ERROR.search(raw.split("\n",1)[0]):
        return None
    if match:
        exception=match.group(1)
        message=text[match.end():].split("\n",1)[0].strip(": -\t\r ")
    elif serial:
        exception="Serialization error (text)"
        message=text[serial.start():].split("\n",1)[0]
    else:
        exception="AKUZ error (text)"
        message=text.split("\n",1)[0].split(": ",1)[-1]
    frame=FRAME.search(text)
    method=frame.group(1) if frame else ""
    family="Сериализация" if ("serializ" in exception.casefold() or
        "сериализац" in exception.casefold() or "serializ" in message.casefold() or
        "сериализац" in message.casefold() or serial or
        re.search(r"\b\w*Serializ\w*(?:Exception|Error)\b",text,re.I)
        ) else "Другие ошибки"
    template=normalize(message)
    signature="\0".join((exception.casefold(),template,method.casefold()))
    fp=hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
    return dict(fp=fp,exception=exception,family=family,template=template,method=method)
def read_js(path,prefix):
    text=path.read_text(encoding="utf-8").strip()
    if not text.startswith(prefix) or not text.endswith(";"):
        raise ValueError("Неизвестный формат отчёта: "+path.name)
    return json.loads(text[len(prefix):-1])
def connect(root):
    directory=root/"cache"
    directory.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(str(directory/"error_analytics.sqlite"),timeout=30)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA journal_mode=DELETE")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS indexed(id TEXT PRIMARY KEY,stamp TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_files(sha TEXT PRIMARY KEY,source_key TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_dates(
        sha TEXT PRIMARY KEY, first_date TEXT NOT NULL, conflict INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS errors(
        fp TEXT,exception TEXT,family TEXT,template TEXT,method TEXT,
        source_key TEXT,line_no INTEGER,end_line INTEGER,raw_sha TEXT,
        day TEXT,clock TEXT,report_id TEXT,event_id INTEGER,ambiguous INTEGER DEFAULT 0,
        relative_day INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source_key,line_no,end_line,raw_sha));
    CREATE INDEX IF NOT EXISTS ix_fp ON errors(fp);
    CREATE INDEX IF NOT EXISTS ix_line ON errors(source_key,line_no);
    """)
    version=db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    columns={row["name"] for row in db.execute("PRAGMA table_info(errors)")}
    missing_relative_day="relative_day" not in columns
    if missing_relative_day or version is None or version["value"]!=str(VERSION):
        with db:
            # CREATE TABLE IF NOT EXISTS does not upgrade an existing table.
            # Inspect columns too: the old migration could already record v4.
            db.execute("BEGIN IMMEDIATE")
            if missing_relative_day:
                db.execute("ALTER TABLE errors ADD COLUMN relative_day INTEGER NOT NULL DEFAULT 0")
            for table in ("indexed","source_files","source_dates","errors"):
                db.execute("DELETE FROM "+table)
            db.execute("INSERT OR REPLACE INTO meta VALUES('schema',?)",(str(VERSION),))
    return db
def reports(root):
    found=[]
    for rid,meta in load_store(root)["reports"].items():
        if not REPORT_ID.fullmatch(rid):
            continue
        catalog=root/"reports"/rid/"data"/"catalog.js"
        if catalog.is_file():
            # Apply filename dates to old reports without rewriting source data.
            meta=dict(meta,sources=[dict(info,date=source_date(info))
                                   for info in meta.get("sources") or []])
            st=catalog.stat()
            # The source-to-report provenance is held in inventory.json, not
            # catalog.js. A corrected source date/path must invalidate the
            # derived index even if report bytes never changed.
            provenance=json.dumps(meta.get("sources") or [],
                                  ensure_ascii=False,sort_keys=True)
            proof=hashlib.sha256(provenance.encode("utf-8")).hexdigest()
            found.append((rid,meta,catalog,f"{st.st_size}:{st.st_mtime_ns}:{proof}"))
    return sorted(found,key=lambda x:(x[1].get("kind")=="combined",x[0]))
def source_identity(info):
    """Content alone does not prove events from two servers are identical."""
    sha=str(info.get("sha256") or "")
    path=str(info.get("remote_path") or "")
    host=str(info.get("host") or "")
    if not (sha and path):
        return ""
    return hashlib.sha256(("\0".join((host,path,sha))).encode("utf-8")).hexdigest()


def source_key(info,rid,sid,aliases):
    path=str(info.get("remote_path") or "")
    host=str(info.get("host") or "")
    day=str(info.get("date") or "")
    identity=source_identity(info)
    if identity and identity in aliases:
        return aliases[identity]
    key=hashlib.sha256(("\0".join((host,path,day)) if path else rid+":"+str(sid)).encode()).hexdigest()
    if identity:
        aliases[identity]=key
    return key

def ingest(db,rid,info,catalog_path,aliases):
    from akuz_diagnostics import event as perf_event
    from time import perf_counter
    started = perf_counter()
    perf_root = catalog_path.parent.parent.parent.parent
    catalog=read_js(catalog_path,"window.AKUZ_DATA=")
    base=catalog["meta"].get("base_date")
    base_day=date.fromisoformat(base) if base else None
    provenance=info.get("sources") or []
    mapping={}
    for sid,label in enumerate(catalog.get("sources") or []):
        found=next((p for p in provenance if
                     label==p.get("name") or
                     label==p.get("name")+" · "+str(p.get("date") or "")),None)
        if found is None and len(provenance)==1:
            found=provenance[0]
        if found is None and len(provenance)==len(catalog.get("sources") or []):
            # A source-date override changes inventory metadata but not the
            # immutable source label embedded in an existing merged report.
            found=provenance[sid]
        found=found or {}
        identity=source_identity(found)
        skip=info.get("kind")=="combined" and bool(identity and identity in aliases)
        key=source_key(found,rid,sid,aliases)
        chosen=str(found.get("date") or "")
        if identity:
            existing=db.execute(
                "SELECT first_date,conflict FROM source_dates WHERE sha=?",
                (identity,)).fetchone()
            if existing is None:
                db.execute("INSERT INTO source_dates VALUES(?,?,0)",(identity,chosen))
            elif existing["first_date"]!=chosen:
                db.execute("UPDATE source_dates SET conflict=1 WHERE sha=?",(identity,))
                db.execute("UPDATE errors SET day=NULL WHERE source_key=?",(key,))
        # A report's base_date alone is not evidence that a source's first
        # day is known: especially important for old merged JSONL reports.
        try:
            has_source_date=bool(chosen and date.fromisoformat(chosen).isoformat()==chosen)
        except ValueError:
            has_source_date=False
        mapping[sid]=(key,skip,bool(identity and db.execute(
            "SELECT conflict FROM source_dates WHERE sha=?",
            (identity,)).fetchone()["conflict"]),has_source_date,chosen)
    part=None
    raw_shard=[]
    for index, row in enumerate(catalog["rows"], 1):
        if index % 50000 == 0:
            perf_event(perf_root, 'analytics.ingest', 'progress', events=index,
                       elapsed_s=round(perf_counter()-started, 3))
        sid=int(row[12]) if len(row)>12 else 0
        key,skip,date_conflict,has_source_date,chosen_source_date=mapping.get(
            sid,(rid+":"+str(sid),False,False,False,""))
        if skip:
            continue
        number=int(row[10])
        if part!=number:
            raw_shard=read_js(catalog_path.parent/("raw_%05d.js"%number),"window.AKUZ_RAW=")
            part=number
        raw=raw_shard[int(row[11])]
        match=recognize_error(raw)
        if match is None:
            continue
        line,end=int(row[8]),int(row[9])
        raw_sha=hashlib.sha256(raw.encode("utf-8")).hexdigest()
        prior=db.execute("SELECT raw_sha FROM errors WHERE source_key=? AND line_no=?",
                         (key,line)).fetchall()
        ambiguous=any(r["raw_sha"]!=raw_sha for r in prior)
        if ambiguous:
            db.execute("UPDATE errors SET ambiguous=1 WHERE source_key=? AND line_no=?",
                       (key,line))
        merged_offset=int(row[1])
        relative_day=merged_offset
        source_day=date.fromisoformat(chosen_source_date) if has_source_date else None
        if info.get("kind") == "combined" and base_day:
            # Combined rows are offset from the earliest file's calendar day.
            # D+N must instead be relative to EACH original source.
            label=(catalog.get("sources") or [""])[sid]
            old_label_day=label.rsplit(" · ",1)[-1]
            try:
                initial_day=date.fromisoformat(old_label_day)
            except ValueError:
                initial_day=source_day or base_day
            relative_day=merged_offset-(initial_day-base_day).days
        calendar_day=(source_day+timedelta(days=relative_day)
                      if source_day else None)
        when=(calendar_day.isoformat() if calendar_day and not date_conflict else None)
        db.execute("""INSERT OR IGNORE INTO errors
          (fp,exception,family,template,method,source_key,line_no,end_line,raw_sha,
           day,clock,report_id,event_id,ambiguous,relative_day)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (match["fp"],match["exception"],match["family"],match["template"],
           match["method"],key,line,end,raw_sha,when,str(row[2]),
           rid,int(row[0]),int(ambiguous),relative_day))
def signature_label(number):
    """Human-readable number of exact templates in a broad group."""
    ending=("шаблон" if number%10==1 and number%100!=11 else
            "шаблона" if 2<=number%10<=4 and not 12<=number%100<=14 else
            "шаблонов")
    return str(number)+" "+ending


def group_id(mode,value):
    return hashlib.sha256((mode+"\0"+value.casefold()).encode("utf-8")).hexdigest()[:24]


def overview(db):
    groups=[dict(r) for r in db.execute("""
      SELECT fp,exception,family,template,method,count(*) AS total,
       sum(CASE WHEN day IS NOT NULL AND ambiguous=0 THEN 1 ELSE 0 END) AS dated,
       sum(ambiguous) AS ambiguous,min(day) AS first,max(day) AS last
      FROM errors GROUP BY fp ORDER BY total DESC,exception
    """)]
    types=[]
    for r in db.execute("""
      SELECT exception,family,count(*) AS total,count(DISTINCT fp) AS signatures,
       sum(CASE WHEN day IS NOT NULL AND ambiguous=0 THEN 1 ELSE 0 END) AS dated,
       sum(ambiguous) AS ambiguous
      FROM errors GROUP BY exception,family ORDER BY total DESC,exception
    """):
        item=dict(r)
        item["fp"]=group_id("type",item["exception"]+"\0"+item["family"])
        item["template"]=signature_label(item["signatures"])
        item["method"]=""
        types.append(item)
    families=[]
    for r in db.execute("""
      SELECT family,count(*) AS total,count(DISTINCT fp) AS signatures,
       sum(CASE WHEN day IS NOT NULL AND ambiguous=0 THEN 1 ELSE 0 END) AS dated,
       sum(ambiguous) AS ambiguous
      FROM errors GROUP BY family ORDER BY total DESC
    """):
        item=dict(r)
        item["fp"]=group_id("family",item["family"])
        item["exception"]=item["family"]
        item["template"]=signature_label(item["signatures"])
        item["method"]=""
        families.append(item)
    return dict(groups=groups,types=types,families=families,
                report_count=db.execute("SELECT count(*) FROM indexed").fetchone()[0],
                distinct_errors=db.execute("SELECT count(*) FROM errors").fetchone()[0],
                ambiguous=db.execute("SELECT count(*) FROM errors WHERE ambiguous=1").fetchone()[0],
                date_conflicts=db.execute("SELECT count(*) FROM source_dates WHERE conflict=1").fetchone()[0],
                schema=VERSION)


def detail(db,fp,mode="exact",value=None):
    if mode == "exact":
        assert re.fullmatch("[0-9a-f]{24}",fp)
        where,params="fp=?",(fp,)
    elif mode == "type":
        exception,family=value
        where,params="exception=? AND family=?",(exception,family)
    elif mode == "family":
        where,params="family=?",(value,)
    else:
        raise ValueError("Unknown group mode")
    rows=[dict(r) for r in db.execute("""
      SELECT fp,exception,family,template,method,day,clock,report_id,
             event_id,ambiguous,relative_day
      FROM errors WHERE """+where+""" ORDER BY day DESC,clock DESC,report_id,event_id
    """,params)]
    days,hours,rel_days,rel_hours=Counter(),Counter(),Counter(),Counter()
    for r in rows:
        if r["ambiguous"]:
            continue
        rel="D+"+str(r["relative_day"])
        rel_days[rel]+=1
        if r["clock"]:
            rel_hours[rel+" "+r["clock"][:2]]+=1
        if r["day"]:
            days[r["day"]]+=1
            if r["clock"]:
                hours[r["day"]+" "+r["clock"][:2]]+=1
    return dict(fp=fp,mode=mode,
                exception=rows[0]["exception"] if mode!="family" and rows else (value if mode=="family" else ""),
                family=rows[0]["family"] if rows else "",
                template=(rows[0]["template"] if len({r["fp"] for r in rows})==1 and rows else
                          signature_label(len({r["fp"] for r in rows}))+" в группе"),
                method=rows[0]["method"] if mode=="exact" and rows else "",
                total=len(rows),dated=sum(days.values()),
                ambiguous=sum(r["ambiguous"] for r in rows),
                days=sorted(days.items()),hours=sorted(hours.items()),
                relative_days=sorted(rel_days.items(),key=lambda r:int(r[0][2:])),
                relative_hours=sorted(rel_hours.items(),key=lambda r:(int(r[0].split()[0][2:]),r[0].split()[1])),
                items=rows)


def export(db,root):
    folder=root/"data"
    folder.mkdir(exist_ok=True)
    summary=overview(db)
    live={g["fp"] for g in summary["groups"]}
    for path in folder.glob("error_*.js"):
        if re.fullmatch(r"error_[0-9a-f]{24}\.js",path.name) and path.stem[6:] not in live:
            path.unlink()
    for mode,collection in (("type",summary["types"]),("family",summary["families"])):
        for item in collection:
            value=(item["exception"],item["family"]) if mode=="type" else item["family"]
            fp=item["fp"]
            target=folder/(mode+"_"+fp+".js")
            temp=target.with_suffix(".tmp")
            temp.write_text("window.AKUZ_ERROR_DETAIL="+js_json(detail(db,fp,mode,value))+";\n",
                            encoding="utf-8")
            temp.replace(target)
    for mode in ("type","family"):
        existing={g["fp"] for g in summary[mode+"s" if mode=="type" else "families"]}
        for stale in folder.glob(mode+"_*.js"):
            if re.fullmatch(mode+r"_[0-9a-f]{24}\.js",stale.name) and stale.stem[len(mode)+1:] not in existing:
                stale.unlink()
    for fp in live:
        target=folder/("error_"+fp+".js")
        temp=target.with_suffix(".tmp")
        temp.write_text("window.AKUZ_ERROR_DETAIL="+js_json(detail(db,fp))+";\n",
                        encoding="utf-8")
        temp.replace(target)
    target=folder/"analytics.js"
    temp=folder/"analytics.js.tmp"
    temp.write_text("window.AKUZ_ANALYTICS="+js_json(summary)+";\n",encoding="utf-8")
    temp.replace(target)
def source_inventory(root):
    """Only source metadata from registered AKUZ reports; no remote access."""
    result={}
    for rid,meta in load_store(Path(root))["reports"].items():
        if not REPORT_ID.fullmatch(rid):
            continue
        if not (Path(root)/"reports"/rid/"data"/"catalog.js").is_file():
            continue
        for idx,info in enumerate(meta.get("sources") or []):
            identity=source_identity(info)
            key=identity or hashlib.sha256((rid+":"+str(idx)).encode()).hexdigest()
            item=result.setdefault(key,dict(id=key,name=str(info.get("name") or "?"),
              host=str(info.get("host") or "не указан"),
              remote_path=str(info.get("remote_path") or ""),
              date=source_date(info),
              reports=[],conflict=False,bytes_sha=str(info.get("sha256") or "")))
            item["reports"].append(dict(id=rid,index=idx,label=str(meta.get("label") or rid)))
            if item["date"]!=source_date(info):
                item["conflict"]=True
    return sorted(result.values(),key=lambda x:(x["date"]!="",x["host"],x["name"]))


def update_source_date(root,identity,first_date):
    """Update only matched registered report metadata, never original .log.

    Keep catalog.js/raw shards immutable. An operator override corrects only
    Error Analytics calendar projections, not the old report's printed date.
    """
    if not isinstance(identity,str) or not re.fullmatch("[0-9a-f]{64}",identity):
        raise ValueError("Некорректный идентификатор источника")
    if not isinstance(first_date,str):
        raise ValueError("Неверный тип даты")
    if first_date:
        try:
            if date.fromisoformat(first_date).isoformat()!=first_date:
                raise ValueError
        except ValueError as exc:
            raise ValueError("Дата первой записи должна быть YYYY-MM-DD") from exc
    with LOCK:
        store=load_store(Path(root))
        changed=0
        for rid,meta in store["reports"].items():
            if not REPORT_ID.fullmatch(rid):
                continue
            if not (Path(root)/"reports"/rid/"data"/"catalog.js").is_file():
                continue
            for idx,info in enumerate(meta.get("sources") or []):
                original=source_identity(info) or hashlib.sha256(
                    (rid+":"+str(idx)).encode()).hexdigest()
                if original==identity:
                    info["date"]=first_date
                    info["date_override"]=True
                    changed+=1
        if not changed:
            raise ValueError("Источник больше не найден, обновите страницу")
        from akuz_store import save_store
        save_store(Path(root),store)
        overview=refresh(root)
        return dict(updated_reports=changed,overview=overview)


def refresh(root):
    """Idempotent processing of *reports*, never a network operation."""
    from akuz_diagnostics import event as perf_event, phase as perf_phase
    root=Path(root)
    with LOCK:
        db=connect(root)
        try:
            migrated=db.total_changes>0
            with perf_phase(root, 'analytics.inventory'):
                available=reports(root)
            perf_event(root, 'analytics.inventory', 'summary', reports=len(available))
            desired={rid:stamp for rid,_,_,stamp in available}
            prior={r["id"]:r["stamp"] for r in db.execute("SELECT id,stamp FROM indexed")}
            removed = any(desired.get(rid)!=stamp for rid,stamp in prior.items())
            if removed:
                with db:
                    for table in ("indexed","source_files","source_dates","errors"):
                        db.execute("DELETE FROM "+table)
                prior={}
            aliases={r["sha"]:r["source_key"] for r in
                     db.execute("SELECT sha,source_key FROM source_files")}
            changed=removed or migrated
            for report_index, (rid,info,catalog,stamp) in enumerate(available, 1):
                if rid in prior:
                    continue
                with perf_phase(root, 'analytics.ingest', report_index=report_index):
                    with db:
                        ingest(db,rid,info,catalog,aliases)
                        db.execute("INSERT INTO indexed VALUES(?,?)",(rid,stamp))
                        for sha,key in aliases.items():
                            db.execute("INSERT OR IGNORE INTO source_files VALUES(?,?)",(sha,key))
                changed=True
            if changed or not (root/"data"/"analytics.js").exists():
                with perf_phase(root, 'analytics.export'):
                    export(db,root)
            with perf_phase(root, 'analytics.overview'):
                return overview(db)
        finally:
            db.close()
