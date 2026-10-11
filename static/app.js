const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function api(path, body) {
  const r = await fetch('/api/' + path, body ? {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  } : {});
  return {status: r.status, data: await r.json().catch(() => ({}))};
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.hidden = true, 2600);
}

/* ---------- auth ---------- */
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = e.target;
  const {status, data} = await api('login', {
    login: f.login.value, password: f.password.value});
  if (status !== 200) return $('#login-err').textContent = data.error || 'Sign-in failed';
  boot();
});

$('#logout').addEventListener('click', async () => {
  await api('logout', {});
  location.reload();
});

/* ---------- tabs ---------- */
document.querySelectorAll('nav button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('main section').forEach(s =>
    s.hidden = s.id !== 'tab-' + b.dataset.tab);
}));

/* ---------- domain ----------
   The panel below never hardcodes a business. Labels come from the active
   domain file, so the same screens read as a studio, a service company or
   a print shop. */
let DOMAIN = null;
let STATUSES = [['new', 'New'], ['queued', 'Queued'], ['running', 'Running'],
                ['done', 'Done'], ['failed', 'Failed']];

async function loadDomain() {
  const {data} = await api('domain');
  DOMAIN = data.current;
  STATUSES = DOMAIN.statuses;
  const t = DOMAIN.terms;
  document.title = `${DOMAIN.name} — ${DOMAIN.tagline}`;
  $('#login-tagline').textContent = DOMAIN.tagline;
  $('#nav-board').textContent = t.board;
  $('#nav-resources').textContent = t.resources;
  $('#res-title').textContent = t.resources;
  $('#cap-title').textContent = t.capacity;
  $('#new-title').placeholder = t.job_new;
  $('#new-customer').placeholder = t.customer;
  $('#dispatch').textContent = t.dispatch;

  const pick = $('#domain-pick');
  pick.innerHTML = data.available.map(d =>
    `<option value="${d.id}"${d.id === DOMAIN.id ? ' selected' : ''}
      >${esc(d.name)}</option>`).join('');
}

$('#domain-pick').addEventListener('change', async (e) => {
  const {status, data} = await api('domain/switch', {id: e.target.value});
  if (status !== 200) { toast(data.error || 'Switch failed'); return loadDomain(); }
  await loadDomain();
  toast(`Now running as: ${data.current.name}`);
  refresh();
});

function renderStats(s) {
  const pct = s.capacity_total ? Math.round(s.capacity_used / s.capacity_total * 100) : 0;
  const label = Object.fromEntries(STATUSES);
  $('#stat-cards').innerHTML = [
    [`${DOMAIN.terms.jobs} total`, s.jobs_total], [label.queued, s.jobs.queued],
    [label.running, s.jobs.running], [label.done, s.jobs.done],
    [label.failed, s.jobs.failed], ['Capacity used', pct + '%'],
  ].map(([l, n]) => `<div class="card"><div class="n">${n}</div>
      <div class="l">${l}</div></div>`).join('');
  $('#cap-summary').textContent =
    `${s.capacity_used} of ${s.capacity_total} slots used today`;
}

function renderCapacity(rows) {
  $('#capacity').innerHTML = rows.map(r => {
    const pct = r.daily_limit ? Math.min(100, Math.round(r.used_today / r.daily_limit * 100)) : 0;
    const cls = pct >= 100 ? 'full' : pct >= 80 ? 'hot' : '';
    return `<div class="bar-row">
      <span class="name"><span class="dot ${r.status}"></span>${esc(r.name)}</span>
      <span class="bar"><i class="${cls}" style="width:${pct}%"></i></span>
      <span class="v">${r.used_today}/${r.daily_limit || '∞'}</span></div>`;
  }).join('');
}

function renderBoard(jobs) {
  $('#board').innerHTML = STATUSES.map(([key, label]) => {
    const items = jobs.filter(j => j.status === key);
    return `<div class="col"><h3>${label}<span>${items.length}</span></h3>
      ${items.map(j => `<div class="job ${j.priority === 1 ? 'p1' : ''}">
        <b>${esc(j.title)}</b>
        <div class="meta">${esc(j.customer || '—')}
          ${j.resource ? ' · ' + esc(j.resource) : ''}</div>
        <div class="meta"><span class="pill">${esc(j.assignee || 'unassigned')}</span></div>
        ${j.error ? `<div class="e">${esc(j.error)}</div>` : ''}
      </div>`).join('') || '<div class="meta muted">Empty</div>'}</div>`;
  }).join('');
}

function renderResources(rows) {
  $('#res-table').innerHTML = `<thead><tr><th>Name</th><th>Kind</th>
    <th>Status</th><th>Used today</th><th>Daily limit</th></tr></thead><tbody>` +
    rows.map(r => `<tr data-id="${r.id}">
      <td><span class="dot ${r.status}"></span>${esc(r.name)}</td>
      <td class="muted">${esc(r.kind)}</td><td>${r.status}</td>
      <td>${r.used_today}</td><td>${r.daily_limit || '∞'}</td></tr>`).join('') +
    '</tbody>';
  $('#res-table').querySelectorAll('tr[data-id]').forEach(tr =>
    tr.addEventListener('click', async () => {
      const {status, data} = await api('resources/toggle', {id: +tr.dataset.id});
      toast(status === 200 ? 'Now ' + data.status : data.error);
      refresh();
    }));
}

function renderTeam(rows) {
  $('#team-table').innerHTML = `<thead><tr><th>Name</th><th>Login</th>
    <th>Role</th><th>Can do</th></tr></thead><tbody>` + rows.map(u => `<tr>
      <td>${esc(u.name)}</td><td class="muted">${esc(u.login)}</td>
      <td><span class="pill">${u.role}</span></td>
      <td class="muted">${u.role === 'admin' ? 'Everything, including the team'
        : u.role === 'operator' ? 'Create and run jobs' : 'Look, but not touch'}</td>
      </tr>`).join('') + '</tbody>';
}

function renderFeed(rows) {
  $('#feed').innerHTML = rows.map(a => `<li>
    <time>${a.created_at.slice(5, 16).replace('T', ' ')}</time>
    <span class="${a.level}">${esc(a.message)}</span>
    <span class="spacer"></span><span class="pill">${esc(a.actor)}</span></li>`).join('');
}

/* ---------- actions ---------- */
$('#add-job').addEventListener('click', async () => {
  const title = $('#new-title').value.trim();
  if (!title) return toast(`Give the ${DOMAIN.terms.job} a title`);
  const {status, data} = await api('jobs/create',
    {title, customer: $('#new-customer').value.trim()});
  if (status !== 200) return toast(data.error);
  $('#new-title').value = $('#new-customer').value = '';
  toast(`${DOMAIN.terms.job[0].toUpperCase() + DOMAIN.terms.job.slice(1)} created`);
  refresh();
});

$('#dispatch').addEventListener('click', async () => {
  const jobs = (await api('jobs')).data.jobs.filter(j => j.status === 'new');
  if (jobs.length) await api('jobs/queue', {ids: jobs.map(j => j.id)});
  const {status, data} = await api('dispatch', {});
  if (status !== 200) return toast(data.error);
  toast(`Assigned ${data.assigned}` +
    (data.skipped ? `, ${data.skipped} waiting for free capacity` : ''));
  refresh();
});

/* ---------- boot ---------- */
async function refresh() {
  const [stats, jobs, res, team, act] = await Promise.all(
    ['stats', 'jobs', 'resources', 'team', 'activity'].map(p => api(p)));
  renderStats(stats.data);
  renderCapacity(res.data.resources);
  renderBoard(jobs.data.jobs);
  renderResources(res.data.resources);
  renderTeam(team.data.team);
  renderFeed(act.data.activity);
}

async function boot() {
  const {data} = await api('me');
  await loadDomain();
  if (!data.user) { $('#login').hidden = false; $('#app').hidden = true; return; }
  $('#login').hidden = true; $('#app').hidden = false;
  $('#who-name').textContent = data.user.name;
  $('#who-role').textContent = data.user.role;
  await refresh();
  clearInterval(boot._t);
  boot._t = setInterval(refresh, 15000);
}

boot();
