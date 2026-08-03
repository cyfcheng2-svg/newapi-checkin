const state = {
  config: {
    accounts: [],
    dingtalk: {
      webhook: '',
      secret: '',
      notify_after_checkin: false,
    },
    schedule: {
      enabled: false,
      time: '08:10',
      run_missed: false,
    },
  },
  exports: {
    json: '[]',
    simple: '',
  },
  scheduler: {},
  running: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function uid() {
  return `account-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function emptyAccount() {
  return {
    id: uid(),
    name: `账号 ${state.config.accounts.length + 1}`,
    url: '',
    session: '',
    user_id: '',
    cf_clearance: '',
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || `请求失败 (${response.status})`);
  }
  return data;
}

function setStatus(message, type = 'info') {
  const el = $('#status');
  el.textContent = message || '';
  el.className = `status ${type} ${message ? 'is-visible' : ''}`;
}

function setBusy(isBusy) {
  state.running = isBusy;
  $('#save-btn').disabled = isBusy;
  $('#run-btn').disabled = isBusy;
  $('#run-btn').textContent = isBusy ? '运行中...' : '立即签到';
}

function syncFormToState() {
  state.config.dingtalk.webhook = $('#dingtalk-webhook').value.trim();
  state.config.dingtalk.secret = $('#dingtalk-secret').value.trim();
  state.config.dingtalk.notify_after_checkin = $('#notify-after-checkin').checked;
  state.config.schedule.enabled = $('#schedule-enabled').checked;
  state.config.schedule.time = $('#schedule-time').value || '08:10';
  state.config.schedule.run_missed = $('#schedule-run-missed').checked;
  updateExports();
}

function normalizeUrl(url) {
  const value = url.trim();
  if (!value) return '';
  if (value.startsWith('http://') || value.startsWith('https://')) return value;
  return `https://${value}`;
}

function exportableAccounts() {
  return state.config.accounts
    .filter((account) => account.url || account.session || account.name)
    .map((account) => {
      const item = {
        url: normalizeUrl(account.url),
        session: account.session.trim(),
        name: account.name.trim(),
      };
      if (account.user_id.trim()) item.user_id = account.user_id.trim();
      if (account.cf_clearance.trim()) item.cf_clearance = account.cf_clearance.trim();
      return item;
    });
}

function updateExports() {
  const accounts = exportableAccounts();
  state.exports.json = JSON.stringify(accounts, null, 2);
  state.exports.simple = accounts
    .filter((account) => account.url && account.session)
    .map((account) => `${account.url}#${account.session}`)
    .join(',');
  $('#export-json').value = state.exports.json;
  $('#export-simple').value = state.exports.simple;
  $('#account-count').textContent = `${state.config.accounts.length} 个账号`;
}

function renderAccounts() {
  const list = $('#accounts-list');
  const template = $('#account-template');
  list.innerHTML = '';

  if (!state.config.accounts.length) {
    const empty = document.createElement('div');
    empty.className = 'account-card';
    empty.innerHTML = '<strong>还没有账号</strong><div class="account-state">点击“添加账号”开始配置。</div>';
    list.appendChild(empty);
    updateExports();
    return;
  }

  state.config.accounts.forEach((account, index) => {
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.id = account.id;
    $('.account-title', node).textContent = account.name || `账号 ${index + 1}`;
    $('.account-subtitle', node).textContent = account.url || '未填写站点 URL';
    $('.field-name', node).value = account.name || '';
    $('.field-url', node).value = account.url || '';
    $('.field-session', node).value = account.session || '';
    $('.field-user-id', node).value = account.user_id || '';
    $('.field-cf', node).value = account.cf_clearance || '';
    list.appendChild(node);
  });

  updateExports();
}

function renderDingtalk() {
  $('#dingtalk-webhook').value = state.config.dingtalk.webhook || '';
  $('#dingtalk-secret').value = state.config.dingtalk.secret || '';
  $('#notify-after-checkin').checked = Boolean(state.config.dingtalk.notify_after_checkin);
}

function renderSchedule() {
  const schedule = state.config.schedule || {};
  $('#schedule-enabled').checked = Boolean(schedule.enabled);
  $('#schedule-time').value = schedule.time || '08:10';
  $('#schedule-run-missed').checked = Boolean(schedule.run_missed);
  renderSchedulerStatus(state.scheduler);
}

function renderSchedulerStatus(status = {}) {
  const enabled = Boolean(status.enabled ?? state.config.schedule?.enabled);
  const time = status.time || state.config.schedule?.time || '08:10';
  $('#schedule-summary').textContent = enabled ? `已开启，每天 ${time}` : '未开启';
  $('#schedule-next-run').textContent = enabled ? (status.next_run_at || '-') : '-';
  $('#schedule-last-run').textContent = status.last_run_at || '-';
  $('#schedule-last-message').textContent = status.running ? '正在运行' : (status.last_message || '-');
}

function renderAll() {
  renderAccounts();
  renderDingtalk();
  renderSchedule();
  updateExports();
}

function accountByCard(card) {
  return state.config.accounts.find((account) => account.id === card.dataset.id);
}

function updateCardFromInput(input) {
  const card = input.closest('.account-card');
  const account = accountByCard(card);
  if (!account) return;

  if (input.classList.contains('field-name')) account.name = input.value;
  if (input.classList.contains('field-url')) account.url = input.value;
  if (input.classList.contains('field-session')) account.session = input.value;
  if (input.classList.contains('field-user-id')) account.user_id = input.value;
  if (input.classList.contains('field-cf')) account.cf_clearance = input.value;

  $('.account-title', card).textContent = account.name || '未命名账号';
  $('.account-subtitle', card).textContent = account.url || '未填写站点 URL';
  updateExports();
}

function currentPayload() {
  syncFormToState();
  return {
    accounts: exportableAccounts(),
    dingtalk: { ...state.config.dingtalk },
    schedule: { ...state.config.schedule },
  };
}

async function loadConfig() {
  const data = await api('/api/config');
  state.config = data.config;
  state.exports = data.exports;
  state.scheduler = data.scheduler || {};
  $('#config-path').textContent = data.config_path || 'local_config.json';
  $('#source-label').textContent = data.source ? `来源: ${data.source}` : '来源: 本地配置';
  if (!state.config.accounts.length) {
    state.config.accounts.push(emptyAccount());
  }
  renderAll();
  setStatus('本地界面已启动', 'success');
}

async function saveConfig() {
  setBusy(true);
  try {
    const data = await api('/api/config', {
      method: 'POST',
      body: JSON.stringify({ config: currentPayload() }),
    });
    state.config = data.config;
    state.exports = data.exports;
    state.scheduler = data.scheduler || {};
    renderAll();
    setStatus(data.message, 'success');
  } catch (error) {
    setStatus(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function runCheckin() {
  setBusy(true);
  setStatus('正在执行签到...', 'info');
  try {
    const payload = currentPayload();
    const data = await api('/api/checkin', {
      method: 'POST',
      body: JSON.stringify({
        config: payload,
        save_before_run: true,
        notify: Boolean(payload.dingtalk.notify_after_checkin),
      }),
    });
    renderResults(data);
    activateTab('results');
    setStatus(data.notification_message || data.message, data.success ? 'success' : 'error');
  } catch (error) {
    setStatus(error.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function refreshSchedulerStatus(silent = false) {
  try {
    const data = await api('/api/scheduler/status');
    state.scheduler = data;
    renderSchedulerStatus(data);
    if (!silent) setStatus('定时状态已刷新', 'success');
  } catch (error) {
    if (!silent) setStatus(error.message, 'error');
  }
}

async function testAccount(card) {
  const account = accountByCard(card);
  const stateEl = $('.account-state', card);
  stateEl.className = 'account-state';
  stateEl.textContent = '正在测试...';
  try {
    const data = await api('/api/account/test', {
      method: 'POST',
      body: JSON.stringify({ account }),
    });
    if (data.user_id && !account.user_id) {
      account.user_id = data.user_id;
      $('.field-user-id', card).value = data.user_id;
    }
    const username = data.user && data.user.username ? `，用户 ${data.user.username}` : '';
    stateEl.className = 'account-state success';
    stateEl.textContent = `${data.message}${username}`;
    updateExports();
  } catch (error) {
    stateEl.className = 'account-state error';
    stateEl.textContent = error.message;
  }
}

function renderResults(data) {
  $('#result-summary').textContent = data.message || '运行完成';
  const list = $('#results-list');
  list.innerHTML = '';

  if (!data.results || !data.results.length) {
    list.innerHTML = '<div class="result-item">没有结果</div>';
    return;
  }

  data.results.forEach((result) => {
    const item = document.createElement('article');
    item.className = 'result-item';
    const badgeClass = result.success ? 'success' : 'error';
    const badgeText = result.success ? '成功' : '失败';
    const username = result.user && result.user.username ? `用户: ${result.user.username}` : '';
    const total = result.total_quota_text ? `累计: ${result.total_quota_text}` : '';
    const count = result.checkin_count ? `本月: ${result.checkin_count} 天` : '';
    item.innerHTML = `
      <div class="result-top">
        <strong>${escapeHtml(result.name || '账号')}</strong>
        <span class="badge ${badgeClass}">${badgeText}</span>
      </div>
      <div>${escapeHtml(result.message || '')}</div>
      <div class="result-meta">
        <span>${escapeHtml(result.url || '')}</span>
        <span>奖励: ${escapeHtml(result.quota_text || '-')}</span>
        ${username ? `<span>${escapeHtml(username)}</span>` : ''}
        ${count ? `<span>${escapeHtml(count)}</span>` : ''}
        ${total ? `<span>${escapeHtml(total)}</span>` : ''}
      </div>
    `;
    list.appendChild(item);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function importConfig() {
  const text = $('#import-text').value;
  try {
    const data = await api('/api/import', {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    if (!data.accounts.length) {
      setStatus(data.message, 'error');
      return;
    }
    state.config.accounts = data.accounts.map((account) => ({ ...account, id: account.id || uid() }));
    renderAccounts();
    setStatus(data.message, 'success');
  } catch (error) {
    setStatus(error.message, 'error');
  }
}

function activateTab(tabName) {
  $$('.nav-tab').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.tab === tabName);
  });
  $$('.tab-panel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.id === `tab-${tabName}`);
  });
}

async function copyExport(kind) {
  const value = kind === 'json' ? state.exports.json : state.exports.simple;
  await navigator.clipboard.writeText(value);
  setStatus('已复制到剪贴板', 'success');
}

function bindEvents() {
  $('.nav-tabs').addEventListener('click', (event) => {
    const button = event.target.closest('.nav-tab');
    if (button) activateTab(button.dataset.tab);
  });

  $('#add-account-btn').addEventListener('click', () => {
    state.config.accounts.push(emptyAccount());
    renderAccounts();
  });

  $('#accounts-list').addEventListener('input', (event) => {
    if (event.target.matches('input, textarea')) {
      updateCardFromInput(event.target);
    }
  });

  $('#accounts-list').addEventListener('click', (event) => {
    const card = event.target.closest('.account-card');
    if (!card) return;
    if (event.target.closest('.remove-account')) {
      state.config.accounts = state.config.accounts.filter((account) => account.id !== card.dataset.id);
      renderAccounts();
    }
    if (event.target.closest('.test-account')) {
      testAccount(card);
    }
  });

  $('#save-btn').addEventListener('click', saveConfig);
  $('#run-btn').addEventListener('click', runCheckin);

  $('#toggle-import-btn').addEventListener('click', () => {
    $('#import-panel').hidden = !$('#import-panel').hidden;
  });
  $('#import-btn').addEventListener('click', importConfig);
  $('#clear-import-btn').addEventListener('click', () => {
    $('#import-text').value = '';
  });

  $('#dingtalk-webhook').addEventListener('input', syncFormToState);
  $('#dingtalk-secret').addEventListener('input', syncFormToState);
  $('#notify-after-checkin').addEventListener('change', syncFormToState);
  $('#schedule-enabled').addEventListener('change', syncFormToState);
  $('#schedule-time').addEventListener('input', syncFormToState);
  $('#schedule-run-missed').addEventListener('change', syncFormToState);
  $('#refresh-schedule-btn').addEventListener('click', () => refreshSchedulerStatus(false));

  $$('.copy-btn').forEach((button) => {
    button.addEventListener('click', () => copyExport(button.dataset.copy));
  });
}

bindEvents();
loadConfig().catch((error) => setStatus(error.message, 'error'));
setInterval(() => refreshSchedulerStatus(true), 15000);
