"""Dispatch rules, checked against a throwaway database.

    python3 -m unittest -v
"""
import os, tempfile, unittest
import db, core


class DispatchRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.use(os.path.join(self.tmp.name, 'test.db'))
        db.init()

    def tearDown(self):
        db.release()
        self.tmp.cleanup()

    def resource(self, name, limit=0, used=0, status='active'):
        return db.run('INSERT INTO resources (name, status, daily_limit, used_today, created_at) '
                      'VALUES (?,?,?,?,?)', (name, status, limit, used, db.now()))

    def job(self, title, status='queued', resource_id=None):
        return db.run('INSERT INTO jobs (title, status, resource_id, created_at, updated_at) '
                      'VALUES (?,?,?,?,?)', (title, status, resource_id, db.now(), db.now()))

    def test_capacity_is_never_exceeded(self):
        self.resource('only one', limit=2, used=0)
        for i in range(5):
            self.job(f'job {i}')
        report = core.dispatch()
        self.assertEqual(report['assigned'], 2)
        self.assertEqual(report['skipped'], 3)
        self.assertEqual(db.one('SELECT used_today FROM resources')['used_today'], 2)

    def test_load_goes_to_the_resource_with_most_headroom(self):
        busy = self.resource('busy', limit=10, used=9)
        idle = self.resource('idle', limit=10, used=1)
        self.assertEqual(core.pick_resource()['id'], idle)
        self.assertNotEqual(core.pick_resource()['id'], busy)

    def test_paused_and_blocked_resources_get_no_work(self):
        self.resource('paused', limit=10, status='paused')
        self.resource('blocked', limit=10, status='blocked')
        self.job('nowhere to go')
        self.assertEqual(core.dispatch()['assigned'], 0)

    def test_three_failures_in_a_row_park_the_resource(self):
        rid = self.resource('flaky', limit=100)
        ids = [self.job(f'job {i}', status='running', resource_id=rid) for i in range(3)]
        for jid in ids[:2]:
            core.finish(jid, ok=False, error='boom')
        self.assertEqual(db.one('SELECT status FROM resources')['status'], 'active')
        core.finish(ids[2], ok=False, error='boom')
        self.assertEqual(db.one('SELECT status FROM resources')['status'], 'blocked')

    def test_one_failure_is_noise(self):
        rid = self.resource('mostly fine', limit=100)
        ok1 = self.job('a', status='running', resource_id=rid)
        bad = self.job('b', status='running', resource_id=rid)
        core.finish(ok1, ok=True)
        core.finish(bad, ok=False, error='boom')
        self.assertEqual(db.one('SELECT status FROM resources')['status'], 'active')

    def test_only_new_or_failed_jobs_can_be_queued(self):
        done = self.job('finished', status='done')
        self.assertFalse(core.enqueue(done)['ok'])
        fresh = self.job('fresh', status='new')
        self.assertTrue(core.enqueue(fresh)['ok'])


if __name__ == '__main__':
    unittest.main()
