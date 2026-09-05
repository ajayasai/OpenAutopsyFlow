#!/usr/bin/env python3
"""Regenerate the source release manifest after reviewing all changed files.

This is a source checksum inventory, not a cryptographically trusted attestation.
Only synthetic example artifacts belong under docs/screenshots.
"""
import hashlib
import json
from pathlib import Path
from publish_github import ROOT, ROOT_FILES, SOURCE_DIRS, BINARY_DOCS, source_files

TEXT_SUFFIXES = {'.py','.md','.json','.sql','.css','.js','.html','.yml','.yaml'}


def build(root: Path):
    files={}
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        name=relative.as_posix()
        if name=='SOURCE_MANIFEST.json' or not path.is_file():
            continue
        if len(relative.parts)==1:
            allowed=name in ROOT_FILES
        else:
            allowed=relative.parts[0] in SOURCE_DIRS and (path.suffix.lower() in TEXT_SUFFIXES or name in BINARY_DOCS)
        if not allowed or any(part in {'.git','.venv','__pycache__','artifacts','node_modules','.pytest_cache'} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('Symlink found in source: '+name)
        data=path.read_bytes()
        files[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    (root/'SOURCE_MANIFEST.json').write_text(json.dumps({'format':'openautopsyflow-source-v1',
        'version':'0.1.0','files':files},indent=2,sort_keys=True)+'\n')
    source_files(root)
    print(f'Validated manifest with {len(files)} files plus the manifest itself.')


if __name__=='__main__':
    build(ROOT)
