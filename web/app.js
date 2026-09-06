(function () {
  'use strict';

  const query = new URLSearchParams(window.location.search);
  const token = query.get('token') || '';
  if (token) window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
  const state = { kind: 'json', path: '', cursor: 0, timer: null, token: token };
  const editor = document.getElementById('editor');

  function setMessage(id, message) {
    document.getElementById(id).textContent = message;
  }

  async function request(url, options) {
    const requestOptions = options || {};
    const headers = new Headers(requestOptions.headers || {});
    if (state.token) headers.set('Authorization', 'Bearer ' + state.token);
    requestOptions.headers = headers;
    const response = await fetch(url, requestOptions);
    if (!response.ok) {
      const body = await response.json().catch(function () { return {}; });
      throw new Error(body.error ? body.error.message : '请求失败');
    }
    if (response.status === 204) return null;
    const type = response.headers.get('content-type') || '';
    return type.indexOf('application/json') >= 0 ? response.json() : response.text();
  }

  function selectFile(path) {
    state.path = path;
    request('/api/' + state.kind + '?path=' + encodeURIComponent(path))
      .then(function (data) {
        editor.value = state.kind === 'json' ? JSON.stringify(data, null, 2) : data.content;
        setMessage('editor-title', path);
        setMessage('save-state', '已加载');
      })
      .catch(function (error) { setMessage('save-state', error.message); });
  }

  function loadFiles() {
    request('/api/files?directory=')
      .then(function (data) {
        const list = document.getElementById('file-list');
        list.innerHTML = '';
        data.files.filter(function (item) { return item.type === 'file' && item.name.endsWith('.' + state.kind); })
          .forEach(function (item) {
            const button = document.createElement('button');
            button.className = 'file-item';
            button.textContent = item.name;
            button.type = 'button';
            button.onclick = function () { selectFile(item.path); };
            list.appendChild(button);
          });
      })
      .catch(function (error) { setMessage('session-status', error.message); });
  }

  function save(action) {
    if (!state.path) return;
    let payload;
    try { payload = state.kind === 'json' ? JSON.parse(editor.value) : { path: state.path, content: editor.value }; }
    catch (error) { setMessage('save-state', 'JSON 格式无效'); return; }
    const endpoint = state.kind === 'json' ? '/api/push' : '/api/ini';
    const body = state.kind === 'json'
      ? { action: action || 'save', path: state.path, payload: payload }
      : { path: state.path, content: payload.content };
    request(endpoint, { method: state.kind === 'json' ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function () { setMessage('save-state', '已保存'); if (action && action !== 'save') startPolling(); })
      .catch(function (error) { setMessage('save-state', error.message); });
  }

  function loadTemp() {
    request('/api/temp').then(function (data) {
      const list = document.getElementById('temp-list');
      list.innerHTML = '';
      if (!data.files.length) { list.textContent = '暂无临时文件'; return; }
      data.files.forEach(function (name) {
        const row = document.createElement('div');
        row.textContent = name + ' ';
        const button = document.createElement('button');
        button.className = 'text-button';
        button.type = 'button';
        button.textContent = '确认删除';
        button.onclick = function () {
          if (!window.confirm('确认删除 ' + name + ' 吗？')) return;
          request('/api/temp/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names: [name], confirmed: true }) })
            .then(loadTemp).catch(function (error) { setMessage('temp-list', error.message); });
        };
        row.appendChild(button);
        list.appendChild(row);
      });
    }).catch(function (error) { setMessage('temp-list', error.message); });
  }

  function startPolling() {
    if (state.timer) return;
    state.timer = setInterval(function () {
      request('/api/push/status?cursor=' + state.cursor).then(function (data) {
        setMessage('push-status', data.status);
        data.outputs.forEach(function (item) { state.cursor = Math.max(state.cursor, item.sequence); document.getElementById('logs').textContent += '\n' + item.sequence + ' | ' + item.stream + ' | ' + item.message; });
        if (data.status !== 'running') { clearInterval(state.timer); state.timer = null; }
      }).catch(function () { clearInterval(state.timer); state.timer = null; });
    }, 1000);
  }

  function updateActions() {
    const pushButtons = [document.getElementById('direct-push'), document.getElementById('save-and-push')];
    pushButtons.forEach(function (button) { button.hidden = state.kind === 'ini'; });
  }

  function clearEditorContext() {
    state.path = '';
    editor.value = '';
    setMessage('editor-title', '');
    setMessage('save-state', '');
  }

  document.querySelectorAll('.tab').forEach(function (tab) { tab.onclick = function () { state.kind = tab.dataset.kind; clearEditorContext(); document.querySelector('.tab.active').classList.remove('active'); tab.classList.add('active'); updateActions(); loadFiles(); }; });
  document.getElementById('save').onclick = function () { save('save'); };
  document.getElementById('direct-push').onclick = function () { save('direct'); };
  document.getElementById('save-and-push').onclick = function () { save('save_and_push'); };
  document.getElementById('refresh-temp').onclick = loadTemp;
  document.getElementById('stop-service').onclick = function () { if (window.confirm('确定退出 Web 服务吗？')) request('/api/service/stop', { method: 'POST' }); };
  updateActions();
  request('/api/session').then(function () { setMessage('session-status', '已连接'); loadFiles(); }).catch(function (error) { setMessage('session-status', error.message); });
}());
