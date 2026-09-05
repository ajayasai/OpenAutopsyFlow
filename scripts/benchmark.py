#!/usr/bin/env python3
"""Small, reproducible single-process synthetic benchmark; not a vendor comparison."""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
import secrets
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from openautopsyflow import service as V, schemas as S
from openautopsyflow.api import create_app
from openautopsyflow.security import create_user
from openautopsyflow.store import Settings


def benchmark(count: int, repeats: int, output: Path):
    with tempfile.TemporaryDirectory(prefix='oaf-bench-') as folder:
        settings=Settings(Path(folder),secrets.token_bytes(32),True,False,('testserver',),('http://testserver',))
        app=create_app(settings);store=app.state.store
        with store.transaction() as db:
            ident=create_user(db,'benchmark','Synthetic benchmark',secrets.token_urlsafe(24),True)
            user={'id':ident};token=secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),ident,'benchmark-csrf',time.time()+3600,time.time()))
            start=time.perf_counter()
            for index in range(count):
                cid=V.create_case(db,user,S.CaseData(case_no=f'SYN-{index:07}',examination_date=date(2026,9,1),
                     requesting_authority='Synthetic authority',examiner='Synthetic Examiner'))
                for task in range(3):
                    V.add_record(db,cid,user,S.RecordCreate(revision=task+1,kind='task',label=f'Task {task}',
                        data=S.RecordData(text='Synthetic pending work only',due_date=date.today()+timedelta(days=task))))
            seed_seconds=time.perf_counter()-start
        client=TestClient(app);client.cookies.set('oaf_session',token)
        results={}
        for path in ('/api/cases','/api/cases?pending_only=true','/api/cases?q=SYN-00000'):
            client.get(path)
            times=[]
            for _ in range(repeats):
                start=time.perf_counter();response=client.get(path);elapsed=(time.perf_counter()-start)*1000
                assert response.status_code==200,response.text
                times.append(elapsed)
            ordered=sorted(times)
            results[path]={'median_ms':round(statistics.median(times),2),
                           'p95_ms':round(ordered[max(0,int(.95*len(ordered))-1)],2),
                           'min_ms':round(min(times),2),'max_ms':round(max(times),2)}
        client.close()
        value={'python':platform.python_version(),'platform':platform.platform(),'cases':count,
               'tasks':count*3,'repeats':repeats,'fixture_seed_seconds':round(seed_seconds,3),
               'database_bytes':store.path.stat().st_size,'results':results,
               'method':'Sequential in-process ASGI client, synthetic cases, no reports/photos, warm cache.',
               'limitations':'Not production capacity, concurrency, network latency, or a head-to-head vendor comparison.'}
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(value,indent=2)+'\n')
        print(json.dumps(value,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=int,default=1000)
    parser.add_argument('--repeats',type=int,default=30)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/benchmark.json')
    args=parser.parse_args()
    if not 1<=args.cases<=100000 or not 5<=args.repeats<=500:
        parser.error('Use 1..100000 cases and 5..500 repetitions')
    benchmark(args.cases,args.repeats,args.output)
