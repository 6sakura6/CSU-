(() => {
  const $ = selector => document.querySelector(selector);
  const api = () => window.pywebview.api;
  let lastName = '本地验证账户';
  let lastLogs = '';
  let timer;
  let refreshing = false;
  function toast(text) {
    $('#toast').textContent = text;
    $('#toast').classList.add('visible');
    clearTimeout(timer);
    timer = setTimeout(() => $('#toast').classList.remove('visible'), 2600);
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const state = await api().state();
      $('#status').textContent = state.status;
      document.body.classList.toggle('running', state.busy);
      $('#run').disabled = state.busy;
      $('#run').querySelector('span').textContent = state.busy ? '正在验证…' : '运行完整验证';
      $('#stop').hidden = !state.busy;
      $('#timer-toggle').setAttribute('aria-checked', String(state.timer_active));
      $('#timer-state').textContent = state.timer_text;
      $('#account-name').textContent = lastName = state.name;
      if (state.size_mb !== null && state.size_mb !== undefined) {
        $('#size').textContent = Number(state.size_mb).toFixed(1);
        $('#size-caption').textContent = '完整目录 · 解压后实测';
      }
      const encrypted = state.results.dpapi && state.results.account_isolation;
      $('#crypto-state').textContent = encrypted ? '已通过' : '待验证';
      $('#crypto-state').classList.toggle('passed', Boolean(encrypted));
      const browser = state.results.fresh_page_verification && state.results.cleanup;
      $('#browser-mark').textContent = browser ? '已通过' : '待验证';
      $('#browser-mark').classList.toggle('passed', Boolean(browser));
      $('#browser-state').textContent = browser ? '本地登录、定位协议与会话已验证' : 'Edge / Chrome · 本地测试';
      $('#log-count').textContent = `${state.records.length} 条`;
      const logs = JSON.stringify(state.records);
      if (lastLogs !== logs && state.records.length) {
        const nodes = state.records.map(record => {
          const row = document.createElement('div'); row.className = 'log-row';
          const time = document.createElement('time'); time.textContent = record.time;
          const message = document.createElement('span'); message.textContent = record.message;
          row.append(time, message); return row;
        });
        $('#logs').replaceChildren(...nodes);
        lastLogs = logs;
      }
    } finally { refreshing = false; }
  }
  async function action(fn) {
    try {
      const result = await fn();
      if (result && result.ok === false) toast(result.message || '操作未完成');
      await refresh();
      return result;
    } catch (_) { toast('操作未完成，请稍后重试'); }
  }
  window.liteRefresh = refresh;
  window.addEventListener('pywebviewready', async () => {
    $('#run').disabled = false;
    $('#run').onclick = () => action(() => api().run_validation());
    $('#stop').onclick = () => action(() => api().stop_validation());
    $('#timer-toggle').onclick = () => action(() => api().toggle_timer());
    for (const button of document.querySelectorAll('[data-window]')) {
      button.onclick = () => action(() => api().window_action(button.dataset.window));
    }
    $('#tray-test').onclick = () => action(() => api().window_action('hide'));
    $('#quit').onclick = () => api().window_action('quit');
    $('#logs-jump').onclick = () => $('#log-panel').scrollIntoView({behavior:'smooth',block:'end'});
    $('#settings-open').onclick = () => {
      $('#name').value = lastName; $('#note').value = ''; $('#settings').showModal();
    };
    for (const id of ['#settings-close','#settings-cancel']) $(id).onclick = () => $('#settings').close();
    $('#settings-form').onsubmit = async event => {
      event.preventDefault();
      const result = await action(() => api().save_settings($('#name').value, $('#note').value));
      if (result && result.ok) { $('#note').value = ''; $('#settings').close(); toast(result.message); }
    };
    await refresh();
    window.liteReady = true;
    setInterval(() => refresh().catch(() => {}), 650);
  });
})();
