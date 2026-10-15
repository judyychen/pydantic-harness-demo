"""Three isolated visual samples, official media bytes and retained local choices.

No model calls, application edits, deletion or automatic Agent resumption.
init [--pending-json state-next/pending.json --tool-call-id REAL_ID] creates one set.
Optional --html-a/--html-b/--html-c import the Agent cloud readback as materials, never as recovery seeds.
attach --set-id ID --candidate A --image screenshot.png attaches a root-CUA screenshot.
serve exposes the loopback comparison page. This standalone export has no TOS adapter.
Local SQLite proves same-host recovery only.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import datetime
import hashlib
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import parse_qs, urlsplit
import uuid

from pydantic_ai.messages import ToolCallPart
from pydantic_ai_harness.ask_user import AskUserRequest, AskUserAnswer, AskUserResponse, ask_user_result
from pydantic_ai_harness.media import MediaContext, SqliteMediaStore


ROOT = Path(__file__).resolve().parent
PURPOSE = 'pydantic-next-spike'
TOS_FIELDS = ('TOS_ACCESS_KEY', 'TOS_SECRET_KEY', 'TOS_ENDPOINT', 'TOS_REGION', 'TOS_BUCKET')
DEFAULT_REQUEST = {'questions': [{'header': '视觉方向', 'question': '你更喜欢哪一种茶铺视觉方向？',
    'multi_select': False, 'options': [
        {'label': '东方雅致', 'description': '米白、墨绿与留白，突出茶的产地和工艺。'},
        {'label': '轻盈极简', 'description': '明亮背景和清晰商品网格，突出便捷浏览。'},
        {'label': '明快品牌', 'description': '醒目色块和大标题，突出品牌与活动。'}]}]}


def media(db):
    return SqliteMediaStore(database=db)


@contextmanager
def database(db):
    db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db)
    try:
        with connection:
            connection.execute('CREATE TABLE IF NOT EXISTS visual_sets (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            yield connection
    finally: connection.close()


def save(db, manifest):
    with database(db) as connection:
        connection.execute('INSERT INTO visual_sets VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                           (manifest['id'], json.dumps(manifest, ensure_ascii=False)))


def load(db, set_id=None):
    with database(db) as connection:
        row = connection.execute('SELECT payload FROM visual_sets WHERE id=?', (set_id,)).fetchone() if set_id else \
              connection.execute('SELECT payload FROM visual_sets ORDER BY rowid DESC LIMIT 1').fetchone()
    if row is None: raise FileNotFoundError('visual-set-not-found')
    return json.loads(row[0])


def request_for(manifest):
    return AskUserRequest.from_tool_call(ToolCallPart(tool_name='ask_user_question',
        tool_call_id=manifest['tool_call_id'], args=manifest['request']))


def candidate_html(index, label):
    palettes = [('#f4f0e8', '#224739', '#d5b477', 'Georgia,serif'),
                ('#f8faf9', '#22443e', '#c5ddd0', 'Arial,sans-serif'),
                ('#f7eddb', '#bc3f2b', '#e7b75b', 'Arial,sans-serif')]
    bg, ink, accent, font = palettes[index]
    layout = 'grid-template-columns:1.4fr 1fr' if index != 1 else 'grid-template-columns:1fr 1fr'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(label)} · 视觉样例</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:{bg};color:{ink};font-family:{font};padding:34px 6vw}}a{{color:inherit;text-decoration:none}}
nav{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {ink}30;padding-bottom:22px}}nav strong{{font-size:24px;letter-spacing:4px}}nav span{{font:14px Arial,sans-serif}}
main{{max-width:1200px;margin:44px auto}}.hero{{display:grid;{layout};gap:44px;align-items:center}}.eyebrow{{font:12px Arial,sans-serif;letter-spacing:4px}}h1{{font-size:clamp(42px,6vw,84px);font-weight:{'500' if index == 0 else '800'};line-height:1.12;margin:22px 0}}p{{line-height:1.8}}.disc{{aspect-ratio:1;border-radius:{'50%' if index == 0 else '24px'};background:radial-gradient(circle at 35% 30%,{accent} 0 15%,transparent 16%),repeating-radial-gradient(circle at 55% 55%,{ink}15 0 3px,transparent 4px 16px),{accent}66;display:grid;place-items:center;font-size:88px}}
button{{background:{ink};color:{bg};border:0;border-radius:{'0' if index == 0 else '28px'};padding:14px 26px;font:16px Arial,sans-serif}}.items{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:52px}}article{{border-top:1px solid {ink}50;padding-top:18px}}article .swatch{{height:100px;background:{accent}88;border-radius:12px;display:grid;place-items:center;font-size:38px}}article h2{{font-size:20px}}small{{font:12px Arial,sans-serif}}@media(max-width:640px){{.hero{{grid-template-columns:1fr}}.disc{{max-height:280px}}.items{{gap:12px}}}}
</style><nav><strong>山野茶铺</strong><span>茶品 / 产地 / 我们</span></nav><main><section class="hero"><div><div class="eyebrow">TEA FROM THE MOUNTAINS</div><h1>{['一盏茶，<br>一段山野。','把好茶，<br>带进每一天。','今天，<br>喝点不一样。'][index]}</h1><p>从山间到杯中，感受茶叶的清香与四季。<br>这是供比较配色与布局的代表页面。</p><button type="button" disabled>浏览茶品 · 展示样例</button></div><div class="disc" aria-label="茶叶纹理装饰">茶</div></section><section class="items">{''.join('<article><div class="swatch">'+symbol+'</div><h2>'+name+'</h2><p>'+text+'</p><small>演示内容 · 无购买功能</small></article>' for symbol,name,text in [('叶','春日绿茶','清新、轻盈，像山间的第一缕风。'),('山','山野乌龙','花香与焙火，层次丰富。'),('日','温润红茶','柔和醇厚，适合悠闲的午后。')])}</section></main><footer><small>{html.escape(label)} · 独立视觉候选，不修改现有应用。</small></footer></html>'''.encode()


async def asset(db, data, content_type):
    if not data or len(data) > 8_000_000: raise ValueError('material-size-out-of-range')
    uri = await media(db).put(data, context=MediaContext(media_type=content_type))
    return {'uri': uri, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'content_type': content_type, 'persistence': 'same-host-sqlite'}


async def initialize(db, owner, request=None, tool_call_id=None, html_paths=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', owner): raise ValueError('invalid-experiment-owner')
    if tool_call_id is not None and (not isinstance(tool_call_id, str) or not tool_call_id.strip()):
        raise ValueError('nonempty-original-tool-id-required')
    imported = None
    if html_paths is not None:
        if len(html_paths) != 3 or (any(html_paths) and not all(html_paths)):
            raise ValueError('html-a-b-c-must-be-provided-together')
        if all(html_paths):
            imported = []
            for path in html_paths:
                if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 8_000_000:
                    raise ValueError('regular-nonsymlink-HTML-file-required')
                data = path.read_bytes()
                data.decode('utf-8')  # Strict decode; preserve the original cloud bytes/hash.
                if b'\x00' in data: raise ValueError('text-UTF8-HTML-required')
                imported.append(data)
    manifest = {'id': uuid.uuid4().hex, 'owner': owner, 'purpose': PURPOSE,
                'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'generator': 'agent-cloud-html-readback' if imported else 'fixed-host-lightweight-pages',
                'bound_to_model': tool_call_id is not None,
                'tool_call_id': tool_call_id or 'standalone-' + uuid.uuid4().hex,
                'request': request or DEFAULT_REQUEST, 'candidates': [], 'choices': []}
    req = request_for(manifest)
    if len(req.questions) != 1 or req.questions[0].multi_select or len(req.questions[0].options) != 3:
        raise ValueError('exactly-one-single-choice-question-with-three-options-required')
    directions = []
    for option in req.questions[0].options:
        matches = [i for i, preset in enumerate(DEFAULT_REQUEST['questions'][0]['options']) if option.label.startswith(preset['label'])]
        if len(matches) != 1: raise ValueError('this-probe-requires-the-three-named-visual-presets')
        directions.append(matches[0])
    if len(set(directions)) != 3: raise ValueError('three-distinct-visual-directions-required')
    for index, option in enumerate(req.questions[0].options):
        direction = directions[index]  # a=东方, b=极简, c=品牌 even if AskUser changes its option order.
        data = imported[direction] if imported else candidate_html(direction, option.label)
        material = await asset(db, data, 'text/html; charset=utf-8')
        if imported:
            material['imported_filename'] = html_paths[direction].name
            material['source_direction'] = ('a-oriental', 'b-minimal', 'c-brand')[direction]
        manifest['candidates'].append({'id': chr(65 + index), 'label': option.label,
            'description': option.description or '', 'html': material, 'image': None})
    save(db, manifest)
    return manifest


def candidate(manifest, candidate_id):
    for item in manifest['candidates']:
        if item['id'] == candidate_id: return item
    raise ValueError('unknown-candidate')


async def attach(db, set_id, candidate_id, path):
    manifest = load(db, set_id)
    item = candidate(manifest, candidate_id)
    if item['image'] is not None: raise ValueError('existing-screenshot-refused-preserve-history')
    data = path.read_bytes()
    content_type = 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else \
                   'image/jpeg' if data.startswith(b'\xff\xd8\xff') else \
                   'image/webp' if data.startswith(b'RIFF') and data[8:12] == b'WEBP' else None
    if content_type is None: raise ValueError('png-jpeg-webp-screenshot-required')
    item['image'] = await asset(db, data, content_type)
    save(db, manifest)
    return manifest


def configured_tos(client=None):
    raise RuntimeError('TOS is not included in this standalone demo; local SQLite is supported.')


async def read_asset(db, item, storage=None):
    if item.get('remote'):
        stored = await (storage or configured_tos()).get(item['remote']['key'])
        if stored is None: raise FileNotFoundError('TOS-material-missing-no-local-fallback')
        data = stored.data
    else:
        data = await media(db).get(item['uri'])
    if hashlib.sha256(data).hexdigest() != item['sha256']: raise ValueError('material-hash-mismatch')
    return data


async def sync_catalog(db, manifest, storage):
    # The signed URL is never persisted; the host reads private objects using stable keys.
    key = manifest['remote_catalog_key']
    pending = dict(manifest, remote_catalog_synced_choices=len(manifest['choices']))
    payload = json.dumps(pending, ensure_ascii=False).encode()
    await storage.put(key, payload)
    downloaded = await storage.get(key)
    if downloaded is None or downloaded.data != payload: raise RuntimeError('TOS-catalog-readback-failed')
    manifest['remote_catalog_synced_choices'] = len(manifest['choices'])
    save(db, manifest)


async def upload_tos(db, set_id, storage=None):
    storage = storage or configured_tos()
    manifest = load(db, set_id)
    prefix = f"agent-spikes/visual/{manifest['owner']}/{manifest['id']}"
    for option in manifest['candidates']:
        for kind in ('html', 'image'):
            item = option[kind]
            if item is None: continue
            # Read local content explicitly for upload, then verify actual remote bytes.
            data = await media(db).get(item['uri'])
            key = storage.scoped_key(prefix + '/' + item['sha256'] + ('.html' if kind == 'html' else '.image'))
            await storage.put(key, data)
            stored = await storage.get(key)
            if stored is None or stored.data != data: raise RuntimeError('TOS-material-readback-failed')
            item.update(remote={'key': key, 'object_url': storage.object_url(key)}, persistence='tos-readback-confirmed')
            save(db, manifest)
    manifest['remote_catalog_key'] = storage.scoped_key(prefix + '/manifest.json')
    await sync_catalog(db, manifest, storage)
    return manifest


async def choose(db, set_id, candidate_id, custom_answer='', storage=None):
    manifest = load(db, set_id)
    option = candidate(manifest, candidate_id)
    if not isinstance(custom_answer, str) or len(custom_answer) > 1000: raise ValueError('invalid-custom-answer')
    req = request_for(manifest)
    answer = AskUserAnswer(header=req.questions[0].header, custom_answer=custom_answer.strip()) if custom_answer.strip() else \
             AskUserAnswer(header=req.questions[0].header, selected=(option['label'],))
    result = ask_user_result(req, AskUserResponse(answers=(answer,)))
    record = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'candidate_id': candidate_id,
              'answer_text': custom_answer.strip() or option['label'], 'tool_call_id': manifest['tool_call_id'],
              'tool_result': result, 'agent_delivery': 'not-sent'}
    manifest['choices'].append(record)
    save(db, manifest)  # Retain the answer even if the independent remote sync fails.
    if manifest.get('remote_catalog_key'):
        await sync_catalog(db, manifest, storage or configured_tos())
    return record


def public_manifest(manifest):
    clean = json.loads(json.dumps(manifest))
    clean.pop('remote_catalog_key', None)
    for item in clean['candidates']:
        for kind in ('html', 'image'):
            if item[kind] is not None:
                item[kind].pop('remote', None)
                item[kind]['url'] = f"/material/{clean['id']}/{item['id']}/{kind}"
    clean['missing_tos_fields'] = [name for name in TOS_FIELDS if not os.environ.get(name)]
    clean['agent_delivery'] = 'not-connected; retained-result-can-answer-original-tool-call'
    return clean


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass  # Never log a token, request body or private storage URL.

    def send(self, status, body, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if content_type.startswith('text/html'):
            policy = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self'; sandbox" if urlsplit(self.path).path.startswith('/material/') else \
                     "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'"
            self.send_header('Content-Security-Policy', policy)
        self.end_headers(); self.wfile.write(body)

    def dispatch(self):
        port = self.server.server_port
        hosts = (f'127.0.0.1:{port}', f'localhost:{port}')
        if self.headers.get('Host') not in hosts: raise ValueError('loopback-host-required')
        path = urlsplit(self.path)
        db = self.server.db
        if self.command == 'GET' and path.path == '/':
            return 200, (ROOT / 'visual-check.html').read_bytes(), 'text/html; charset=utf-8'
        if self.command == 'GET' and path.path == '/api/manifest':
            manifest = load(db, parse_qs(path.query).get('set', [None])[0])
            return 200, json.dumps(public_manifest(manifest), ensure_ascii=False).encode(), 'application/json'
        match = re.fullmatch(r'/material/([0-9a-f]{32})/([ABC])/(html|image)', path.path)
        if self.command == 'GET' and match:
            item = candidate(load(db, match[1]), match[2])[match[3]]
            if item is None: raise FileNotFoundError('screenshot-not-attached')
            return 200, asyncio.run(read_asset(db, item)), item['content_type']
        if self.command == 'POST' and path.path == '/api/choose':
            if self.headers.get('Origin') not in (None, *(f'http://{h}' for h in hosts)):
                raise ValueError('same-origin-required')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('json-required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192: raise ValueError('choice-body-size')
            payload = json.loads(self.rfile.read(length))
            if set(payload) - {'set_id', 'candidate_id', 'custom_answer'}: raise ValueError('unexpected-choice-fields')
            record = asyncio.run(choose(db, payload['set_id'], payload['candidate_id'], payload.get('custom_answer', '')))
            return 200, json.dumps(record, ensure_ascii=False).encode(), 'application/json'
        raise FileNotFoundError('route-not-found')

    def do_GET(self):
        try: self.send(*self.dispatch())
        except FileNotFoundError: self.send(404, b'{"error":"not-found"}')
        except (ValueError, KeyError, TypeError): self.send(400, b'{"error":"invalid-request"}')
        except Exception: self.send(503, b'{"error":"storage-unavailable; no-local-fallback"}')

    do_POST = do_GET


def server(db, port=7950):
    # ponytail: one loopback operator; platform auth and concurrent catalog writes come with multi-user integration.
    result = HTTPServer(('127.0.0.1', port), Handler)
    result.db = db
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'attach', 'serve', 'upload-tos', 'manifest'))
    parser.add_argument('--db', type=Path, default=ROOT / 'state-next' / 'visual.db')
    parser.add_argument('--owner')
    parser.add_argument('--set-id')
    parser.add_argument('--candidate', choices=('A', 'B', 'C'))
    parser.add_argument('--image', type=Path)
    parser.add_argument('--pending-json', type=Path)
    parser.add_argument('--tool-call-id')
    parser.add_argument('--html-a', type=Path)
    parser.add_argument('--html-b', type=Path)
    parser.add_argument('--html-c', type=Path)
    parser.add_argument('--port', type=int, default=7950)
    args = parser.parse_args()
    if args.action == 'serve': server(args.db, args.port).serve_forever()
    else:
        try:
            if args.action == 'init':
                request = json.loads(args.pending_json.read_text())[args.tool_call_id] if args.pending_json else None
                if bool(args.pending_json) != bool(args.tool_call_id): raise ValueError('pending-and-tool-id-required-together')
                manifest = asyncio.run(initialize(args.db, args.owner or '', request, args.tool_call_id,
                    html_paths=(args.html_a, args.html_b, args.html_c)))
            elif args.action == 'attach': manifest = asyncio.run(attach(args.db, args.set_id, args.candidate, args.image))
            elif args.action == 'upload-tos': manifest = asyncio.run(upload_tos(args.db, args.set_id))
            else: manifest = load(args.db, args.set_id)
            print(json.dumps(public_manifest(manifest), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({'error_type': type(exc).__name__, 'missing_tos_fields': [k for k in TOS_FIELDS if not os.environ.get(k)]}))
            sys.exit(1)
