"""Synthetic-only B-lite sidecar experiment; not an application cache.

Stores derived fields and coordinates, never raw/message/headline.
Normalized patterns may still be sensitive: private test workspace only.
"""
from __future__ import annotations
from hashlib import sha256 as digest
import json
from pathlib import Path
from akuz_derived import DerivedEvent
from akuz_store import sha256

SCHEMA = 1
ROOT = Path(__file__).resolve().parents[1]
DERIVER_FILES = ('akuz_log_parser.py', 'akuz_analytics.py',
                 'akuz_derived.py', 'akuz_html_explorer.py')

def code_revision() -> str:
    sha = digest(b'akuz-phase9-sidecar-v1')
    for name in DERIVER_FILES:
        body = (ROOT/name).read_bytes()
        sha.update(name.encode('ascii'))
        sha.update(len(body).to_bytes(8, 'big'))
        sha.update(body)
    return sha.hexdigest()

def record(ev: dict, item: DerivedEvent) -> str:
    return json.dumps([ev['event_id'],ev['start_line'],ev['end_line'],
        item.category,item.normalized_pattern,item.duration,
        item.error_fp,item.replacement_chars],
        ensure_ascii=False,separators=(',',':'))+'\n'

class SidecarWriter:
    def __init__(self, folder: Path, source: Path, host: str,
                 logical_path: str, first_date: str):
        self.folder=folder
        self.source=source
        self.identity=dict(sha256=sha256(source),
                           bytes=source.stat().st_size,host=host,
                           path=logical_path,date=first_date)
        self.count=0
        self.bytes_written=0
        self._sha=digest()
        self._file=None
        self._tmp=folder/'derived.jsonl.tmp'
        self.body=folder/'derived.jsonl'
        self.manifest=folder/'manifest.json'

    def __enter__(self):
        self.folder.mkdir(parents=True,exist_ok=False)
        self._file=self._tmp.open('x',encoding='utf-8',newline='\n')
        return self

    def __call__(self, ev: dict, item: DerivedEvent):
        if self._file is None or ev['event_id'] != self.count+1:
            raise ValueError('Nonsequential or closed sidecar writer')
        line=record(ev,item)
        self._file.write(line)
        payload=line.encode('utf-8')
        self._sha.update(payload)
        self.bytes_written+=len(payload)
        self.count+=1

    def __exit__(self, exc_type, exc, tb):
        if self._file is not None:
            self._file.close()
            self._file=None
        if exc_type is not None:
            self._tmp.unlink(missing_ok=True)
            return False
        if not self.count:
            self._tmp.unlink(missing_ok=True)
            raise ValueError('Empty derived sidecar')
        if sha256(self.source)!=self.identity['sha256']:
            self._tmp.unlink(missing_ok=True)
            raise ValueError('Source changed while creating sidecar')
        self._tmp.replace(self.body)
        header=dict(schema=SCHEMA,revision=code_revision(),
                    source=self.identity,rows=self.count,
                    body_sha256=self._sha.hexdigest(),
                    body_bytes=self.bytes_written)
        draft=self.folder/'manifest.json.tmp'
        draft.write_text(json.dumps(header,ensure_ascii=False,
                          separators=(',',':'))+'\n',encoding='utf-8')
        draft.replace(self.manifest)
        return False

class VerifiedSidecar:
    def __init__(self, folder: Path, source: Path, host: str,
                 logical_path: str, first_date: str):
        body=folder/'derived.jsonl'
        manifest=folder/'manifest.json'
        if folder.is_symlink() or body.is_symlink() or manifest.is_symlink():
            raise ValueError('Redirected sidecar path')
        try:
            header=json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:
            raise ValueError('Sidecar manifest missing or malformed') from exc
        identity=dict(sha256=sha256(source),bytes=source.stat().st_size,
                      host=host,path=logical_path,date=first_date)
        if (header.get('schema')!=SCHEMA or
                header.get('revision')!=code_revision() or
                header.get('source')!=identity):
            raise ValueError('Sidecar schema, algorithm or source mismatch')
        if (not isinstance(header.get('rows'),int) or
                header['rows']<1 or body.stat().st_size!=header.get('body_bytes')
                or sha256(body)!=header.get('body_sha256')):
            raise ValueError('Sidecar length or checksum mismatch')
        self.body=body
        self.expected=header['rows']
        self.seen=0
        self._file=None

    def __enter__(self):
        self._file=self.body.open('r',encoding='utf-8',newline='\n')
        return self

    def take(self, ev: dict) -> DerivedEvent:
        if self._file is None or self.seen>=self.expected:
            raise ValueError('Missing or exhausted sidecar reader')
        line=self._file.readline()
        if not line:
            raise ValueError('Derived sidecar ended before source event')
        try:
            row=json.loads(line)
        except ValueError as exc:
            raise ValueError('Malformed derived sidecar record') from exc
        if not isinstance(row,list) or len(row)!=8:
            raise ValueError('Unexpected derived sidecar record layout')
        eid,start,end,category,pattern,duration,fp,count=row
        expected=(ev.get('source_event_id',ev['event_id']),
                  ev.get('original_start_line',ev['start_line']),
                  ev.get('original_end_line',ev['end_line']))
        if (eid,start,end)!=expected:
            raise ValueError('Derived sidecar source coordinates mismatch')
        self.seen+=1
        message=ev['message']
        headline=message.split('\n',1)[0].strip()[:1400]
        if duration is not None:
            duration=tuple(duration)
        return DerivedEvent(category,pattern,duration,fp,count,
                            headline,0.,0.,0.,0.,0.)

    def finish(self):
        if self._file is None or self.seen!=self.expected:
            raise ValueError('Derived sidecar count mismatch')
        if self._file.readline():
            raise ValueError('Derived sidecar trailing records')

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.finish()
        finally:
            if self._file is not None:
                self._file.close()
                self._file=None
        return False


# Only a bounded prototype frame, NOT a full-file streaming sidecar.
# A real 657k-event sidecar requires multiple frames and a block index.
MAX_BINARY_FRAME_BYTES = 8 * 1024 * 1024


def encode_binary_frame(body: bytes) -> bytes:
    """Explicit bounded experimental frame, NOT a stable cache format."""
    import zlib
    if len(body) > MAX_BINARY_FRAME_BYTES:
        raise ValueError('Binary sidecar frame too large')
    return (b'AKZS'+bytes([1])+len(body).to_bytes(8,'big')+
            digest(body).digest()+zlib.compress(body,level=6))


def decode_binary_frame(frame: bytes) -> bytes:
    import zlib
    if len(frame)<45 or frame[:4]!=b'AKZS' or frame[4]!=1:
        raise ValueError('Unsupported sidecar frame version')
    size=int.from_bytes(frame[5:13],'big')
    if size > MAX_BINARY_FRAME_BYTES:
        raise ValueError('Binary sidecar frame too large')
    try:
        decoder = zlib.decompressobj()
        # Never decompress an untrusted frame beyond its declared length.
        body = decoder.decompress(frame[45:], size + 1)
    except zlib.error as exc:
        raise ValueError('Corrupt binary sidecar body') from exc
    if (len(body) != size or not decoder.eof or
            decoder.unconsumed_tail or decoder.unused_data):
        raise ValueError('Binary sidecar length or checksum mismatch')
    if digest(body).digest()!=frame[13:45]:
        raise ValueError('Binary sidecar length or checksum mismatch')
    return body
