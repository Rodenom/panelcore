# PanelCore

A small, dependency-free core for internal operations panels. Jobs move across a board,
resources have a daily capacity, and a dispatcher assigns work without overloading a
resource or letting one dead resource take a whole batch down with it.

Python standard library and SQLite. No `pip install`, no framework.

## Why it exists

Every operations panel I have built — an upload panel for a media team, a dispatch console
for a logistics office — needed the same 80%: roles, a registry of resources with limits,
a queue, a status board, an activity log, CSV export. PanelCore is that 80%, written once.

The other 20% is vocabulary: what a "job" and a "resource" are called in a given business.
That lives in one JSON file per business under `domains/`, so a new vertical is a data file,
not a fork. The same screens read as a content studio, a field-service company, a moving
company or a print shop.

## Run it

```bash
python3 server.py
```

Open http://127.0.0.1:8900 and sign in as `admin` / `demo`. Other demo accounts: `dana`
(operator) and `sam` (read-only). Demo data is seeded on first start.

`DEMO_OPEN=1 python3 server.py` runs it as a public stand: every visitor gets a private
database seeded for their own trade (`?b=print`), and idle ones are swept after a few hours.

## The dispatch rules

All of them live in `core.py` and are covered by `test_core.py`:

- A resource is never pushed past its daily capacity.
- Work goes to the active resource with the most headroom, so the first one is not hammered.
- One failure is noise. Three in a row park the resource, and the rest of the batch moves on.
- Only new or failed jobs can be queued.

```bash
python3 -m unittest -v
```

## Layout

| File | What it does |
|---|---|
| `core.py` | Queue, dispatch, finish, stats |
| `db.py` | SQLite schema, per-request connections, sessions, activity log |
| `domain.py` | Loads the active business vocabulary from `domains/*.json` |
| `seed.py` | Deterministic demo data for whichever domain is active |
| `server.py` | HTTP server, routes, roles, CSV export, per-visitor demo databases |
| `static/` | The panel UI: plain HTML, CSS and JavaScript |

## Design notes

- **Standard library only.** The panel is meant to run on a client's machine, where an
  install step is the first thing that breaks.
- **Roles are enforced on the server.** A read-only account gets a 403 from the API, not a
  hidden button.
- **The demo id comes from a cookie, so it is validated** before it is turned into a file
  path and can never point outside `demos/`.
- **Stylesheet and script are stamped with their modification time**, so a returning browser
  never renders old CSS against new markup.

## Known limits

Passwords are stored as salted SHA-256, which is fine for a demo and not for production —
swap in `hashlib.scrypt` before real users. There is no job runner here: `dispatch` assigns
work, and whatever executes it reports back through `finish`.
