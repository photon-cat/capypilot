// capypilot connect — minimal single-page UI.
const app = document.getElementById('app');
let map, trackLayer;
const state = { dongle: null, route: null };

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: 'include', ...opts });
  if (res.status === 401) { renderLogin(); throw new Error('unauthorized'); }
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function renderLogin(err = '') {
  app.innerHTML = `
    <div class="login">
      <h1>capypilot connect</h1>
      <div class="err">${err}</div>
      <input id="email" placeholder="email" value="admin@local" />
      <input id="password" type="password" placeholder="password" value="admin" />
      <button id="loginBtn">Sign in</button>
    </div>`;
  document.getElementById('loginBtn').onclick = async () => {
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;
    const res = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ email, password }),
    });
    if (res.ok) renderApp(); else renderLogin('Invalid credentials');
  };
}

function shell() {
  app.innerHTML = `
    <header>
      <h1>🦫 capypilot connect</h1><div class="sp"></div>
      <span class="meta" id="hostinfo"></span>
    </header>
    <div class="layout">
      <div class="col" id="devices"><h2>Devices</h2></div>
      <div class="col" id="routes"><h2>Routes</h2></div>
      <div class="col" id="detail"><div id="map"></div><div class="detail" id="detailBody"></div></div>
    </div>`;
}

async function renderApp() {
  shell();
  map = L.map('map').setView([37.77, -122.41], 12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '© OpenStreetMap',
  }).addTo(map);
  await loadDevices();
}

async function loadDevices() {
  const devices = await api('/api/devices');
  const el = document.getElementById('devices');
  el.innerHTML = '<h2>Devices</h2>' + (devices.length ? '' : '<div class="item sub">No devices yet</div>');
  for (const d of devices) {
    const div = document.createElement('div');
    div.className = 'item' + (state.dongle === d.dongle_id ? ' active' : '');
    div.innerHTML = `<div><span class="dot ${d.is_online ? 'online' : ''}"></span>${d.alias || d.dongle_id}</div>
      <div class="sub">${d.last_seen ? 'seen ' + new Date(d.last_seen).toLocaleString() : 'never seen'}</div>`;
    div.onclick = () => { state.dongle = d.dongle_id; loadDevices(); loadRoutes(d.dongle_id); };
    el.appendChild(div);
  }
}

async function loadRoutes(dongle) {
  const routes = await api(`/api/devices/${dongle}/routes`);
  const el = document.getElementById('routes');
  el.innerHTML = '<h2>Routes</h2>' + (routes.length ? '' : '<div class="item sub">No routes indexed</div>');
  for (const r of routes) {
    const div = document.createElement('div');
    div.className = 'item' + (state.route === r.fullname ? ' active' : '');
    const km = r.length_m ? (r.length_m / 1000).toFixed(1) + ' km' : '—';
    const when = r.start_time ? new Date(r.start_time).toLocaleString() : r.log_id;
    div.innerHTML = `<div>${when}</div><div class="sub">${r.segment_count} seg · ${km}</div>`;
    div.onclick = () => { state.route = r.fullname; loadRoutes(dongle); loadDetail(r.fullname); };
    el.appendChild(div);
  }
}

async function loadDetail(route) {
  const [meta, track] = await Promise.all([
    api(`/api/routes/${encodeURIComponent(route)}`),
    api(`/api/routes/${encodeURIComponent(route)}/track`),
  ]);
  if (trackLayer) map.removeLayer(trackLayer);
  if (track.points.length) {
    trackLayer = L.polyline(track.points, { color: '#4ea1ff', weight: 4 }).addTo(map);
    map.fitBounds(trackLayer.getBounds(), { padding: [30, 30] });
  }
  const body = document.getElementById('detailBody');
  const hasQcam = meta.segments.some(s => s.files.includes('qcamera'));
  body.innerHTML = `
    <div class="meta">${meta.fullname}<br/>${meta.version || ''} ${meta.git_commit ? '· ' + meta.git_commit.slice(0,8) : ''}</div>
    <div style="margin:8px 0">
      ${hasQcam ? `<video id="vid" controls></video>` :
        `<div class="sub">No qcamera uploaded. <button class="ghost" id="reqUpload">Request upload from device</button></div>`}
    </div>
    <div class="sub">${meta.segments.length} segments · ${track.points.length} GPS points</div>`;
  if (hasQcam) {
    const seg = meta.segments.find(s => s.files.includes('qcamera'));
    const { url } = await api(`/api/routes/${encodeURIComponent(route)}/segments/${seg.segment_num}/file/qcamera`);
    document.getElementById('vid').src = url;
  }
  const btn = document.getElementById('reqUpload');
  if (btn) btn.onclick = async () => {
    btn.disabled = true; btn.textContent = 'Requesting…';
    try {
      const res = await fetch(`/api/routes/${encodeURIComponent(route)}/request_upload`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        credentials: 'include', body: JSON.stringify({ kinds: ['rlog', 'fcamera', 'ecamera', 'qcamera'] }),
      });
      const data = await res.json();
      const a = data.athena || {};
      if (a.online === false) alert('Device is offline — it will upload when next connected to Athena.');
      else alert(`Requested ${data.requested} files. Device response: ${JSON.stringify(a.result || a.error || a)}`);
    } catch (e) {
      alert('Request failed: ' + e);
    } finally {
      btn.disabled = false; btn.textContent = 'Request upload from device';
    }
  };
}

// boot
api('/api/devices').then(renderApp).catch(() => renderLogin());
