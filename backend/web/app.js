const state = {
  token: localStorage.getItem('dashboard_token') || '',
  user: JSON.parse(localStorage.getItem('dashboard_user') || 'null'),
  selectedPostId: null,
  selectedSourceId: null,
  sources: [],
  sourceDetails: new Map(),
};

const els = {
  username: document.getElementById('username'),
  password: document.getElementById('password'),
  loginBtn: document.getElementById('loginBtn'),
  registerBtn: document.getElementById('registerBtn'),
  authStatus: document.getElementById('authStatus'),
  sourceType: document.getElementById('sourceType'),
  sourceUrl: document.getElementById('sourceUrl'),
  includeComments: document.getElementById('includeComments'),
  includeReplies: document.getElementById('includeReplies'),
  maxDaysOld: document.getElementById('maxDaysOld'),
  createSourceBtn: document.getElementById('createSourceBtn'),
  summaryStats: document.getElementById('summaryStats'),
  sourcesList: document.getElementById('sourcesList'),
  trendingList: document.getElementById('trendingList'),
  sourceDetail: document.getElementById('sourceDetail'),
  postsList: document.getElementById('postsList'),
  postDetail: document.getElementById('postDetail'),
  postMetricsHistory: document.getElementById('postMetricsHistory'),
  postComments: document.getElementById('postComments'),
  growthList: document.getElementById('growthList'),
  taskLogs: document.getElementById('taskLogs'),
  scraperLogs: document.getElementById('scraperLogs'),
  postSourceFilter: document.getElementById('postSourceFilter'),
  activeOnlyToggle: document.getElementById('activeOnlyToggle'),
};

function setAuthStatus(text, isError = false) {
  els.authStatus.textContent = text;
  els.authStatus.style.color = isError ? 'var(--danger)' : 'var(--muted)';
}

async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const payload = await res.json();
      detail = payload.detail || payload.error || JSON.stringify(payload);
    } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function saveAuth(token, user) {
  state.token = token;
  state.user = user;
  localStorage.setItem('dashboard_token', token);
  localStorage.setItem('dashboard_user', JSON.stringify(user));
  setAuthStatus(`Da dang nhap voi tai khoan ${user.username}${user.is_admin ? ' (admin)' : ''}`);
}

function metricBox(label, value) {
  return `<div class="stat-box"><span class="muted">${label}</span><strong>${value}</strong></div>`;
}

function formatDate(value) {
  if (!value) return 'khong co';
  return new Date(value).toLocaleString();
}

function trimText(value, max = 160) {
  if (!value) return '(khong co noi dung)';
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

async function login(mode) {
  try {
    const payload = {
      username: els.username.value.trim(),
      password: els.password.value,
    };
    if (!payload.username || !payload.password) throw new Error('Can nhap ten dang nhap va mat khau');

    const route = mode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const result = await api(route, { method: 'POST', body: JSON.stringify(mode === 'register' ? { ...payload, email: `${payload.username}@local.dev` } : payload) });
    saveAuth(result.access_token, result.user);
    await bootstrap();
  } catch (error) {
    setAuthStatus(error.message, true);
  }
}

async function createSource() {
  try {
    const payload = {
      source_type: els.sourceType.value,
      facebook_url: els.sourceUrl.value.trim(),
      include_comments: els.includeComments.checked,
      include_replies: els.includeReplies.checked,
      max_days_old: Number(els.maxDaysOld.value || 30),
      check_access: true,
    };
    await api('/api/sources/', { method: 'POST', body: JSON.stringify(payload) });
    els.sourceUrl.value = '';
    await loadSources();
    await loadSummary();
  } catch (error) {
    alert(`Tao nguon that bai: ${error.message}`);
  }
}

async function triggerAdminTask(task, sourceId = null) {
  try {
    const query = sourceId ? `?source_id=${sourceId}` : '';
    const result = await api(`/api/admin/tasks/${task}${query}`, { method: 'POST' });
    await loadTaskLogs();
    return result;
  } catch (error) {
    alert(`Chay tac vu that bai: ${error.message}`);
    throw error;
  }
}

async function loadSummary() {
  const summary = await api('/api/analytics/summary');
  els.summaryStats.innerHTML = [
    metricBox('Nguon', summary.total_sources),
    metricBox('Bai viet', summary.total_posts),
    metricBox('Tuong tac', summary.total_engagement),
    metricBox('Luot thich', summary.total_likes),
    metricBox('Chia se', summary.total_shares),
    metricBox('Binh luan', summary.total_comments),
  ].join('');
}

async function loadSources() {
  const activeOnly = els.activeOnlyToggle.checked;
  const sources = await api(`/api/sources/?limit=100${activeOnly ? '&active_only=true' : ''}`);
  state.sources = sources;
  els.sourcesList.innerHTML = '';
  els.postSourceFilter.innerHTML = '<option value="">Tat ca nguon</option>';

  for (const source of sources) {
    const option = document.createElement('option');
    option.value = source.id;
    option.textContent = source.source_name || `${source.source_type}:${source.facebook_id}`;
    els.postSourceFilter.appendChild(option);

    const node = document.getElementById('sourceCardTemplate').content.firstElementChild.cloneNode(true);
    node.querySelector('.title').textContent = source.source_name || source.facebook_id;
    node.querySelector('.meta').textContent = `${source.source_type} · ${source.facebook_id}`;
    node.querySelector('.submeta').textContent = `Lan quet gan nhat: ${formatDate(source.last_scraped)} · Truy cap duoc: ${source.is_accessible}`;
    node.querySelector('.badge').textContent = source.permission_status || 'khong ro';
    node.onclick = async (event) => {
      if (event.target.closest('button')) return;
      state.selectedSourceId = source.id;
      await loadSourceDetail();
    };

    const actions = node.querySelector('.actions');
    const refreshBtn = document.createElement('button');
    refreshBtn.textContent = 'Xep hang quet';
    refreshBtn.className = 'secondary';
    refreshBtn.onclick = async () => {
      await api(`/api/sources/${source.id}/refresh`, { method: 'POST' });
      await loadSources();
    };
    actions.appendChild(refreshBtn);

    if (state.user?.is_admin) {
      const scrapeBtn = document.createElement('button');
      scrapeBtn.textContent = 'Chay quet ngay';
      scrapeBtn.onclick = async () => { await triggerAdminTask('scrape_posts', source.id); await loadSources(); await loadPosts(); };
      actions.appendChild(scrapeBtn);

      const metricsBtn = document.createElement('button');
      metricsBtn.textContent = 'Cap nhat chi so';
      metricsBtn.className = 'secondary';
      metricsBtn.onclick = async () => { await triggerAdminTask('update_metrics', source.id); await loadPosts(); };
      actions.appendChild(metricsBtn);
    }

    els.sourcesList.appendChild(node);
  }
}

async function loadTrending() {
  const payload = await api('/api/analytics/trending?limit=8');
  els.trendingList.innerHTML = payload.trending_posts.map((item) => `
    <article class="card">
      <h3>${item.facebook_post_id}</h3>
      <p class="meta">Toc do tuong tac ${item.engagement_velocity.toFixed(2)} / gio</p>
      <p class="submeta">Thich ${item.likes} · Chia se ${item.shares} · Binh luan ${item.comments}</p>
    </article>
  `).join('') || '<p class="muted">Chua co bai viet noi bat.</p>';
}

async function loadGrowth() {
  const payload = await api('/api/analytics/growth');
  els.growthList.innerHTML = payload.growth_data.map((item) => `
    <article class="card">
      <h3>${item.source_name || item.source_id}</h3>
      <p class="meta">Ty le tang truong ${item.growth_rate == null ? 'khong co' : `${item.growth_rate.toFixed(2)}%`}</p>
      <p class="submeta">Thich ${item.likes_growth} · Chia se ${item.shares_growth} · Binh luan ${item.comments_growth}</p>
    </article>
  `).join('') || '<p class="muted">Chua co du lieu tang truong.</p>';
}

async function loadSourceDetail() {
  if (!state.selectedSourceId) return;
  const source = await api(`/api/sources/${state.selectedSourceId}`);
  const analytics = await api(`/api/analytics/source/${state.selectedSourceId}`);
  state.sourceDetails.set(source.id, source);

  const daily = analytics.daily_analytics || [];
  const latestDaily = daily.length ? daily[daily.length - 1] : null;
  els.sourceDetail.innerHTML = `
    <h3>${source.source_name || source.facebook_id}</h3>
    <p class="meta">${source.source_type} · ${source.facebook_url}</p>
    <p class="submeta">Trang thai quyen: ${source.permission_status || 'khong ro'} · Truy cap duoc: ${source.is_accessible} · Lan kiem tra: ${formatDate(source.permission_checked_at)}</p>
    <div class="detail-grid">
      ${metricBox('Bai viet', source.post_count || analytics.statistics.posts_count || 0)}
      ${metricBox('Luot thich', analytics.statistics.total_likes || 0)}
      ${metricBox('Chia se', analytics.statistics.total_shares || 0)}
      ${metricBox('Binh luan', analytics.statistics.total_comments || 0)}
      ${metricBox('TB luot thich', (analytics.statistics.avg_likes || 0).toFixed(1))}
      ${metricBox('So moc hang ngay', analytics.daily_analytics_count || 0)}
    </div>
    <div class="list compact" style="margin-top: 12px;">
      ${daily.slice(-7).reverse().map((entry) => `
        <article class="card">
          <h3>${formatDate(entry.date)}</h3>
          <p class="submeta">Bai viet ${entry.total_posts} · Thich ${entry.total_likes} · Chia se ${entry.total_shares} · Binh luan ${entry.total_comments}</p>
          <p class="submeta">Tang truong ${entry.growth_rate == null ? 'khong co' : `${entry.growth_rate.toFixed(2)}%`} · TB ER ${entry.avg_engagement_rate == null ? 'khong co' : `${entry.avg_engagement_rate.toFixed(2)}%`}</p>
        </article>
      `).join('') || `<p class="muted">Chua co analytics cache${latestDaily ? '' : '. Hay chay tac vu analytics de nap du lieu cho khu vuc nay.'}</p>`}
    </div>
  `;
}

async function loadPosts() {
  const sourceId = els.postSourceFilter.value;
  const query = sourceId ? `?source_id=${sourceId}&limit=50` : '?limit=50';
  const posts = await api(`/api/posts/${query}`.replace('/?', '?'));
  els.postsList.innerHTML = '';

  for (const post of posts) {
    const node = document.getElementById('postCardTemplate').content.firstElementChild.cloneNode(true);
    node.querySelector('.title').textContent = trimText(post.content, 90);
    node.querySelector('.meta').textContent = `Bai viet ${post.facebook_post_id} · ${formatDate(post.posted_at)}`;
    node.querySelector('.metrics').innerHTML = [
      metricBox('Thich', post.current_likes),
      metricBox('Chia se', post.current_shares),
      metricBox('Binh luan', post.current_comments),
      metricBox('So lan cap nhat', post.metrics_update_count),
    ].join('');

    const actions = node.querySelector('.actions');
    const inspectBtn = document.createElement('button');
    inspectBtn.textContent = 'Xem chi tiet';
    inspectBtn.onclick = async () => {
      state.selectedPostId = post.id;
      await loadPostDetail();
    };
    actions.appendChild(inspectBtn);

    els.postsList.appendChild(node);
  }
}

async function loadPostDetail() {
  if (!state.selectedPostId) return;
  const detail = await api(`/api/posts/${state.selectedPostId}`);
  const analytics = await api(`/api/analytics/posts/${state.selectedPostId}`);
  const comments = await api(`/api/posts/${state.selectedPostId}/comments?limit=50`);

  els.postDetail.innerHTML = `
    <h3>${trimText(detail.content, 220)}</h3>
    <p class="meta">${detail.facebook_post_id} · ${formatDate(detail.posted_at)}</p>
    <div class="metrics">
      ${metricBox('Thich hien tai', detail.current_likes)}
      ${metricBox('Chia se hien tai', detail.current_shares)}
      ${metricBox('Binh luan hien tai', detail.current_comments)}
      ${metricBox('Tang thich', analytics.growth.likes_growth)}
      ${metricBox('Tang chia se', analytics.growth.shares_growth)}
      ${metricBox('Tang binh luan', analytics.growth.comments_growth)}
    </div>
  `;

  els.postMetricsHistory.innerHTML = (detail.metrics_history || []).slice().reverse().map((entry) => `
    <article class="card">
      <h3>${formatDate(entry.recorded_at)}</h3>
      <p class="submeta">Thich ${entry.likes_count} · Chia se ${entry.shares_count} · Binh luan ${entry.comments_count}${entry.views_count == null ? '' : ` · Luot xem ${entry.views_count}`}</p>
    </article>
  `).join('') || '<p class="muted">Chua co lich su metrics.</p>';

  els.postComments.innerHTML = comments.comments.map((comment) => `
    <article class="card">
      <h3>${comment.commenter_name || 'Khong ro'}</h3>
      <p>${trimText(comment.comment_text, 180)}</p>
      <p class="submeta">Thich ${comment.likes_count} · Tra loi ${comment.reply_count} · Cap ${comment.depth_level}</p>
    </article>
  `).join('') || '<p class="muted">Chua co binh luan.</p>';
}

async function loadTaskLogs() {
  if (!state.user?.is_admin) {
    els.taskLogs.innerHTML = '<p class="muted">Can quyen admin.</p>';
    return;
  }
  const payload = await api('/api/admin/task-logs?limit=20');
  els.taskLogs.innerHTML = payload.logs.map((log) => `
    <article class="card">
      <h3>${log.task_name}</h3>
      <p class="meta">${log.status} · ${formatDate(log.started_at)}</p>
      <p class="submeta">Xu ly ${log.items_processed} · Loi ${log.errors_count} · Thoi gian ${log.duration_seconds || 0}s</p>
    </article>
  `).join('') || '<p class="muted">Chua co nhat ky tac vu.</p>';
}

async function loadScraperLogs() {
  if (!state.user?.is_admin) {
    els.scraperLogs.innerHTML = '<p class="muted">Can quyen admin.</p>';
    return;
  }
  const payload = await api('/api/admin/logs?limit=20');
  els.scraperLogs.innerHTML = payload.logs.map((log) => `
    <article class="card">
      <h3>${log.level}</h3>
      <p>${trimText(log.message, 180)}</p>
      <p class="submeta">Nguon ${log.source_id || 'khong co'} · ${formatDate(log.timestamp)}</p>
    </article>
  `).join('') || '<p class="muted">Chua co nhat ky scraper.</p>';
}

async function bootstrap() {
  if (!state.token) {
    setAuthStatus('Can dang nhap');
    return;
  }
  try {
    await loadSummary();
    await loadSources();
    await loadTrending();
    await loadGrowth();
    await loadPosts();
    await loadTaskLogs();
    await loadScraperLogs();
    if (state.selectedSourceId) await loadSourceDetail();
    if (state.selectedPostId) await loadPostDetail();
  } catch (error) {
    setAuthStatus(error.message, true);
  }
}

els.loginBtn.onclick = () => login('login');
els.registerBtn.onclick = () => login('register');
els.createSourceBtn.onclick = createSource;
els.reloadSourcesBtn.onclick = loadSources;
els.reloadSourceDetailBtn.onclick = loadSourceDetail;
els.reloadTrendingBtn.onclick = loadTrending;
els.reloadGrowthBtn.onclick = loadGrowth;
els.reloadPostsBtn.onclick = loadPosts;
els.reloadPostDetailBtn.onclick = loadPostDetail;
els.reloadTaskLogsBtn.onclick = loadTaskLogs;
els.reloadScraperLogsBtn.onclick = loadScraperLogs;
els.postSourceFilter.onchange = loadPosts;
els.activeOnlyToggle.onchange = loadSources;

document.querySelectorAll('.task-btn').forEach((btn) => {
  btn.onclick = async () => {
    await triggerAdminTask(btn.dataset.task);
    await loadTaskLogs();
    await loadScraperLogs();
  };
});

if (state.user) {
  setAuthStatus(`Da dang nhap voi tai khoan ${state.user.username}${state.user.is_admin ? ' (admin)' : ''}`);
}

bootstrap();
