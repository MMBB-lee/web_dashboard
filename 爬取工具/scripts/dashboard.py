"""Local crawler console; raw SQLite stays read-only, project imports are explicit actions."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = Path.home() / 'Library/Application Support/AnhuiPublicDataCrawler/data'
REQUIRED = {'ac_meta', 'ac_entities', 'ac_sources', 'ac_source_state', 'ac_assets', 'ac_facts', 'ac_runs', 'ac_issues'}


def decoded(value, default=None):
    if not value:
        return {} if default is None else default
    return json.loads(value)


def public_url(value):
    try:
        parsed = urlsplit(value or '')
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def run_view(row):
    summary = decoded(row['summary'])
    # Deliberately project public fields. Summaries can contain internal paths.
    return {k: row[k] for k in ('id', 'kind', 'status', 'started_at', 'finished_at')} | {
        'source_id': summary.get('source_id'), 'records': summary.get('records', {}),
        'pending': summary.get('pending'), 'review_count': len(summary.get('review_required', [])),
        'items': [{'url': public_url(i.get('url')), 'status': i.get('status'),
                   'reason': i.get('reason'), 'http_status': i.get('http_status'), 'counts': i.get('counts')}
                  for i in summary.get('items', [])],
    }


def read_dashboard(data_root):
    db = Path(data_root).resolve() / 'crawler.sqlite3'
    # mode=ro forbids creation, schema migration, and application writes.
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED <= tables or any(not name.startswith('ac_') and name != 'sqlite_sequence' for name in tables):
            raise ValueError('不是受支持的独立爬虫数据库')
        entities = []
        for r in conn.execute('SELECT id,name,type,details FROM ac_entities ORDER BY id'):
            detail = decoded(r['details'])
            entities.append({'id': r['id'], 'name': r['name'], 'type': r['type'],
                'source_ids': detail.get('source_ids', []), 'target_metrics': detail.get('target_metrics', [])})
        states = {r['source_id']: dict(r) for r in conn.execute('SELECT * FROM ac_source_state')}
        sources = []
        for r in conn.execute('SELECT id,url,publisher,details FROM ac_sources ORDER BY id'):
            config = decoded(r['details'])
            state = states.get(r['id'], {})
            detail = decoded(state.get('details'))
            sources.append({'id': r['id'], 'url': public_url(r['url']), 'publisher': r['publisher'],
                'automatic': bool(config.get('automatic_scheduling_enabled', False)),
                'status': state.get('status', 'pending'), 'last_checked': state.get('last_checked'),
                'last_success': state.get('last_success'), 'next_due': state.get('next_due'),
                'pending': len(detail.get('pending', [])), 'review_count': len(detail.get('review_required', [])),
                'failures': [{'url': public_url(f.get('url')), 'reason': f.get('reason'), 'retry_at': f.get('retry_at')}
                             for f in detail.get('failures', [])],
                'reviews': [{'url': public_url(f.get('url')), 'reason': f.get('reason')} for f in detail.get('review_required', [])]})
        facts = []
        for r in conn.execute('''SELECT f.*,a.url AS source_url,a.sha256 AS source_sha256,
              a.published_at AS source_published_at,a.retrieved_at
              FROM ac_facts f JOIN ac_assets a ON a.id=f.asset_id ORDER BY f.period_start,f.entity_id'''):
            fact = dict(r)
            fact['value'] = str(fact['value'])
            fact['locator'] = decoded(fact['locator'])
            fact['source_url'] = public_url(fact['source_url'])
            facts.append(fact)
        runs = [run_view(r) for r in conn.execute('SELECT * FROM ac_runs ORDER BY started_at DESC LIMIT 100')]
        counts = {name: conn.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in ['ac_assets', 'ac_runs', 'ac_issues']}
        running_count = conn.execute("SELECT COUNT(*) FROM ac_runs WHERE status='running'").fetchone()[0]
        version_row = conn.execute("SELECT value FROM ac_meta WHERE key='schema_version'").fetchone()
        schema_version = version_row[0] if version_row else None
    latest = None
    # Read the latest complete update report; ignore a file still being written.
    for path in sorted((Path(data_root) / 'runs').glob('update-*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            latest = {k: value.get(k) for k in ('status', 'started_at', 'finished_at', 'clock_override', 'window')}
            latest['sources'] = [{k: s.get(k) for k in ('source_id', 'status', 'records', 'next_due')} for s in value.get('sources', [])]
            break
        except (OSError, ValueError):
            continue
    standard = [f for f in facts if f['quality'] == 'validated' and not f['qualifier']]
    # Only a known receipt for the same business counts is shown, with its date.
    evidence = None
    evidence_path = ROOT / 'acceptance/2026-10-17-closeout/closeout.json'
    if evidence_path.is_file():
        e = json.loads(evidence_path.read_text(encoding='utf-8'))
        evidence = {'verified_at': e['frozen_at'], 'full_acceptance': e['full_acceptance'],
            'blockers': e['blockers'], 'same_fact_count': e['export']['records'] == len(standard),
            'note': '历史验收快照，不是当前实时完整性检查'}
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'timezone': 'Asia/Shanghai',
        'database_mode': 'read_only', 'schema_version': schema_version,
        'data_label': 'Mac 日常采集库' if Path(data_root).resolve() == DEFAULT_DATA.resolve() else '指定采集库',
        'summary': {'standard': len(standard), 'candidates': len(facts) - len(standard),
            'entities': len(entities), 'entities_with_facts': len({f['entity_id'] for f in standard}),
            'sources': len(sources), 'automatic_sources': sum(s['automatic'] for s in sources),
            'assets': counts['ac_assets'], 'runs': counts['ac_runs'], 'issues': counts['ac_issues'],
            'targets': sum(len(e['target_metrics']) for e in entities),
            'running': running_count},
        'entities': entities, 'sources': sources, 'facts': facts, 'runs': runs,
        'latest_update': latest, 'acceptance': evidence}


def handler_for(data_root):
    token=secrets.token_urlsafe(32)
    task={'status':'idle'}
    lock=threading.Lock()
    package=Path(data_root).resolve()/'project_flow'
    def update_project():
        try:
            run=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/update_project_data.py'),'--apply'],
                cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',timeout=180)
            with lock:
                task.update(status='succeeded' if run.returncode==0 else 'failed',
                            message='采集、导出和MySQL同步完成' if run.returncode==0 else '更新失败，请在终端运行更新脚本查看详情')
        except Exception:
            with lock: task.update(status='failed',message='更新未完成，请在终端运行更新脚本查看详情')
    class Handler(BaseHTTPRequestHandler):
        def project_json(self):
            path=package/'manifest.json'
            payload=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
            rows=payload.get('rows',[])
            from collections import Counter
            return {'token':token,'status':dict(task),'start':payload.get('start'),'end':payload.get('end'),
                    'rows':len(rows),'entities':len({r['entity_name'] for r in rows}),
                    'kinds':dict(Counter(r['data_kind'] for r in rows)), 'anchors':payload.get('anchors',[]),
                    'records':[r for r in rows if r['grain'] in {'month','holiday'}]}

        def do_POST(self):
            host=self.headers.get('Host','')
            origin=urlsplit(self.headers.get('Origin',''))
            if host.split(':')[0] not in {'127.0.0.1','localhost'} or origin.netloc!=host or self.headers.get('X-Project-Token')!=token:
                self.send_error(403);return
            if urlsplit(self.path).path!='/api/project-data/update':
                self.send_error(404);return
            with lock:
                if task['status']=='running':
                    self.send_error(409);return
                task.update(status='running',message='正在采集与同步')
            threading.Thread(target=update_project,daemon=True).start()
            raw=json.dumps({'status':'running'}).encode()
            self.send_response(202);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

        def do_GET(self):
            host = self.headers.get('Host', '').split(':')[0]
            if host not in {'127.0.0.1', 'localhost'}:
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            try:
                if path == '/api/project-data':
                    raw=json.dumps(self.project_json(),ensure_ascii=False).encode()
                    media='application/json; charset=utf-8'
                elif path in {'/api/project-data/export.csv','/api/project-data/manifest.json'}:
                    raw=(package/('flow_data.csv' if path.endswith('.csv') else 'manifest.json')).read_bytes()
                    media='text/csv; charset=utf-8' if path.endswith('.csv') else 'application/json; charset=utf-8'
                elif path == '/api/dashboard':
                    raw = json.dumps(read_dashboard(data_root), ensure_ascii=False).encode()
                    media = 'application/json; charset=utf-8'
                else:
                    names = {'/': ('index.html', 'text/html; charset=utf-8'),
                        '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                        '/style.css': ('style.css', 'text/css; charset=utf-8')}
                    if path not in names:
                        self.send_error(404)
                        return
                    name, media = names[path]
                    raw = (ROOT / 'dashboard' / name).read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', media)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(raw)
            except (sqlite3.Error, OSError, ValueError, KeyError):
                raw = json.dumps({'error': '暂时无法读取采集库，请检查路径、数据库结构或稍后刷新。'}, ensure_ascii=False).encode()
                self.send_response(503)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        def log_message(self, format, *args):
            if args and str(args[1]) != '200':
                super().log_message(format, *args)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true', help='在默认浏览器打开控制台')
    args = parser.parse_args()
    read_dashboard(args.data_root)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.data_root))
    except OSError as exc:
        import errno
        if exc.errno != errno.EADDRINUSE:
            raise
        print(f'端口 {args.port} 已在使用。已启动的控制台可直接访问 http://127.0.0.1:{args.port}；也可使用 --port 指定其他端口。', flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{args.port}')
        return
    print(f'Crawler console: http://127.0.0.1:{server.server_port}', flush=True)
    print('采集库只读；项目序列可通过专用按钮更新并同步MySQL。Press Ctrl+C to stop.', flush=True)
    if args.open:
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
