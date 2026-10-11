"""Queue and dispatch — the part every ops panel needs and nobody wants to write twice.

Rules that hold for uploading videos, dispatching trucks or sending invoices alike:
  * a resource has a daily capacity and must not be pushed past it
  * work spreads across resources instead of hammering the first one
  * a dead resource takes itself out of rotation, it does not poison the batch
"""
import json
import db


def pick_resource(kind=None):
    """Least-loaded active resource with capacity left. None when everything is full."""
    sql = ("SELECT * FROM resources WHERE status = 'active' "
           "AND (daily_limit = 0 OR used_today < daily_limit)")
    args = ()
    if kind:
        sql += ' AND kind = ?'
        args = (kind,)
    # Spread the load: prefer whoever has the most headroom left.
    sql += ' ORDER BY (CASE WHEN daily_limit = 0 THEN 999999 ELSE daily_limit - used_today END) DESC'
    return db.one(sql, args)


def enqueue(job_id, actor='system'):
    job = db.one('SELECT * FROM jobs WHERE id = ?', (job_id,))
    if not job:
        return {'ok': False, 'error': 'job not found'}
    if job['status'] not in ('new', 'failed'):
        return {'ok': False, 'error': f"job is already {job['status']}"}
    db.run("UPDATE jobs SET status='queued', error='', updated_at=? WHERE id=?",
           (db.now(), job_id))
    db.log(f"Queued: {job['title']}", actor=actor, job_id=job_id)
    return {'ok': True}


def dispatch(limit=25, actor='system'):
    """Assign queued jobs to resources. Returns a per-job report."""
    queued = db.q("SELECT * FROM jobs WHERE status='queued' "
                  "ORDER BY priority ASC, id ASC LIMIT ?", (limit,))
    report = {'assigned': 0, 'skipped': 0, 'details': []}

    for job in queued:
        res = pick_resource()
        if not res:
            report['skipped'] += 1
            report['details'].append({'job': job['title'], 'result': 'no capacity left'})
            db.log(f"No capacity for: {job['title']}", level='warn',
                   actor=actor, job_id=job['id'])
            continue

        db.run("UPDATE jobs SET status='running', resource_id=?, updated_at=? WHERE id=?",
               (res['id'], db.now(), job['id']))
        db.run("UPDATE resources SET used_today = used_today + 1 WHERE id = ?", (res['id'],))
        report['assigned'] += 1
        report['details'].append({'job': job['title'], 'result': f"→ {res['name']}"})
        db.log(f"{job['title']} → {res['name']}", actor=actor, job_id=job['id'])

    return report


def finish(job_id, ok=True, error='', actor='system'):
    job = db.one('SELECT * FROM jobs WHERE id = ?', (job_id,))
    if not job:
        return {'ok': False, 'error': 'job not found'}
    status = 'done' if ok else 'failed'
    db.run('UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?',
           (status, error, db.now(), job_id))

    if not ok and job['resource_id']:
        # One failure is noise. A run of them means the resource itself is down —
        # park it so the rest of the batch does not die with it.
        recent = db.q("SELECT status FROM jobs WHERE resource_id=? "
                      "ORDER BY updated_at DESC LIMIT 3", (job['resource_id'],))
        if len(recent) == 3 and all(r['status'] == 'failed' for r in recent):
            db.run("UPDATE resources SET status='blocked' WHERE id=?", (job['resource_id'],))
            res = db.one('SELECT name FROM resources WHERE id=?', (job['resource_id'],))
            db.log(f"Paused {res['name']} after 3 failures in a row",
                   level='error', actor=actor)

    db.log(f"{'Done' if ok else 'Failed'}: {job['title']}" + (f' — {error}' if error else ''),
           level='info' if ok else 'error', actor=actor, job_id=job_id)
    return {'ok': True, 'status': status}


def stats():
    counts = {r['status']: r['n'] for r in
              db.q('SELECT status, COUNT(*) n FROM jobs GROUP BY status')}
    res = db.q('SELECT status, COUNT(*) n FROM resources GROUP BY status')
    cap = db.one('SELECT COALESCE(SUM(daily_limit),0) lim, COALESCE(SUM(used_today),0) used '
                 "FROM resources WHERE status='active'")
    return {
        'jobs': {k: counts.get(k, 0) for k in
                 ('new', 'queued', 'running', 'done', 'failed')},
        'jobs_total': sum(counts.values()),
        'resources': {r['status']: r['n'] for r in res},
        'capacity_used': cap['used'],
        'capacity_total': cap['lim'],
    }
