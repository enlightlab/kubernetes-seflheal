(() => {
  const SUGGESTIONS = [
    'What is wrong with fastapi in enlight-staging right now?',
    'Summarize pod health for a client in 3 sentences.',
    'Is the staging outage still active or recovered?',
    'What image is the fastapi deployment using?',
  ];

  const messagesEl = document.getElementById('holmesMessages');
  const form = document.getElementById('holmesForm');
  const input = document.getElementById('holmesInput');
  const sendBtn = document.getElementById('holmesSend');
  const statusBadge = document.getElementById('holmesStatus');
  let busy = false;

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function appendMsg(role, text, extra) {
    const wrap = document.createElement('div');
    wrap.className = `holmes-msg holmes-msg--${role}`;
    const label = role === 'user' ? 'You' : role === 'error' ? 'Error' : 'HolmesGPT';
    wrap.innerHTML = `
      <div class="holmes-msg-label">${label}</div>
      <div class="holmes-msg-body">${esc(text).replace(/\n/g, '<br>')}${extra || ''}</div>
    `;
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    input.disabled = on;
    statusBadge.textContent = on ? 'Holmes thinking…' : 'Holmes ready';
    statusBadge.className = 'badge' + (on ? ' warn' : '');
  }

  async function loadConfig() {
    try {
      const j = await (await fetch('/api/config')).json();
      if (!j.ok) return;
      const d = j.data || {};
      document.getElementById('metaNs').textContent = d.namespace || '—';
      document.getElementById('metaDep').textContent = 'fastapi';
      document.getElementById('metaModel').textContent = d.holmes_model || '—';
      if (!d.holmes_enabled) {
        statusBadge.textContent = 'Holmes disabled';
        statusBadge.className = 'badge err';
        document.getElementById('holmesFootnote').textContent =
          'HolmesGPT is off — set HOLMES_ENABLED=true and a Gemini key on the cluster.';
        sendBtn.disabled = true;
      } else {
        statusBadge.textContent = 'Holmes ready';
      }
    } catch (_) { /* ignore */ }

    try {
      const j = await (await fetch('/api/status')).json();
      if (j.ok && j.data) {
        document.getElementById('metaPod').textContent = j.data.pod || '—';
      }
    } catch (_) { /* ignore */ }
  }

  function renderSuggestions() {
    const el = document.getElementById('holmesSuggestions');
    el.innerHTML = SUGGESTIONS.map(s =>
      `<button type="button" class="holmes-chip">${esc(s)}</button>`
    ).join('');
    el.querySelectorAll('.holmes-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        input.value = btn.textContent;
        input.focus();
      });
    });
  }

  async function sendMessage(text) {
    const msg = String(text || '').trim();
    if (!msg || busy) return;
    appendMsg('user', msg);
    input.value = '';
    setBusy(true);
    const thinking = document.createElement('div');
    thinking.className = 'holmes-msg holmes-msg--bot holmes-msg--thinking';
    thinking.innerHTML = '<div class="holmes-msg-label">HolmesGPT</div><div class="holmes-msg-body"><span class="holmes-dots">Investigating cluster</span></div>';
    messagesEl.appendChild(thinking);
    messagesEl.scrollTop = messagesEl.scrollHeight;

    try {
      const r = await fetch('/api/holmes/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg }),
      });
      const j = await r.json();
      thinking.remove();
      if (j.ok && j.data?.ok) {
        const model = j.data.model ? `<div class="holmes-msg-meta">Model: ${esc(j.data.model)}</div>` : '';
        appendMsg('bot', j.data.reply || '(empty reply)', model);
        if (j.data.context?.pod_line) {
          document.getElementById('metaPod').textContent = j.data.context.pod_line;
        }
      } else {
        const err = j.error || j.data?.error || 'HolmesGPT request failed';
        appendMsg('error', err);
      }
    } catch (e) {
      thinking.remove();
      appendMsg('error', String(e.message || e));
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  form.addEventListener('submit', e => {
    e.preventDefault();
    sendMessage(input.value);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  renderSuggestions();
  loadConfig();
})();
