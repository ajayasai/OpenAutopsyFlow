#!/usr/bin/env python3
"""Publish only the verified source manifest to a NEW public GitHub repository.

Requires the user's locally authenticated GitHub CLI and configured Git identity.
Never publishes a working data directory or changes an existing repository.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    'README.md','LICENSE','NOTICE','SECURITY.md','CONTRIBUTING.md','AGENTS.md',
    'CHANGELOG.md','pyproject.toml','requirements.lock','requirements-dev.lock',
    '.gitignore','.dockerignore','.env.example','Dockerfile','compose.yaml',
    'SOURCE_MANIFEST.json',
}
SOURCE_DIRS = {'openautopsyflow','tests','scripts','docs','.github'}
BINARY_DOCS = {
    'docs/screenshots/dashboard.png','docs/screenshots/examination.png',
    'docs/screenshots/report-traceability.png','docs/screenshots/mobile-report.png',
    'docs/screenshots/synthetic-issued-report.pdf',
}
SECRET_PATTERNS = (
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(rb'github_pat_[A-Za-z0-9_]{40,}'),
    re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
)


def source_files(root: Path) -> list[Path]:
    manifest = json.loads((root/'SOURCE_MANIFEST.json').read_text())
    if manifest.get('format') != 'openautopsyflow-source-v1':
        raise ValueError('Unsupported or missing source manifest')
    result = []
    for name, entry in manifest['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Unsafe manifest path: ' + name)
        if len(path.parts) == 1:
            if name not in ROOT_FILES:
                raise ValueError('Unapproved root file: ' + name)
        elif path.parts[0] not in SOURCE_DIRS:
            raise ValueError('Runtime/unapproved directory: ' + name)
        if any(part in {'data','artifacts','__pycache__','.git','.venv','node_modules'} for part in path.parts):
            raise ValueError('Runtime content is forbidden: ' + name)
        if path.suffix.lower() in {'.sqlite3','.sqlite','.db','.key','.pem','.oafbackup','.log'} or '.env' == path.name:
            raise ValueError('Sensitive runtime file is forbidden: ' + name)
        local = root.joinpath(*path.parts)
        if any(item.is_symlink() for item in (local, *local.parents)):
            raise ValueError('Symlink publication is forbidden: ' + name)
        if not local.is_file() or root.resolve() not in local.resolve().parents:
            raise ValueError('Source file does not resolve inside project: ' + name)
        data = local.read_bytes()
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Source differs from reviewed release manifest: ' + name)
        if name not in BINARY_DOCS:
            data.decode('utf-8')
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                raise ValueError('Possible secret/private key in ' + name)
        result.append(local)
    if not result:
        raise ValueError('Empty source manifest')
    result.append(root/'SOURCE_MANIFEST.json')
    return result


def command(arguments, cwd=None):
    return subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, check=True)


def publish(owner: str, name: str, dry_run: bool = False):
    files = source_files(ROOT)
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', owner) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name):
        raise ValueError('Invalid GitHub owner or repository name')
    print(f'Validated {len(files)} source/document files; runtime data is not included.')
    if dry_run:
        print('Dry run: no GitHub access, repository creation or upload was performed.')
        return
    if not shutil.which('gh') or not shutil.which('git'):
        raise ValueError('Install Git and GitHub CLI, then run gh auth login on your own computer.')
    login = command(['gh','api','user','--jq','.login']).stdout.strip()
    if login.casefold() != owner.casefold():
        raise ValueError(f'Authenticated account is {login}, not {owner}. No changes made.')
    existing = subprocess.run(['gh','api',f'repos/{owner}/{name}'],capture_output=True,text=True)
    if existing.returncode == 0:
        raise ValueError('Repository already exists. Refusing overwrite or visibility change.')
    if '404' not in existing.stderr:
        raise ValueError('Cannot establish that repository is absent. Check GitHub CLI authorization.')
    stage = ROOT/'artifacts/publish-stage'
    if stage.exists():
        raise ValueError('A prior publish stage exists; inspect it before retrying. It was not modified.')
    stage.mkdir(parents=True)
    for source in files:
        target = stage/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source,target)
    command(['git','init','-b','main'],stage)
    try:
        command(['git','var','GIT_AUTHOR_IDENT'],stage)
    except subprocess.CalledProcessError as error:
        shutil.rmtree(stage)  # Only this call's new source-only staging copy.
        raise ValueError('Configure your Git user.name and user.email. No GitHub repository was created; the new staging copy was removed so you can retry.') from error
    command(['git','add','--',*[str(p.relative_to(ROOT)) for p in files]],stage)
    command(['git','commit','-m','Initial OpenAutopsyFlow 0.1.0: evidence-linked casework and tested review controls'],stage)
    try:
        result = command(['gh','repo','create',f'{owner}/{name}','--public','--source',str(stage),
                          '--remote','origin','--push','--description',
                          'Open autopsy casework: traceable findings, protected evidence and human-reviewed reports. Pre-production.'],stage)
    except subprocess.CalledProcessError as error:
        raise ValueError('Publication failed. GitHub may have created the repository before the push failed. Inspect GitHub and artifacts/publish-stage; no force-push or deletion was attempted. Details: '+error.stderr.strip()) from error
    verified = json.loads(command(['gh','repo','view',f'{owner}/{name}','--json','url,visibility'],stage).stdout)
    if verified['visibility'] != 'PUBLIC':
        raise ValueError('Repository creation returned, but PUBLIC visibility was not verified.')
    print(result.stdout.strip())
    print('Verified public repository: '+verified['url'])


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner',default='ajayasai')
    parser.add_argument('--name',default='OpenAutopsyFlow')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        publish(args.owner,args.name,args.dry_run)
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        print('Stopped: '+str(error),file=sys.stderr)
        raise SystemExit(1)
