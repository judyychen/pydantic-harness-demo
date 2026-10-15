"""One offline, no-model/no-cloud check: official media reopen + choices + loopback boundaries."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import visual_materials as visual

urlopen = build_opener(ProxyHandler({})).open


async def check(db):
    manifest = await visual.initialize(db, 'visual-check-owner-20261014')
    set_id = manifest['id']
    assert len(manifest['candidates']) == 3 and not manifest['bound_to_model']
    for option in manifest['candidates']:
        original = await visual.read_asset(db, option['html'])
        assert b'<!doctype html>' in original and option['html']['sha256'] in option['html']['uri']
    image = db.parent / 'shot.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+b9WQAAAAASUVORK5CYII='))
    await visual.attach(db, set_id, 'A', image)
    await visual.choose(db, set_id, 'A')
    record = await visual.choose(db, set_id, 'B', 'A 的布局，加 B 的配色')
    assert record['tool_result'] == {'视觉方向': ['A 的布局，加 B 的配色']}
    reopened = visual.load(db, set_id)
    assert len(reopened['candidates']) == 3 and len(reopened['choices']) == 2
    assert await visual.media(db).get(reopened['candidates'][0]['image']['uri']) == image.read_bytes()
    assert await visual.media(db).public_url(reopened['candidates'][0]['html']['uri']) is None
    assert all(option['html']['persistence'] == 'same-host-sqlite' for option in reopened['candidates'])
    assert reopened['choices'][-1]['agent_delivery'] == 'not-sent'
    try: await visual.choose(db, set_id, 'wrong')
    except ValueError: pass
    else: raise AssertionError('unknown-candidate-accepted')
    # Supplied bytes must replace the presets; labels still map to the original a/b/c direction.
    sources = tuple(db.parent / name for name in ('a.html', 'b.html', 'c.html'))
    for index, path in enumerate(sources): path.write_text(f'<!doctype html><meta charset="utf-8"><h1>提供的真实材料 {index} · 茶</h1>', encoding='utf-8')
    request = json.loads(json.dumps(visual.DEFAULT_REQUEST))
    request['questions'][0]['options'].reverse()
    supplied = await visual.initialize(db, 'visual-import-owner-20261014', request, 'original-cloud-question', html_paths=sources)
    assert supplied['generator'] == 'agent-cloud-html-readback'
    for option, source in zip(supplied['candidates'], reversed(sources)):
        assert await visual.read_asset(db, option['html']) == source.read_bytes()
        assert option['html']['imported_filename'] == source.name
    try: await visual.initialize(db, 'visual-import-owner-20261014', html_paths=(sources[0], None, None))
    except ValueError: pass
    else: raise AssertionError('partial-import-accepted')
    link = db.parent / 'linked.html'; link.symlink_to(sources[0])
    try: await visual.initialize(db, 'visual-import-owner-20261014', html_paths=(link, sources[1], sources[2]))
    except ValueError: pass
    else: raise AssertionError('symlink-import-accepted')
    try: visual.configured_tos()
    except RuntimeError as error: assert 'not included' in str(error)
    else: raise AssertionError('standalone-demo-must-not-load-company-TOS-code')
    return set_id


async def check_workbench_routes(state):
    """Real Starlette routes, isolated SQLite material bytes; no live state or network."""
    import httpx
    state.mkdir()
    # Importing server otherwise opens the live pending/reference paths.
    with patch.dict(os.environ, {'SPIKE_STATE_DIR': str(state), 'SPIKE_SEED': '0', 'PYDANTIC_AI_NO_BANNER': '1'}):
        import server
    assert server.STATE == state.resolve()
    owner='workbench-visual-check-20261014'
    db=state/'visual.db'
    files=tuple(state/name for name in ('a.html','b.html','c.html'))
    for index,path in enumerate(files):
        path.write_text(f'<!doctype html><meta charset="utf-8"><style>h1{{color:green}}</style><h1>候选 {index}</h1>',encoding='utf-8')
    foreign=await visual.initialize(db,'other-workbench-owner-20261014',tool_call_id='foreign-choice',html_paths=files)
    call_id='workbench-pending-visual-choice'
    bound=await visual.initialize(db,owner,tool_call_id=call_id,html_paths=files)
    image=state/'candidate.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+b9WQAAAAASUVORK5CYII='))
    for candidate_id in ('A','B','C'): await visual.attach(db,bound['id'],candidate_id,image)
    original_state,original_workspace,original_pending=server.STATE,server.workspace,server.pending
    server.workspace=SimpleNamespace(reference={'owner':owner})
    server.pending={call_id:bound['request']}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://offline') as client:
            response=await client.get('/spike/visual/'+call_id)
            assert response.status_code==200 and response.headers['cache-control']=='no-store'
            manifest=response.json()
            assert manifest['tool_call_id']==call_id and len(manifest['candidates'])==3
            assert [item['label'] for item in manifest['candidates']]==['东方雅致','轻盈极简','明快品牌']
            for item in manifest['candidates']:
                for kind in ('html','image'):
                    part=item[kind]
                    assert part['url']==f"/spike/visual-material/{bound['id']}/{item['id']}/{kind}"
                    material=await client.get(part['url'])
                    expected=await visual.read_asset(db,visual.candidate(visual.load(db,bound['id']),item['id'])[kind])
                    assert material.status_code==200 and material.content==expected
                    assert hashlib.sha256(material.content).hexdigest()==part['sha256']
                    assert material.headers['cache-control']=='no-store' and material.headers['x-content-type-options']=='nosniff'
                    if kind=='html':
                        policy=material.headers['content-security-policy']
                        for directive in ("default-src 'none'","script-src 'none'","connect-src 'none'","img-src data:","frame-ancestors 'self'","sandbox"):
                            assert directive in policy
                        assert 'allow-scripts' not in policy and 'allow-same-origin' not in policy
            for missing in ('prior-unmapped-choice','wrong-choice','foreign-choice'):
                assert (await client.get('/spike/visual/'+missing)).status_code==404
            assert (await client.get(f"/spike/visual-material/{foreign['id']}/A/html")).status_code==404
            for set_id,candidate_id,kind in ((bound['id'],'D','html'),(bound['id'],'A','invalid'),('invalid','A','html')):
                assert (await client.get(f'/spike/visual-material/{set_id}/{candidate_id}/{kind}')).status_code==404
            alias=await client.get('/designs/a.html')
            stable=f"/spike/visual-material/{bound['id']}/A/html"
            assert alias.status_code==307 and alias.headers['location']==stable
            followed=await client.get('/designs/a.html',follow_redirects=True)
            assert followed.status_code==200 and followed.content==files[0].read_bytes()
            assert (await client.get('/designs/unknown.html')).status_code==404
        print('PASS: workbench pending-call manifest exposes three byte/hash-matching HTML/PNG candidates; owner/path rejection, restrictive HTML CSP and stable relative-link redirect pass')
    finally:
        server.STATE,server.workspace,server.pending=original_state,original_workspace,original_pending


def main():
    markup = (visual.ROOT / 'visual-check.html').read_text()
    script = markup.split('<script>', 1)[1].split('</script>', 1)[0]
    subprocess.run(['node', '--check'], input=script, text=True, check=True, capture_output=True)
    with tempfile.TemporaryDirectory(prefix='visual-offline-check-') as temp:
        db = Path(temp) / 'visual.db'
        set_id = asyncio.run(check(db))
        asyncio.run(check_workbench_routes(Path(temp)/'workbench-state'))
        host = visual.server(db, 0)
        worker = threading.Thread(target=host.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{host.server_port}'
        try:
            with urlopen(origin + '/api/manifest?set=' + set_id) as response:
                manifest = json.load(response)
            assert len(manifest['candidates']) == 3 and len(manifest['choices']) == 2
            with urlopen(origin + manifest['candidates'][1]['html']['url']) as response:
                assert response.status == 200 and b'<!doctype html>' in response.read()
                policy = response.headers['Content-Security-Policy']
                assert "script-src 'none'" in policy and "default-src 'none'" in policy and "connect-src 'none'" in policy
            body = json.dumps({'set_id': set_id, 'candidate_id': 'C'}).encode()
            with urlopen(Request(origin + '/api/choose', data=body, headers={'Content-Type': 'application/json', 'Origin': origin})) as response:
                assert json.load(response)['agent_delivery'] == 'not-sent'
            try:
                urlopen(Request(origin + '/api/choose', data=body, headers={'Content-Type': 'application/json', 'Origin': 'https://untrusted.invalid'}))
            except HTTPError as error: assert error.code == 400
            else: raise AssertionError('cross-origin-choice-accepted')
            assert len(visual.load(db, set_id)['choices']) == 3
        finally:
            host.shutdown(); host.server_close(); worker.join()
    print(json.dumps({'offline_check': 'passed', 'model_calls': 0, 'cloud_calls': 0,
                      'sqlite_same_host_recovery': True, 'TOS_verified': False}))


if __name__ == '__main__': main()
