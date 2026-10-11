"""The 20% of a panel that is not reusable: what things are called in this business.

Core code below this file never mentions videos, crews or presses — it moves
"jobs" across "resources". A domain file supplies the vocabulary, the board
columns and the demo data, so a new vertical is one JSON file, not a fork.
"""
import json, os

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'domains')
DEFAULT = 'content'
_cache = {}


def available():
    ids = sorted(f[:-5] for f in os.listdir(DIR) if f.endswith('.json'))
    return [{'id': i, 'name': load(i)['name'], 'tagline': load(i)['tagline']}
            for i in ids]


def load(domain_id):
    if domain_id not in _cache:
        path = os.path.join(DIR, f'{domain_id}.json')
        if not os.path.isfile(path):
            path = os.path.join(DIR, f'{DEFAULT}.json')
        with open(path, encoding='utf-8') as f:
            _cache[domain_id] = json.load(f)
    return _cache[domain_id]


def current():
    import db
    return load(db.get_setting('domain', DEFAULT))


def set_current(domain_id):
    import db
    cfg = load(domain_id)
    db.set_setting('domain', cfg['id'])
    return cfg
