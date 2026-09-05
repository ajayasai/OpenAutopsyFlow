"""Local-only tests; these never contact GitHub or publish a repository."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('oaf_publisher',Path(__file__).parents[1]/'scripts/publish_github.py')
publisher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def prepare(root,name='README.md',data=b'SYNTHETIC source release'):
    target=root/name
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
    (root/'SOURCE_MANIFEST.json').write_text(json.dumps({'format':'openautopsyflow-source-v1',
        'files':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}}}))
    return target


def test_publisher_only_includes_manifest_allowlist(tmp_path):
    prepare(tmp_path)
    (tmp_path/'unrelated-private-notes.txt').write_text('DO NOT PUBLISH THIS TEST CANARY')
    assert {p.name for p in publisher.source_files(tmp_path)}=={'README.md','SOURCE_MANIFEST.json'}


def test_publisher_rejects_modified_source(tmp_path):
    prepare(tmp_path).write_text('Changed since reviewed manifest')
    with pytest.raises(ValueError,match='differs'):
        publisher.source_files(tmp_path)


@pytest.mark.parametrize('name',['data/case.json','docs/private.key','openautopsyflow/data/records.json'])
def test_publisher_refuses_runtime_data_even_in_manifest(tmp_path,name):
    prepare(tmp_path,name)
    with pytest.raises(ValueError):
        publisher.source_files(tmp_path)


def test_publisher_rejects_obvious_token_and_private_key(tmp_path):
    for content in [b'gh'+b'p_'+b'A'*40,b'-----BEGIN '+b'RSA PRIVATE KEY-----']:
        prepare(tmp_path,data=content)
        with pytest.raises(ValueError,match='Possible secret'):
            publisher.source_files(tmp_path)


def test_publisher_rejects_symlink(tmp_path):
    prepare(tmp_path)
    destination=tmp_path/'target.txt';destination.write_bytes(b'SYNTHETIC source release')
    path=tmp_path/'README.md';path.unlink();path.symlink_to(destination)
    with pytest.raises(ValueError,match='Symlink'):
        publisher.source_files(tmp_path)


def test_publisher_rejects_parent_traversal(tmp_path):
    (tmp_path/'SOURCE_MANIFEST.json').write_text(json.dumps({'format':'openautopsyflow-source-v1',
        'files':{'../outside.md':{'bytes':0,'sha256':''}}}))
    with pytest.raises(ValueError,match='Unsafe'):
        publisher.source_files(tmp_path)
