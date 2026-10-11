"""PanelCore — stdlib HTTP server. No pip install, no framework, runs anywhere Python does."""
import json, os, io, csv, urllib.parse, threading, time, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import db, core, seed, domain

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, 'static')
PORT = int(os.environ.get('PORT', 8900))
HOST = os.environ.get('HOST', '127.0.0.1')

# Public demo stand. Every visitor gets a private database seeded for their own
# trade, so two people can be inside at the same time without seeing each other.
DEMO_OPEN = os.environ.get('DEMO_OPEN') == '1'
DEMO_MAX_AGE_HOURS = int(os.environ.get('DEMO_MAX_AGE_HOURS', 6))
DEMO_MAX_COUNT = int(os.environ.get('DEMO_MAX_COUNT', 800))

MIME = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
        '.js': 'application/javascript; charset=utf-8', '.svg': 'image/svg+xml',
        '.ico': 'image/x-icon', '.png': 'image/png'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'PanelCore'

    def log_message(self, *a):
        pass  # keep the console readable during a demo

    # --- plumbing -----------------------------------------------------
    def send_json(self, obj, code=200, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        for c in getattr(self, 'new_cookies', ()):
            self.send_header('Set-Cookie', c)
        self.end_headers()
        self.wfile.write(body)

    def cookie(self, name):
        for part in (self.headers.get('Cookie') or '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == name:
                return v
        return None

    def bind_demo(self, path):
        """Hand this visitor their own database, creating it on the first hit.

        `?b=print` in the link decides which trade they land in, so a print shop
        opening the demo sees presses and reprints, not somebody else's business.
        """
        if not DEMO_OPEN:
            return
        self.new_cookies = []
        wanted = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query).get('b', [''])[0]
        if wanted not in _domain_ids():
            wanted = ''
        demo_id = self.cookie('pc_demo') or ''
        file = _demo_file(demo_id)
        if file and os.path.isfile(file):
            db.use(file)
            os.utime(file, None)  # touch: the cleaner only removes idle demos
            # A returning visitor already has a database. If the link they just
            # clicked names a different trade, that link wins — otherwise a print
            # shop coming from our email lands in whatever they browsed last.
            if wanted and domain.current()['id'] != wanted:
                seed.populate(wanted)
            return
        # Only a page or an API call is worth a new database — not every css hit.
        if path != '/' and not path.startswith('/api/'):
            return
        if _demo_count() >= DEMO_MAX_COUNT:
            _sweep_demos(force=True)
        demo_id = secrets.token_urlsafe(12)
        file = _demo_file(demo_id)
        os.makedirs(db.DEMO_DIR, exist_ok=True)
        db.use(file)
        db.init()
        seed.populate(wanted or None)
        self.new_cookies.append(
            f'pc_demo={demo_id}; Path=/; SameSite=Lax; Max-Age=86400')

    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except json.JSONDecodeError:
            return {}

    def current_user(self):
        cookie = self.headers.get('Cookie') or ''
        token = None
        for part in cookie.split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'pc_session':
                token = v
        user = db.user_by_token(token)
        if not user and DEMO_OPEN:
            # Everyone who opens the demo lands straight in the panel as admin.
            user = db.one("SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        return user

    def serve_static(self, path):
        rel = 'index.html' if path in ('/', '') else path.lstrip('/')
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, 'rb') as f:
            data = f.read()
        if full.endswith('index.html'):
            # Stamp css/js with their own mtime. Without it a browser keeps the
            # old stylesheet against new markup and the panel renders broken
            # for anyone who visited before the update.
            for asset in ('style.css', 'app.js'):
                try:
                    v = int(os.path.getmtime(os.path.join(STATIC, asset)))
                except OSError:
                    continue
                data = data.replace(f'/{asset}"'.encode(),
                                    f'/{asset}?v={v}"'.encode())
        if DEMO_OPEN and full.endswith('index.html'):
            # The visitor is signed in before the page loads, but the login card
            # is in the markup and flashes for a moment first. Somebody who came
            # from an email promising "no sign-up" sees a password box and leaves.
            data = data.replace(b'<div id="login" class="login-wrap">',
                                b'<div id="login" class="login-wrap" hidden>')
            # "Sign out" of an account they never signed into just drops them on
            # the login card with nothing to type.
            data = data.replace(b'<button class="ghost" id="logout">Sign out</button>',
                                b'<button class="ghost" id="logout" hidden>Sign out</button>')
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(os.path.splitext(full)[1],
                                                  'application/octet-stream'))
        self.send_header('Content-Length', str(len(data)))
        # The page carries a per-visitor cookie and is rewritten for the demo, so
        # a cached copy hands the next person a stale panel. The css and js have
        # no version in their names either, so caching them ships an old layout
        # against new markup. These files are a few KB — revalidating is cheap.
        self.send_header('Cache-Control', 'no-cache')
        for c in getattr(self, 'new_cookies', ()):
            self.send_header('Set-Cookie', c)
        self.end_headers()
        self.wfile.write(data)

    # --- routes -------------------------------------------------------
    def do_GET(self):
        try:
            self._get()
        finally:
            db.release()

    def do_POST(self):
        try:
            self._post()
        finally:
            db.release()

    def _get(self):
        path = urllib.parse.urlparse(self.path).path
        self.bind_demo(path)
        if not path.startswith('/api/'):
            return self.serve_static(path)

        if path == '/api/domain':
            return self.send_json({'current': domain.current(),
                                   'available': domain.available()})

        user = self.current_user()
        if path == '/api/me':
            return self.send_json({'user': _public(user)} if user else {'user': None})
        if not user:
            return self.send_json({'error': 'not authenticated'}, 401)

        if path == '/api/stats':
            return self.send_json(core.stats())
        if path == '/api/jobs':
            return self.send_json({'jobs': db.q(
                'SELECT j.*, u.name AS assignee, r.name AS resource FROM jobs j '
                'LEFT JOIN users u ON u.id = j.assignee_id '
                'LEFT JOIN resources r ON r.id = j.resource_id '
                'ORDER BY j.priority ASC, j.id DESC')})
        if path == '/api/resources':
            return self.send_json({'resources': db.q(
                'SELECT * FROM resources ORDER BY status, name')})
        if path == '/api/team':
            return self.send_json({'team': [_public(u) for u in
                                            db.q('SELECT * FROM users ORDER BY role, name')]})
        if path == '/api/activity':
            return self.send_json({'activity': db.q(
                'SELECT * FROM activity ORDER BY id DESC LIMIT 60')})
        if path == '/api/export.csv':
            return self.export_csv()
        self.send_error(404)

    def _post(self):
        path = urllib.parse.urlparse(self.path).path
        self.bind_demo(path)
        data = self.body_json()

        if path == '/api/login':
            token = db.login(data.get('login', ''), data.get('password', ''))
            if not token:
                return self.send_json({'error': 'Wrong login or password'}, 401)
            return self.send_json({'ok': True}, headers={
                'Set-Cookie': f'pc_session={token}; Path=/; HttpOnly; SameSite=Lax'})

        user = self.current_user()
        if not user:
            return self.send_json({'error': 'not authenticated'}, 401)

        if path == '/api/logout':
            db.run('DELETE FROM sessions WHERE user_id = ?', (user['id'],))
            return self.send_json({'ok': True})

        if user['role'] == 'viewer':
            return self.send_json({'error': 'Read-only account'}, 403)

        if path == '/api/jobs/create':
            title = (data.get('title') or '').strip()
            if not title:
                return self.send_json({'error': 'Title is required'}, 400)
            jid = db.run('INSERT INTO jobs (title, customer, priority, assignee_id, '
                         'created_at, updated_at) VALUES (?,?,?,?,?,?)',
                         (title, data.get('customer', ''), int(data.get('priority', 2)),
                          user['id'], db.now(), db.now()))
            db.log(f'Created: {title}', actor=user['login'], job_id=jid)
            return self.send_json({'ok': True, 'id': jid})

        if path == '/api/jobs/queue':
            ids = data.get('ids') or []
            for jid in ids:
                core.enqueue(jid, actor=user['login'])
            return self.send_json({'ok': True, 'queued': len(ids)})

        if path == '/api/jobs/finish':
            return self.send_json(core.finish(
                data.get('id'), ok=bool(data.get('ok', True)),
                error=data.get('error', ''), actor=user['login']))

        if path == '/api/dispatch':
            return self.send_json(core.dispatch(actor=user['login']))

        if path == '/api/resources/toggle':
            r = db.one('SELECT * FROM resources WHERE id = ?', (data.get('id'),))
            if not r:
                return self.send_json({'error': 'not found'}, 404)
            new = 'active' if r['status'] != 'active' else 'paused'
            db.run('UPDATE resources SET status=? WHERE id=?', (new, r['id']))
            db.log(f"{r['name']} → {new}", actor=user['login'])
            return self.send_json({'ok': True, 'status': new})

        if path == '/api/domain/switch':
            # Same panel, different business — the whole point of the core.
            cfg = domain.set_current(data.get('id') or domain.DEFAULT)
            seed.populate()
            db.log(f"Switched the demo to: {cfg['name']}", actor=user['login'])
            return self.send_json({'ok': True, 'current': cfg},
                                  headers=self.keep_signed_in(user))

        if path == '/api/reset-demo':
            seed.populate()
            db.log('Demo data reset', actor=user['login'])
            return self.send_json({'ok': True}, headers=self.keep_signed_in(user))

        self.send_error(404)

    def keep_signed_in(self, user):
        """Re-seeding wipes the users table — hand the caller a fresh cookie
        so a demo never drops you back to the login screen."""
        u = db.one('SELECT id FROM users WHERE login = ?', (user['login'],))
        if not u:
            return {}
        return {'Set-Cookie': f'pc_session={db.new_session(u["id"])}; '
                              'Path=/; HttpOnly; SameSite=Lax'}

    def export_csv(self):
        rows = db.q('SELECT j.id, j.title, j.customer, j.status, j.priority, '
                    'r.name AS resource, j.created_at FROM jobs j '
                    'LEFT JOIN resources r ON r.id = j.resource_id ORDER BY j.id')
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=['id', 'title', 'customer', 'status',
                                            'priority', 'resource', 'created_at'])
        w.writeheader()
        w.writerows(rows)
        data = buf.getvalue().encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv; charset=utf-8')
        self.send_header('Content-Disposition', 'attachment; filename="jobs.csv"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _public(u):
    return None if not u else {'id': u['id'], 'login': u['login'],
                               'name': u['name'], 'role': u['role']}


def _domain_ids():
    return {d['id'] for d in domain.available()}


def _demo_file(demo_id):
    """Reject anything that is not one of our own ids — the id reaches us from a
    cookie, so it must never be able to point outside demos/."""
    if not demo_id or len(demo_id) > 40 or not demo_id.replace('-', '').replace('_', '').isalnum():
        return None
    return os.path.join(db.DEMO_DIR, f'{demo_id}.db')


def _demo_count():
    try:
        return sum(1 for f in os.listdir(db.DEMO_DIR) if f.endswith('.db'))
    except FileNotFoundError:
        return 0


def _sweep_demos(force=False):
    """Delete demos nobody has touched for a while. `force` drops the oldest
    half instead, for when the stand is at its file limit."""
    try:
        files = [os.path.join(db.DEMO_DIR, f) for f in os.listdir(db.DEMO_DIR)
                 if f.endswith('.db')]
    except FileNotFoundError:
        return
    if force:
        files.sort(key=os.path.getmtime)
        doomed = files[:len(files) // 2]
    else:
        cutoff = time.time() - DEMO_MAX_AGE_HOURS * 3600
        doomed = [f for f in files if os.path.getmtime(f) < cutoff]
    for f in doomed:
        for path in (f, f + '-wal', f + '-shm'):
            try:
                os.remove(path)
            except OSError:
                pass


def _sweep_loop():
    while True:
        time.sleep(600)
        try:
            _sweep_demos()
        except Exception as e:  # a broken cleaner must never take the demo down
            print(f'demo sweep failed: {e}')


def main():
    if DEMO_OPEN:
        os.makedirs(db.DEMO_DIR, exist_ok=True)
        threading.Thread(target=_sweep_loop, daemon=True).start()
        print(f'PanelCore demo → http://{HOST}:{PORT}   '
              f'(a private panel per visitor, idle ones dropped after '
              f'{DEMO_MAX_AGE_HOURS}h)')
    else:
        db.init()
        if not db.q('SELECT 1 FROM users LIMIT 1'):
            seed.populate()
        db.release()
        print(f'PanelCore → http://{HOST}:{PORT}   (admin / demo)')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
