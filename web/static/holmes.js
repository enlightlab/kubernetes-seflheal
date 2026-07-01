(() => {
  const SUGGESTIONS = [
    'Is my pod healthy?',
    'What image is fastapi using?',
    'Is the outage recovered?',
    'Investigate root cause of the staging failure',
    'Summarize cluster health for a client in 3 sentences',
  ];

  const messagesEl = document.getElementById('holmesMessages');
  const form = document.getElementById('holmesForm');
  const input = document.getElementById('holmesInput');
  const sendBtn = document.getElementById('holmesSend');
  const statusBadge = document.getElementById('holmesStatus');
  const activityEl = document.getElementById('holmesActivity');
  const activityTitle = document.getElementById('activityTitle');
  const activityDetail = document.getElementById('activityDetail');
  const activityTime = document.getElementById('activityTime');
  let busy = false;
  let activityTimer = null;
  let activityStart = 0;

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function formatReply(text) {
    let s = esc(text);
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`([^`]+)`/g, '<code class="holmes-inline-code">$1</code>');
    s = s.replace(/^- (.+)$/gm, '<li>$1</li>');
    if (s.includes('<li>')) s = '<ul class="holmes-ul">' + s + '</ul>';
    s = s.replace(/\n\n/g, '</p><p>');
    if (!s.startsWith('<ul') && !s.startsWith('<p')) s = '<p>' + s + '</p>';
    return s;
  }

  function appendMsg(role, text, meta) {
    const wrap = document.createElement('div');
    wrap.className = `holmes-msg holmes-msg--${role}`;
    const label = role === 'user' ? 'You' : role === 'error' ? 'Error' : 'HolmesGPT';
    const source = meta?.source
      ? `<span class="holmes-source holmes-source--${meta.source}">${meta.source === 'telemetry' ? 'Live telemetry' : 'Holmes deep scan'}</span>`
      : '';
    const model = meta?.model ? `<span class="holmes-msg-meta">${esc(meta.model)}</span>` : '';
    wrap.innerHTML = `
      <div class="holmes-msg-head">
        <span class="holmes-msg-label">${label}</span>
        ${source}${model}
      </div>
      <div class="holmes-msg-body">${role === 'error' ? esc(text) : formatReply(text)}</div>
    `;
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    input.disabled = on;
    statusBadge.textContent = on ? 'Holmes working…' : 'Holmes ready';
    statusBadge.className = 'badge' + (on ? ' warn' : '');
  }

  function showActivity(title, detail) {
    activityEl.hidden = false;
    activityTitle.textContent = title || 'Working…';
    activityDetail.textContent = detail || '';
    if (!activityStart) {
      activityStart = Date.now();
      activityTimer = setInterval(() => {
        const sec = Math.floor((Date.now() - activityStart) / 1000);
        activityTime.textContent = sec + 's';
      }, 1000);
    }
  }

  function hideActivity() {
    activityEl.hidden = true;
    activityStart = 0;
    if (activityTimer) clearInterval(activityTimer);
    activityTimer = null;
    activityTime.textContent = '0s';
  }

  function renderHealth(d) {
    const healthy = !!d.healthy;
    const card = document.getElementById('healthCard');
    const pip = document.getElementById('healthPip');
    const title = document.getElementById('healthTitle');
    const sub = document.getElementById('healthSub');
    card.className = 'holmes-health-card' + (healthy ? ' holmes-health-card--ok' : ' holmes-health-card--bad');
    pip.className = 'holmes-health-pip' + (healthy ? ' holmes-health-pip--ok' : ' holmes-health-pip--bad');
    title.textContent = healthy ? 'Staging is healthy' : 'Staging needs attention';
    sub.textContent = d.pod_line || 'No pod data';
    document.getElementById('argoPills').innerHTML = [
      `<span class="holmes-pill holmes-pill--sync">${esc(d.argocd_sync || '—')}</span>`,
      `<span class="holmes-pill holmes-pill--health">${esc(d.argocd_health || '—')}</span>`,
    ].join('');
    document.getElementById('metaNs').textContent = d.namespace || '—';
    document.getElementById('metaDep').textContent = d.deployment || '—';
    document.getElementById('metaImage').textContent = d.image_short || d.image || '—';
    document.getElementById('metaPod').textContent = d.pod_line || '—';
    document.getElementById('metaModel').textContent = d.model || '—';
  }

  async function loadSnapshot() {
    try {
      const r = await fetch('/api/holmes/snapshot');
      const text = await r.text();
      let j;
      try { j = JSON.parse(text); } catch (_) {
        throw new Error('Server returned non-JSON — try refreshing or check the UI pod');
      }
      if (!j.ok) throw new Error(j.error || 'Snapshot failed');
      renderHealth(j.data);
      if (!j.data.holmes_enabled) {
        statusBadge.textContent = 'Holmes disabled';
        statusBadge.className = 'badge err';
        document.getElementById('holmesFootnote').textContent =
          'HolmesGPT is off on the cluster — set HOLMES_ENABLED=true and Gemini key.';
        sendBtn.disabled = true;
      } else {
        statusBadge.textContent = 'Holmes ready';
        statusBadge.className = 'badge';
      }
    } catch (e) {
      statusBadge.textContent = 'Snapshot error';
      statusBadge.className = 'badge err';
      document.getElementById('healthSub').textContent = String(e.message || e);
    }
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

  async function streamChat(msg) {
    const resp = await fetch('/api/holmes/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });
    if (!resp.ok || !resp.body) {
      const t = await resp.text();
      if (t.trim().startsWith('<')) {
        throw new Error(
          'Gateway timeout (HTML response). The connection was cut — deep scans use SSE now; redeploy the latest UI image and retry.'
        );
      }
      throw new Error('Stream failed (HTTP ' + resp.status + ')');
    }

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let result = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() || '';
      for (const block of parts) {
        const line = block.trim();
        if (!line.startsWith('data:')) continue;
        let data;
        try { data = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (data.type === 'step' && data.step) {
          showActivity(data.step.title, data.step.detail);
        } else if (data.type === 'ping') {
          showActivity(activityTitle.textContent, activityDetail.textContent || 'Still working…');
        } else if (data.type === 'complete') {
          result = data.data;
        } else if (data.type === 'error') {
          throw new Error(data.error);
        }
      }
    }
    return result;
  }

  async function sendMessage(text) {
    const msg = String(text || '').trim();
    if (!msg || busy) return;
    appendMsg('user', msg);
    input.value = '';
    setBusy(true);
    showActivity('Starting', 'Connecting to cluster…');

    try {
      const data = await streamChat(msg);
      hideActivity();
      if (data?.ok) {
        appendMsg('bot', data.reply || '(empty reply)', {
          source: data.source,
          model: data.model,
        });
        if (data.context) renderHealth({
          healthy: data.context.healthy,
          pod_line: data.context.pod_line,
          namespace: data.context.namespace,
          deployment: data.context.deployment,
          image: data.context.image,
          image_short: (data.context.image || '').split('/').pop(),
          argocd_sync: data.context.argocd_sync,
          argocd_health: data.context.argocd_health,
          model: data.model,
          holmes_enabled: true,
        });
      } else {
        appendMsg('error', data?.error || 'HolmesGPT could not complete the request');
      }
    } catch (e) {
      hideActivity();
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

  document.getElementById('btnRefreshSnap').addEventListener('click', loadSnapshot);

  renderSuggestions();
  loadSnapshot();
  setInterval(loadSnapshot, 45000);
})();
