#!/usr/bin/env python3
"""Run a loopback-only synthetic demo with generated accounts and a dedicated data directory."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if os.environ.get('OAF_MASTER_KEY'):
    raise SystemExit('A master key is already set. Use a clean shell for the dedicated synthetic demo; production configuration was not modified.')
os.environ.update(OAF_DEMO='1',OAF_DATA_DIR=str(ROOT/'artifacts/demo-data'),
                  OAF_HOSTS='localhost,127.0.0.1',
                  OAF_ORIGINS='http://127.0.0.1:8000,http://localhost:8000')
from openautopsyflow.api import create_app
from openautopsyflow.cli import seed_demo
import uvicorn
app=create_app()
with app.state.store.read() as db:
    empty=not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()
if empty:
    accounts=seed_demo(app.state.store)
    print('\nSYNTHETIC DEMO ONLY — do not enter real case material.\n',flush=True)
    for username,password in accounts.items():
        print(f'{username}: {password}',flush=True)
else:
    print('Existing synthetic demo retained. Accounts/passwords were not reset.',flush=True)
print('\nOpen http://127.0.0.1:8000 — stop with Ctrl+C.\n',flush=True)
uvicorn.run(app,host='127.0.0.1',port=8000,proxy_headers=False)
