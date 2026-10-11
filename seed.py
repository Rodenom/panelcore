"""Fills the demo with plausible data for whichever domain is active."""
import random
from datetime import datetime, timedelta, timezone
import db, domain

TEAM = [
    ('admin',  'Alex Moore',     'admin'),
    ('dana',   'Dana Fisher',    'operator'),
    ('kirill', 'Kirill Rudenko', 'operator'),
    ('sam',    'Sam Whitfield',  'viewer'),
]

STATUS_MIX = (['done'] * 26 + ['running'] * 5 + ['queued'] * 8 +
              ['new'] * 12 + ['failed'] * 3)


def reset():
    for t in ('activity', 'sessions', 'jobs', 'resources', 'users'):
        db.run(f'DELETE FROM {t}')


def populate(domain_id=None):
    random.seed(7)  # same demo every time — screenshots stay consistent
    cfg = domain.set_current(domain_id) if domain_id else domain.current()
    reset()
    now = db.now()

    for login, name, role in TEAM:
        db.run('INSERT INTO users (login, name, role, pw_hash, created_at) '
               'VALUES (?,?,?,?,?)', (login, name, role, db.hash_pw('demo'), now))

    for name, status, limit, used in cfg['resources']:
        db.run('INSERT INTO resources (name, kind, status, daily_limit, used_today, '
               'created_at) VALUES (?,?,?,?,?,?)',
               (name, cfg['resource_kind'], status, limit, used, now))

    operators = db.q("SELECT id FROM users WHERE role != 'viewer'")
    res_ids = [r['id'] for r in db.q("SELECT id FROM resources WHERE status='active'")]

    base = datetime.now(timezone.utc) - timedelta(days=3)
    for i, status in enumerate(random.sample(STATUS_MIX, len(STATUS_MIX))):
        ts = (base + timedelta(minutes=i * 47)).isoformat(timespec='seconds')
        assigned = status in ('running', 'done', 'failed')
        db.run('INSERT INTO jobs (title, customer, status, priority, assignee_id, '
               'resource_id, error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
               (random.choice(cfg['titles']).format(n=i + 1),
                random.choice(cfg['customers']),
                status,
                random.choice([1, 2, 2, 2, 3]),
                random.choice(operators)['id'],
                random.choice(res_ids) if assigned else None,
                random.choice(cfg['errors']) if status == 'failed' else '',
                ts, ts))

    for j in db.q('SELECT id, title, status FROM jobs ORDER BY id DESC LIMIT 18'):
        if j['status'] == 'done':
            db.log(f"Done: {j['title']}", job_id=j['id'], actor='system')
        elif j['status'] == 'failed':
            db.log(f"Failed: {j['title']} — {cfg['errors'][0]}",
                   level='error', job_id=j['id'], actor='system')
        elif j['status'] == 'running':
            db.log(f"Started: {j['title']}", job_id=j['id'], actor='dana')
    db.log(cfg['block_note'], level='error')
    db.log('Daily counters reset', actor='system')


if __name__ == '__main__':
    db.init()
    populate()
    print('Demo data ready. Logins: admin / dana / kirill / sam — password: demo')
