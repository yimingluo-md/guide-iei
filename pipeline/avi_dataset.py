#!/usr/bin/env python3
"""Prepare/publish lossless AVI VCF bundles; never modify the source ZIP.

Chromosomes are independently restartable. The ZIP stores its BGZF members
without outer compression, allowing workers to seek using the source TBI.
Only a completely indexed, hashed bundle becomes the installed pointer.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import time
import uuid
import zipfile
import zlib

SOURCE_URL = 'https://deepmind.google.com/science/alphagenome/_/download/atlas/avi_scores_snvs_tabix.zip'
TERMS_URL = 'https://deepmind.google.com/science/alphagenome/terms'
MEMBER = 'alphagenome_variant_impact_score_snvs.tsv.gz'
ARCHIVE_NAME = 'avi_scores_snvs_tabix.zip'
SOURCE_SIZE = 88_470_182_310
SOURCE_CRC = 0x6c6c1d44
EXPECTED_ROWS = 8_812_917_339
RELEASE = 'atlas-2026-09-08-vcf-v1'
SCHEMA = 'guide-iei.avi/v1'
EOF = bytes.fromhex('1f8b08040000000000ff0600424302001b0003000000000000000000')
CONTIGS = ['chr'+str(n) for n in range(1,23)]+['chrX','chrY']
LENGTHS = [248956422,242193529,198295559,190214555,181538259,170805979,
           159345973,145138636,138394717,133797422,135086622,133275309,
           114364328,107043718,101991189,90338345,83257441,80373285,
           58617616,64444167,46709983,50818468,156040895,57227415]


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def inspect_archive(path: Path, *, pinned: bool = True) -> dict:
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != {MEMBER, MEMBER+'.tbi'} or len(archive.infolist()) != 2:
            raise ValueError('Unexpected AVI ZIP members; obtain the official AVI SNV archive')
        info = archive.getinfo(MEMBER)
        if info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1:
            raise ValueError('AVI source must be an unencrypted ZIP_STORED BGZF member')
        if pinned and (info.file_size != SOURCE_SIZE or info.CRC != SOURCE_CRC):
            raise ValueError('AVI archive is not the verified September 8 release; update GUIDE-IEI before using a new release')
        index_info = archive.getinfo(MEMBER+'.tbi')
        if index_info.file_size > 16*1024*1024:
            raise ValueError('Oversized AVI index')
        # ZIP reader validates the index CRC; bound its expanded index too.
        with gzip.GzipFile(fileobj=__import__('io').BytesIO(archive.read(index_info))) as g:
            index = g.read(32*1024*1024+1)
        if len(index)>32*1024*1024 or index[:4] != b'TBI\x01':
            raise ValueError('Invalid AVI Tabix index')
        nref, fmt, seq, start, end, meta, skip, names_len = struct.unpack_from('<8i',index,4)
        if not 0 < nref <= 24 or (fmt,seq,start,end,meta,skip)!=(2,1,2,0,35,0) or not 0<names_len<1024:
            raise ValueError('Unexpected AVI index coordinate convention')
        names=index[36:36+names_len].decode().rstrip('\0').split('\0')
        if len(names)!=nref or (pinned and names!=CONTIGS):
            raise ValueError('Unexpected AVI chromosome coverage')
        cursor=36+names_len; refs=[]
        for name in names:
            nbin,=struct.unpack_from('<i',index,cursor); cursor+=4
            if not 0<nbin<1000000: raise ValueError('Invalid index bin count')
            bounds=[]; count=None
            for _ in range(nbin):
                bin_id,nchunk=struct.unpack_from('<Ii',index,cursor); cursor+=8
                if not 0<=nchunk<1000000 or cursor+nchunk*16>len(index): raise ValueError('Invalid index chunks')
                chunks=[struct.unpack_from('<QQ',index,cursor+i*16) for i in range(nchunk)]
                cursor+=nchunk*16
                if bin_id==37450:
                    if len(chunks)!=2: raise ValueError('Missing index statistics')
                    count=chunks[1][0]
                else: bounds.extend(chunks)
            nlinear,=struct.unpack_from('<i',index,cursor); cursor+=4
            if not 0<=nlinear<1000000 or cursor+nlinear*8>len(index): raise ValueError('Invalid linear index')
            cursor+=nlinear*8
            if name not in CONTIGS or not count or count%3 or not bounds: raise ValueError('Invalid chromosome row count')
            first=min(a for a,b in bounds)
            if (first>>16)>=info.file_size: raise ValueError('Index offset exceeds table')
            refs.append({'chrom':name,'rows':count,'virtual_start':first})
        if pinned and sum(r['rows'] for r in refs)!=EXPECTED_ROWS:
            raise ValueError('Unexpected AVI total row count')
        with archive.open(info) as member, gzip.GzipFile(fileobj=member) as g:
            if g.readline(1024)!=b'#CHROM\tPOS\tREF\tALT\traw_score\tPHRED\n':
                raise ValueError('Unrecognized AVI score schema')
    with path.open('rb') as raw:
        raw.seek(info.header_offset); header=raw.read(30)
        if header[:4]!=b'PK\x03\x04': raise ValueError('Invalid ZIP local header')
        name_len,extra_len=struct.unpack_from('<HH',header,26)
        data_start=info.header_offset+30+name_len+extra_len
        raw.seek(data_start+info.file_size-len(EOF))
        if raw.read(len(EOF))!=EOF: raise ValueError('AVI BGZF EOF marker is missing')
    return {'member_offset':data_start,'member_size':info.file_size,'member_crc32':f'{info.CRC:08x}','chromosomes':refs}


def bgzf(data: bytes) -> bytes:
    result=bytearray()
    for offset in range(0,len(data),65280):
        block=data[offset:offset+65280]
        encoder=zlib.compressobj(6,zlib.DEFLATED,-15)
        encoded=encoder.compress(block)+encoder.flush()
        header=bytes.fromhex('1f8b08040000000000ff060042430200')+struct.pack('<H',len(encoded)+25)
        result.extend(header+encoded+struct.pack('<II',zlib.crc32(block),len(block)))
    return bytes(result)


def vcf_header() -> bytes:
    lines=['##fileformat=VCFv4.2','##reference=GRCh38',f'##source=AlphaGenomeAVI,{RELEASE}',
           f'##source_url={SOURCE_URL}',f'##source_terms={TERMS_URL}',
           '##INFO=<ID=raw,Number=A,Type=Float,Description="Upstream AVI raw logit; unchanged decimal precision">',
           '##INFO=<ID=phred,Number=A,Type=Float,Description="Upstream AVI Phred-scaled genome-wide impact rank; not a clinical classification">']
    lines.extend(f'##contig=<ID={name[3:]},length={length}>' for name,length in zip(CONTIGS,LENGTHS))
    return ('\n'.join(lines)+'\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n').encode()


def valid_bundle(manifest: Path, *, strict: bool = False) -> bool:
    try:
        value=json.loads(manifest.read_text(encoding='utf-8'))
        if value.get('schema')!=SCHEMA or value.get('release')!=RELEASE or value.get('assembly')!='GRCh38': return False
        if value.get('rows')!=EXPECTED_ROWS or value.get('positions')!=EXPECTED_ROWS//3: return False
        for name in ('avi.grch38.vcf.gz','avi.grch38.vcf.gz.tbi'):
            record=value['files'][name]; path=manifest.parent/name; stat=path.stat()
            if stat.st_size!=record['size'] or not path.is_file(): return False
            if strict or stat.st_mtime_ns!=record.get('mtime_ns'):
                if digest(path)!=record['sha256']: return False
        return True
    except (OSError,ValueError,KeyError,TypeError,AttributeError): return False


def current_bundle(root: Path) -> Path | None:
    try:
        pointer=json.loads((root/'current.json').read_text(encoding='utf-8'))
        if pointer.get('release')!=RELEASE: return None
        manifest=root/'releases'/RELEASE/'manifest.json'
        return manifest if valid_bundle(manifest) else None
    except (OSError,ValueError,TypeError,AttributeError): return None


def _tabix(path: Path) -> None:
    if shutil.which('tabix'): command=['tabix','-f','-p','vcf',str(path)]
    else: command=['bash',str(Path(__file__).resolve().parents[1]/'scripts/avi_hts.sh'),'tabix','-f','-p','vcf',str(path)]
    subprocess.run(command,check=True)


def prepare(archive: Path, root: Path, converter: Path, workers: int) -> Path:
    root.mkdir(parents=True,exist_ok=True)
    with (root/'.prepare.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another AVI preparation is already running for this destination')
        return _prepare_locked(archive,root,converter,workers)


def _prepare_locked(archive: Path, root: Path, converter: Path, workers: int) -> Path:
    before=archive.stat(); identity=inspect_archive(archive)
    final=root/'releases'/RELEASE
    if valid_bundle(final/'manifest.json',strict=True):
        atomic_json(root/'current.json',{'release':RELEASE})
        print('100% AlphaGenome AVI already prepared and verified',flush=True); return final
    if final.exists():
        preserved = final.with_name(final.name+'.damaged-'+uuid.uuid4().hex)
        os.replace(final,preserved)
        print(f'Preserved damaged AVI installation at {preserved}; preparing a replacement',flush=True)
    work=root/('.preparing-'+RELEASE); work.mkdir(exist_ok=True)
    existing=sum(p.stat().st_size for p in work.glob('chr*.vcf.gz') if p.is_file())
    combined=work/'bundle'/'avi.grch38.vcf.gz'
    if combined.is_file(): existing+=combined.stat().st_size
    # Both incomplete chromosome outputs and an interrupted combination are
    # overwritten in place, not duplicated. Retain headroom for the index.
    if shutil.disk_usage(root).free < max(10*1024**3,190*1024**3-existing):
        raise ValueError('AVI preparation needs up to 190 GiB free beside the destination (source ZIP is preserved)')
    # A changed source must never resume chunks from a different ZIP.
    source_stamp={'size':before.st_size,'mtime_ns':before.st_mtime_ns,'crc32':identity['member_crc32'],
                  'converter_sha256':digest(Path(__file__).with_name('avi_convert.cpp'))}
    stamp=work/'source.json'
    if stamp.exists() and json.loads(stamp.read_text())!=source_stamp:
        raise ValueError('AVI source changed since interrupted preparation; use a new destination')
    atomic_json(stamp,source_stamp)
    jobs=[]
    for ref in identity['chromosomes']:
        name=ref['chrom']; output=work/(name+'.vcf.gz'); stats=work/(name+'.json')
        done=False
        if output.is_file() and stats.is_file():
            try:
                old=json.loads(stats.read_text()); done=old['rows']==ref['rows'] and old['sha256']==digest(output)
            except (ValueError,KeyError,OSError): pass
        jobs.append((ref,output,stats,done))
    active=[]
    def stop(*_):
        for p in active:
            if p.poll() is None: p.terminate()
        raise KeyboardInterrupt
    previous=signal.signal(signal.SIGTERM,stop)
    try:
        pending=[j for j in jobs if not j[3]]; completed=sum(j[0]['rows'] for j in jobs if j[3])
        while pending or active:
            while pending and len(active)<workers:
                ref,output,stats,_=pending.pop(0); stats.unlink(missing_ok=True)
                progress=work/(ref['chrom']+'.progress'); progress.unlink(missing_ok=True)
                virtual=ref['virtual_start']
                cmd=[str(converter),str(archive),str(identity['member_offset']+(virtual>>16)),str(virtual&65535),ref['chrom'],str(ref['rows']),str(output),str(stats),str(progress)]
                p=subprocess.Popen(cmd); p.avi_job=(ref,output,stats,progress); active.append(p)
            visible=completed
            for p in list(active):
                ref,output,stats,progress=p.avi_job
                if p.poll() is not None:
                    if p.returncode: raise ValueError(f"AVI {ref['chrom']} conversion failed; completed chromosomes are resumable")
                    value=json.loads(stats.read_text()); value['sha256']=digest(output); atomic_json(stats,value)
                    completed+=ref['rows']; visible+=ref['rows']; active.remove(p)
                else:
                    try: visible+=min(ref['rows'],int(progress.read_text()))
                    except (OSError,ValueError): pass
            print(f'{85*visible/EXPECTED_ROWS:.1f}% Preparing AlphaGenome AVI: {visible:,} / {EXPECTED_ROWS:,} SNV scores',flush=True)
            if active: time.sleep(10)
    finally:
        for p in active:
            if p.poll() is None: p.terminate()
        for p in active:
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
        signal.signal(signal.SIGTERM,previous)
    bundle=work/'bundle'; bundle.mkdir(exist_ok=True)
    scores=bundle/'avi.grch38.vcf.gz'
    print('86% Combining verified AlphaGenome AVI chromosome blocks',flush=True)
    with scores.open('wb') as target:
        target.write(bgzf(vcf_header()))
        for ref,output,stats,_ in jobs:
            with output.open('rb') as source:
                remaining=output.stat().st_size-len(EOF)
                while remaining:
                    data=source.read(min(8*1024*1024,remaining))
                    if not data: raise ValueError('Truncated chromosome output')
                    target.write(data); remaining-=len(data)
                if source.read()!=EOF: raise ValueError('Chromosome BGZF EOF missing')
        target.write(EOF)
    print('90% Building AlphaGenome AVI VCF Tabix index',flush=True); _tabix(scores)
    print('94% Verifying AlphaGenome AVI bundle and source checksums',flush=True)
    source_hash=digest(archive)
    after=archive.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('Source ZIP changed during preparation')
    files={}
    for path in (scores,Path(str(scores)+'.tbi')):
        stat=path.stat(); files[path.name]={'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'sha256':digest(path)}
    manifest={'schema':SCHEMA,'release':RELEASE,'assembly':'GRCh38','rows':EXPECTED_ROWS,
              'positions':EXPECTED_ROWS//3,'source':{'url':SOURCE_URL,'archive_sha256':source_hash,**source_stamp},
              'terms_url':TERMS_URL,'chromosomes':{r['chrom'][3:]:r['rows'] for r in identity['chromosomes']},
              'transformation':'Lossless per-position ALT grouping; Number=A raw/phred; chr prefix removed; no score rounding or filtering',
              'converter_sha256':digest(Path(__file__).with_name('avi_convert.cpp')),'files':files}
    atomic_json(bundle/'manifest.json',manifest)
    (bundle/'SOURCE_AND_TERMS.txt').write_text(f'AlphaGenome AVI scores — Google DeepMind\nSource: {SOURCE_URL}\nTerms: {TERMS_URL}\nRelease: {RELEASE}\n{manifest["transformation"]}\nAVI is an impact prediction, not a clinical classification.\n',encoding='utf-8')
    final.parent.mkdir(parents=True,exist_ok=True); os.replace(bundle,final)
    atomic_json(root/'current.json',{'release':RELEASE})
    # Only generated, validated chromosome fragments are reclaimed.
    for _,output,stats,_ in jobs:
        output.unlink(missing_ok=True); stats.unlink(missing_ok=True)
    print(f'100% AlphaGenome AVI ready: {final}',flush=True)
    return final


def progress(root: Path) -> dict:
    """Read lightweight progress from an independently launched preparation."""
    if current_bundle(root):
        return {'state':'ready','rows':EXPECTED_ROWS,'total_rows':EXPECTED_ROWS,'percent':100}
    work=root/('.preparing-'+RELEASE)
    rows=0
    for chrom in CONTIGS:
        try:
            stats=json.loads((work/(chrom+'.json')).read_text())
            rows+=int(stats['rows'])
        except (OSError,ValueError,KeyError,TypeError):
            try: rows+=int((work/(chrom+'.progress')).read_text())
            except (OSError,ValueError): pass
    active=False
    try:
        with (root/'.prepare.lock').open('r') as lock:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: active=True
    except OSError: pass
    return {'state':'preparing' if active else 'incomplete','rows':rows,
            'total_rows':EXPECTED_ROWS,'percent':round(85*min(rows,EXPECTED_ROWS)/EXPECTED_ROWS,1)}


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='action',required=True)
    inspect=sub.add_parser('inspect'); inspect.add_argument('archive',type=Path)
    sub.add_parser('source-url')
    sub.add_parser('download-pin')
    prep=sub.add_parser('prepare'); prep.add_argument('--archive',type=Path,required=True); prep.add_argument('--root',type=Path,required=True); prep.add_argument('--converter',type=Path,required=True); prep.add_argument('--workers',type=int,default=4)
    status=sub.add_parser('status'); status.add_argument('root',type=Path); status.add_argument('--strict',action='store_true')
    monitor=sub.add_parser('progress'); monitor.add_argument('root',type=Path)
    args=p.parse_args()
    if args.action=='source-url': print(SOURCE_URL)
    elif args.action=='download-pin': print(ARCHIVE_NAME+'\t'+SOURCE_URL)
    elif args.action=='progress': print(json.dumps(progress(args.root),indent=2))
    elif args.action=='inspect': print(json.dumps(inspect_archive(args.archive),indent=2))
    elif args.action=='prepare':
        if not 1<=args.workers<=8: p.error('workers must be 1–8')
        prepare(args.archive.resolve(),args.root.resolve(),args.converter.resolve(),args.workers)
    else:
        manifest=current_bundle(args.root)
        if not manifest or not valid_bundle(manifest,strict=args.strict): return 1
        print(manifest.parent)
    return 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (OSError,ValueError,struct.error,zipfile.BadZipFile,subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}',flush=True); raise SystemExit(1)
