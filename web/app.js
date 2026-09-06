(function () {
  'use strict';

  const query = new URLSearchParams(window.location.search);
  const token = query.get('token') || '';
  if (token) window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
  const state = { kind: 'json', path: '', directory: '', cursor: 0, timer: null, token: token, requestVersion: 0, dirty: false };
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

  function beforeContextChange() {
    if (!state.dirty) return true;
    return window.confirm('当前有未保存的变更，放弃未保存的变更吗？');
  }

  function setEditor(content, path) {
    editor.value = content;
    state.path = path;
    state.dirty = false;
    setMessage('editor-title', path);
    setMessage('save-state', '已加载');
  }

  function selectFile(path) {
    if (!beforeContextChange()) return;
    state.path = path;
    const requestedKind = state.kind;
    const requestedPath = path;
    const requestVersion = ++state.requestVersion;
    const endpoint = requestedKind === 'json' ? '/api/json?path=' : '/api/ini?path=';
    request(endpoint + encodeURIComponent(path))
      .then(function (data) {
        if (requestVersion !== state.requestVersion || requestedKind !== state.kind || requestedPath !== state.path) return;
        setEditor(requestedKind === 'json' ? JSON.stringify(data, null, 2) : data.content, requestedPath);
      })
      .catch(function (error) {
        if (requestVersion === state.requestVersion) setMessage('save-state', error.message);
      });
  }

  function loadFiles() {
    request('/api/files?directory=' + encodeURIComponent(state.directory))
      .then(function (data) {
        state.directory = data.path || '';
        const list = document.getElementById('file-list');
        list.innerHTML = '';
        const parent = document.getElementById('parent-directory');
        parent.hidden = !state.directory;
        data.items.forEach(function (item) {
          if (item.type === 'file' && !item.name.endsWith('.' + state.kind)) return;
          const button = document.createElement('button');
          button.className = 'file-item';
          button.textContent = item.type === 'directory' ? '[目录] ' + item.name : item.name;
          button.type = 'button';
          button.onclick = function () {
            if (item.type === 'directory') {
              if (!beforeContextChange()) return;
              state.directory = item.path;
              loadFiles();
            } else selectFile(item.path);
          };
          list.appendChild(button);
        });
        setMessage('directory-label', state.directory || '工作区');
      })
      .catch(function (error) { setMessage('session-status', error.message); });
  }

  function save(action) {
    if (!state.path) return saveAs();
    let payload;
    try { payload = state.kind === 'json' ? JSON.parse(editor.value) : null; }
    catch (error) { setMessage('save-state', 'JSON 格式无效'); return; }
    const endpoint = state.kind === 'json' ? '/api/push' : '/api/ini';
    const body = state.kind === 'json'
      ? { action: action || 'save', path: state.path, payload: payload }
      : { path: state.path, content: editor.value };
    request(endpoint, { method: state.kind === 'json' ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function () { state.dirty = false; setMessage('save-state', '已保存'); if (action && action !== 'save') startPolling(); })
      .catch(function (error) { setMessage('save-state', error.message); });
  }

  function saveAs() {
    const suggested = state.path || (state.directory ? state.directory + '/' : '') + 'new.json';
    const path = window.prompt('请输入 JSON 文件路径（相对工作区）', suggested);
    if (!path) return;
    if (state.kind !== 'json') { setMessage('save-state', 'INI 文件请直接保存'); return; }
    let payload;
    try { payload = JSON.parse(editor.value || '{}'); }
    catch (error) { setMessage('save-state', 'JSON 格式无效'); return; }
    const body = Object.assign({}, payload, { path: path });
    request('/api/json', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function () { state.path = path; state.dirty = false; setMessage('editor-title', path); setMessage('save-state', '已创建'); loadFiles(); })
      .catch(function (error) { setMessage('save-state', error.message); });
  }

  function newJson() {
    if (!beforeContextChange()) return;
    state.kind = 'json';
    document.querySelectorAll('.tab').forEach(function (tab) { tab.classList.toggle('active', tab.dataset.kind === 'json'); });
    updateActions();
    setEditor(JSON.stringify({ title: '新模板', content: '', channels: [{}] }, null, 2), '');
    state.dirty = true;
    setMessage('editor-title', '新 JSON 文件');
    setMessage('save-state', '未保存');
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
        button.className = 'text-button'; button.type = 'button'; button.textContent = '确认删除';
        button.onclick = function () {
          if (!window.confirm('确认删除 ' + name + ' 吗？')) return;
          request('/api/temp/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ names: [name], confirmed: true }) }).then(loadTemp).catch(function (error) { setMessage('temp-list', error.message); });
        };
        row.appendChild(button); list.appendChild(row);
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
    document.getElementById('save-as').hidden = state.kind === 'ini';
  }

  function clearEditorContext() {
    state.requestVersion += 1; state.path = ''; editor.value = ''; state.dirty = false;
    setMessage('editor-title', ''); setMessage('save-state', '');
  }

  editor.addEventListener('input', function () { state.dirty = true; setMessage('save-state', '有未保存变更'); });
  document.querySelectorAll('.tab').forEach(function (tab) { tab.onclick = function () { if (!beforeContextChange()) return; state.kind = tab.dataset.kind; clearEditorContext(); document.querySelector('.tab.active').classList.remove('active'); tab.classList.add('active'); updateActions(); loadFiles(); }; });
  document.getElementById('parent-directory').onclick = function () { if (!beforeContextChange()) return; const parts = state.directory.split('/'); parts.pop(); state.directory = parts.join('/'); loadFiles(); };
  document.getElementById('new-json').onclick = newJson;
  document.getElementById('save').onclick = function () { save('save'); };
  document.getElementById('save-as').onclick = saveAs;
  document.getElementById('direct-push').onclick = function () { save('direct'); };
  document.getElementById('save-and-push').onclick = function () { save('save_and_push'); };
  document.getElementById('refresh-temp').onclick = loadTemp;
  document.getElementById('stop-service').onclick = function () { if (window.confirm('确定退出 Web 服务吗？')) request('/api/service/stop', { method: 'POST' }); };
  updateActions();
  request('/api/session').then(function () { setMessage('session-status', '已连接'); loadFiles(); }).catch(function (error) { setMessage('session-status', error.message); });
}());
