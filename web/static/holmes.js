(() => {
  const app = document.getElementById('holmes-app');
  const landingEl = document.getElementById('ccd-landing');
  const chatEl = document.getElementById('ccd-chat');
  const landingForm = document.getElementById('ccd-landing-form');
  const landingInput = document.getElementById('ccd-landing-input');
  const landingSend = document.getElementById('ccd-landing-send');
  const messagesEl = document.getElementById('holmes-messages');
  const messagesPane = document.getElementById('holmes-messages-pane');
  const form = document.getElementById('holmes-form');
  const input = document.getElementById('holmes-input');
  const sendBtn = document.getElementById('holmes-send');
  const statusBadge = document.getElementById('holmes-status');
  const geminiChip = document.getElementById('gemini-chip');
  const activityEl = document.getElementById('holmes-activity');
  const activityText = document.getElementById('activity-text');
  const clusterStrip = document.getElementById('cluster-strip-text');
  const clusterDot = document.getElementById('cluster-dot');
  if (!messagesEl || (!form && !landingForm)) return;

  const chatHistory = [];
  let busy = false;
  let chatStarted = false;

  const LEAK_MARKERS = [
    'Answer ONLY what the user asked', 'LIVE CLUSTER FACTS', 'RECENT CHAT',
    'Do not invent pod names', 'Reply entirely in', 'User wants a MANUAL',
    'Using selected model', 'Toolset ', 'Environment variable', 'was not set',
  ];
  const DONE_STEP_TITLES = new Set([
    'HolmesGPT complete', 'Answer ready', 'Using factual fallback', 'Done',
  ]);

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function isNoiseLine(line) {
    const s = String(line || '').trim();
    if (!s) return false;
    const low = s.toLowerCase();
    return LEAK_MARKERS.some(m => low.includes(m.toLowerCase()))
      || /^toolset\b/i.test(s) || /^[✅❌✓✗]/.test(s);
  }

  function sanitizeReply(text) {
    const lines = String(text || '').split('\n').filter(ln => !isNoiseLine(ln));
    let t = lines.join('\n').trim().replace(/^(AI|Assistant|Holmes):\s*/im, '');
    return t || String(text || '').trim();
  }

  function formatReply(text) {
    const raw = sanitizeReply(text);
    const blocks = [];
    const re = /```(\w*)\n([\s\S]*?)```/g;
    let last = 0;
    let m;
    while ((m = re.exec(raw)) !== null) {
      if (m.index > last) blocks.push({ type: 'text', v: raw.slice(last, m.index) });
      blocks.push({ type: 'code', v: m[2] });
      last = m.index + m[0].length;
    }
    if (last < raw.length) blocks.push({ type: 'text', v: raw.slice(last) });
    if (!blocks.length) blocks.push({ type: 'text', v: raw });

    return blocks.map(b => {
      if (b.type === 'code') {
        return `<pre class="holmes-code"><code>${esc(b.v.trim())}</code></pre>`;
      }
      let s = esc(b.v);
      const anchors = [];
      s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, (_, label, url) => {
        const idx = anchors.length;
        anchors.push(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
        return `\x00LNK${idx}\x00`;
      });
      s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
      s = s.replace(/`([^`]+)`/g, '<code class="holmes-inline-code">$1</code>');
      s = s.replace(/(^|[\s(])((https?:\/\/)[^\s<")]+)/g, (full, pre, url) => {
        if (pre.includes('\x00LNK')) return full;
        return `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
      });
      s = s.replace(/\x00LNK(\d+)\x00/g, (_, i) => anchors[+i] || '');
      s = s.replace(/^- (.+)$/gm, '<li>$1</li>');
      if (s.includes('<li>')) s = '<ul class="agent-reply-list">' + s + '</ul>';
      s = s.replace(/\n\n/g, '</p><p>');
      if (!s.startsWith('<ul') && !s.startsWith('<p')) s = '<p>' + s + '</p>';
      return s;
    }).join('');
  }

  function renderStatusCards(apps) {
    if (!apps?.length) return '';
    const cards = apps.map(a => {
      const cls = a.state_key === 'ok' ? 'ok' : a.state_key === 'bad' ? 'bad' : 'idle';
      const gitops = a.gitops
        ? `<div class="status-card-meta">GitOps <span>${esc(a.gitops)}</span></div>` : '';
      const links = [];
      if (a.links?.dashboard) links.push(`<a href="${esc(a.links.dashboard)}" target="_blank" rel="noreferrer">Open app</a>`);
      if (a.links?.argocd_app) links.push(`<a href="${esc(a.links.argocd_app)}" target="_blank" rel="noreferrer">Argo CD</a>`);
      if (a.links?.health) links.push(`<a href="${esc(a.links.health)}" target="_blank" rel="noreferrer">Health</a>`);
      const actions = links.length ? `<div class="status-card-actions">${links.join('')}</div>` : '';
      return `<article class="status-card status-card--${cls}">
        <div class="status-card-top">
          <strong>${esc(a.label)}</strong>
          <span class="status-card-pill status-card-pill--${cls}">${esc(a.state)}</span>
        </div>
        <p class="status-card-blurb">${esc(a.blurb)}</p>
        <code class="status-card-pod">${esc(a.pod_line)}</code>
        ${gitops}${actions}
      </article>`;
    }).join('');
    return `<div class="status-card-grid" role="group" aria-label="Cluster snapshot">${cards}</div>`;
  }

  function renderChoicesPanel(choices) {
    if (!choices?.length) return '';
    return `<div class="choice-deck" role="group" aria-label="Follow-up choices">
      <div class="choice-deck-label">Pick an action</div>
      <div class="choice-panel choice-panel--deck">
      ${choices.map(c => `
        <button type="button" class="choice-chip choice-chip--deck" data-choice-prompt="${esc(c.prompt)}">
          <span class="choice-chip-text">${esc(c.label)}</span>
        </button>`).join('')}
      </div>
    </div>`;
  }

  function enterChatState() {
    if (chatStarted) return;
    chatStarted = true;
    app?.classList.add('agent-has-chat');
    if (landingEl) landingEl.hidden = true;
    if (chatEl) chatEl.hidden = false;
  }

  function appendMsg(role, text, meta) {
    if (role === 'user') enterChatState();

    const wrap = document.createElement('div');
    wrap.className = `holmes-msg holmes-msg--${role}`;
    if (role === 'bot' && meta?.degraded) wrap.classList.add('holmes-msg--warn');
    if (meta?.action) wrap.classList.add('holmes-msg--action');

    let extra = '';
    if (role === 'bot' && meta?.apps_status?.length) {
      extra = renderStatusCards(meta.apps_status);
    } else if (role === 'bot' && meta?.ui === 'choices' && meta?.choices) {
      extra = renderChoicesPanel(meta.choices);
    } else if (role === 'bot' && meta?.ui === 'capabilities') {
      extra = '';
    }

    const foot = role === 'bot' && meta?.degraded ? `<p class="holmes-warn-note">Fallback — ${esc(meta?.gemini_error?.user_message || 'AI unavailable')}</p>` : '';
    const body = role === 'error' ? esc(text) : formatReply(text);
    wrap.innerHTML = `<div class="holmes-bubble">${body}${extra}${foot}</div>`;
    messagesEl.appendChild(wrap);
    wrap.querySelectorAll('[data-choice-prompt]').forEach(btn => {
      btn.addEventListener('click', () => sendMessage(btn.getAttribute('data-choice-prompt')));
    });

    const scrollEl = messagesPane || messagesEl;
    requestAnimationFrame(() => { scrollEl.scrollTop = scrollEl.scrollHeight; });

    if ((role === 'user' || role === 'bot') && !meta?.system) {
      chatHistory.push({ role: role === 'user' ? 'user' : 'assistant', content: text });
      if (chatHistory.length > 16) chatHistory.splice(0, chatHistory.length - 16);
    }
  }

  function setBusy(on) {
    busy = on;
    if (sendBtn) sendBtn.disabled = on || !input?.value.trim();
    if (landingSend) landingSend.disabled = on || !landingInput?.value.trim();
    if (input) input.disabled = on;
    if (landingInput) landingInput.disabled = on;
    if (statusBadge) {
      statusBadge.textContent = on ? 'Working…' : 'Ready';
      statusBadge.classList.toggle('readiness-status-pill--busy', on);
    }
    document.querySelectorAll('.ccd-pill').forEach(b => { b.disabled = on; });
  }

  function syncSendButtons() {
    const v = input?.value.trim();
    const lv = landingInput?.value.trim();
    if (sendBtn) sendBtn.disabled = busy || !v;
    if (landingSend) landingSend.disabled = busy || !lv;
  }

  function showActivity(msg) {
    if (DONE_STEP_TITLES.has(msg)) return;
    if (activityEl) {
      activityEl.hidden = false;
      if (activityText) activityText.textContent = msg || 'Thinking through your request…';
    }
  }

  function hideActivity() {
    if (activityEl) activityEl.hidden = true;
  }

  function renderGeminiChip(data) {
    if (!geminiChip) return;
    if (!data) { geminiChip.hidden = true; return; }
    geminiChip.hidden = false;
    geminiChip.className = 'holmes-gemini-chip' + (data.ok ? ' holmes-gemini-chip--ok' : ' holmes-gemini-chip--bad');
    geminiChip.textContent = data.ok ? 'AI ready' : `AI: ${data.label || 'unavailable'}`;
  }

  async function refreshClusterStrip() {
    try {
      const j = await (await fetch('/api/holmes/snapshot')).json();
      if (!j.ok) return;
      const snap = j.data || j;
      const apps = snap.apps || [];
      const healthy = apps.filter(a => a.healthy).length;
      const critical = apps.filter(a => a.deployed && !a.healthy).length;
      const ns = snap.namespace || 'enlight-staging';
      if (clusterStrip) {
        clusterStrip.textContent = critical
          ? `${ns} · ${critical} critical · ${healthy}/${apps.length} healthy`
          : `${ns} · ${healthy}/${apps.length} healthy`;
      }
      if (clusterDot) {
        clusterDot.className = 'ccd-cluster-dot' + (
          critical ? ' ccd-cluster-dot--bad' : healthy < apps.length ? ' ccd-cluster-dot--warn' : ''
        );
      }
    } catch (_) { /* optional */ }
  }

  async function streamChat(msg, history) {
    const resp = await fetch('/api/holmes/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history }),
    });
    if (resp.status === 404) return null;
    if (!resp.ok || !resp.body) throw new Error('Stream failed (HTTP ' + resp.status + ')');

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let result = null;
    const deadline = Date.now() + 320000;

    while (true) {
      if (Date.now() > deadline) throw new Error('Request timed out — try "show status".');
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
        if (data.type === 'step' && data.step) showActivity(data.step.title);
        else if (data.type === 'complete') result = data.data;
        else if (data.type === 'error') throw new Error(data.error);
      }
    }
    return result;
  }

  async function postChat(msg, history) {
    const resp = await fetch('/api/holmes/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history }),
    });
    const j = await resp.json();
    if (!j.ok) throw new Error(j.error || 'Request failed');
    return j.data;
  }

  async function sendMessage(text) {
    const msg = String(text || '').trim();
    if (!msg || busy) return;
    const history = chatHistory.slice();
    appendMsg('user', msg);
    if (input) input.value = '';
    if (landingInput) landingInput.value = '';
    syncSendButtons();
    setBusy(true);
    showActivity('Connecting…');

    try {
      let data = await streamChat(msg, history);
      if (!data) data = await postChat(msg, history);
      if (data?.ok) {
        appendMsg('bot', data.reply || '(empty)', {
          degraded: data.degraded,
          gemini_error: data.gemini_error,
          action: data.action,
          target: data.action_target,
          apps_status: data.apps_status,
          ui: data.ui,
          choices: data.choices,
          links: data.links,
        });
        if (data.degraded && data.gemini_error) {
          renderGeminiChip({ ok: false, label: data.gemini_error.user_message });
        }
      } else {
        appendMsg('error', data?.error || 'Request failed');
      }
    } catch (e) {
      appendMsg('error', String(e.message || e));
    } finally {
      hideActivity();
      setBusy(false);
      refreshClusterStrip();
      syncSendButtons();
      (chatStarted ? input : landingInput)?.focus();
    }
  }

  function bindPromptButtons() {
    document.querySelectorAll('[data-prompt]').forEach(btn => {
      btn.addEventListener('click', () => {
        const prompt = btn.getAttribute('data-prompt');
        if (prompt) sendMessage(prompt);
      });
    });
  }

  landingForm?.addEventListener('submit', e => {
    e.preventDefault();
    sendMessage(landingInput?.value);
  });
  form?.addEventListener('submit', e => {
    e.preventDefault();
    sendMessage(input?.value);
  });

  landingInput?.addEventListener('input', syncSendButtons);
  input?.addEventListener('input', syncSendButtons);
  landingInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); landingForm?.requestSubmit(); }
  });
  input?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form?.requestSubmit(); }
  });

  bindPromptButtons();
  syncSendButtons();

  const urlPrompt = new URLSearchParams(window.location.search).get('prompt');
  if (urlPrompt) {
    window.history.replaceState({}, '', window.location.pathname);
    sendMessage(urlPrompt);
  }

  fetch('/health/gemini').then(r => r.json()).then(j => renderGeminiChip(j.data || j)).catch(() => {});
  refreshClusterStrip();
})();
