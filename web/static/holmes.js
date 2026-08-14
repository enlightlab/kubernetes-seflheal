(() => {
  const pageEl = document.getElementById('holmes-app');
  const heroStateEl = document.getElementById('el-hero-state');
  const chatStateEl = document.getElementById('el-chat-state');
  const messagesEl = document.getElementById('holmes-messages');
  const messagesPane = document.getElementById('holmes-messages-pane');
  const composeLanding = document.getElementById('el-compose-slot-landing');
  const composeChat = document.getElementById('el-compose-slot-chat');
  const composeTpl = document.getElementById('el-compose-template');
  const clusterStrip = document.getElementById('cluster-strip-text');
  const clusterDot = document.getElementById('cluster-dot');
  const CHAT_MODE = 'agent';

  if (!messagesEl || !composeTpl || !composeLanding) return;

  composeLanding.appendChild(composeTpl.content.cloneNode(true));
  const form = document.getElementById('holmes-form');
  const input = document.getElementById('holmes-input');
  const sendBtn = document.getElementById('holmes-send');
  if (!form || !input || !sendBtn) return;

  const FIRST_BOT_MESSAGE =
    "Hey! I'm your **Kubernetes agent** on **enlight-staging**. "
    + "Say *pod status*, *break FastAPI*, or *auto-fix* — I'll deploy, inject failures, diagnose, and heal **FastAPI** and **Nginx** without kubectl.";

  const chatHistory = [];
  let busy = false;
  let chatStarted = false;
  let loadingEl = null;
  let stripPoll = null;
  let liveSteps = [];
  let seededWelcome = false;

  function markChatHasMessages() {
    pageEl?.classList.add('el-chat-has-messages');
  }

  const LEAK_MARKERS = [
    'Answer ONLY what the user asked', 'LIVE CLUSTER FACTS', 'RECENT CHAT',
    'Do not invent pod names', 'Reply entirely in', 'User wants a MANUAL',
    'Using selected model', 'Toolset ', 'Environment variable', 'was not set',
  ];
  const DONE_STEP_TITLES = new Set([
    'HolmesGPT complete', 'Answer ready', 'Using factual fallback', 'Done', 'Agent tools',
  ]);

  const CHAOS_MODE_PHRASE = {
    init: 'init container crash', startup: 'startup probe failure',
    readiness: 'readiness probe failure', liveness: 'liveness probe failure',
    bad_command: 'bad command / RunContainerError', privileged: 'privileged container denied',
    oom: 'OOM', cpu_throttle: 'CPU throttling', crash: 'crash loop',
    image: 'image pull failure', deadlock: 'deadlock / hang',
    instant: 'instant outage / scale to zero', configmap: 'missing ConfigMap',
    secret_env: 'missing Secret env', bad_rollout: 'bad rollout',
    rollout_stuck: 'rollout stuck', service_selector: 'service selector mismatch',
    port_mismatch: 'port mismatch', network_policy: 'network policy block',
    ingress_bad: 'ingress misconfiguration', pending: 'pending / unschedulable',
    affinity: 'node affinity failure', toleration: 'taint toleration mismatch',
    volume: 'volume mount failure', hostpath: 'HostPath failure',
    pvc_pending: 'PVC pending', readonly_root: 'read-only filesystem',
    memory_leak: 'memory leak', cpu_stress: 'CPU stress',
    http_500: 'HTTP 500', high_latency: 'high latency',
    dns_failure: 'DNS failure', network_delay: 'network delay',
    network_loss: 'packet loss', network_partition: 'network partition',
    pod_kill: 'pod kill', http_abort: 'HTTP abort 500',
    http_delay: 'HTTP delay', stress_chaos_cpu: 'Chaos Mesh CPU stress',
    stress_chaos_memory: 'Chaos Mesh memory stress',
  };

  function modesToSimulatePhrase(modes) {
    return (modes || []).map(m => CHAOS_MODE_PHRASE[m] || String(m).replace(/_/g, ' ')).join(' and ');
  }

  function isTechnicalStep(step) {
    const title = String(step?.title || '');
    if (/^Tool:/i.test(title)) return true;
    if (/^Engineer agent$/i.test(title)) return true;
    if (/^Answer ready$/i.test(title)) return true;
    if (/^Simulating failure$/i.test(title)) return true;
    if (/^Done$/i.test(title)) return true;
    if (step?.phase === 'ai') return true;
    return false;
  }

  function rewritePromptForApp(basePrompt, appId) {
    const appPhrase = appId === 'both' ? 'both apps' : appId;
    let p = String(basePrompt || '').trim();
    p = p
      .replace(/\bon both apps\b/gi, `on ${appPhrase}`)
      .replace(/\bon fastapi\b/gi, `on ${appPhrase}`)
      .replace(/\bon nginx\b/gi, `on ${appPhrase}`);
    if (!/\bon (fastapi|nginx|both apps?)\b/i.test(p)) {
      p = `${p.replace(/[.!?]$/, '')} on ${appPhrase}`;
    }
    return p;
  }

  function extractAppTarget(prompt) {
    const p = String(prompt || '').toLowerCase();
    if (/\b(both|both apps|all apps|all applications)\b/.test(p)) return 'all';
    if (/\bfastapi\b|\bfast api\b/.test(p)) return 'fastapi';
    if (/\bnginx\b|\bnginx-demo\b/.test(p)) return 'nginx';
    return null;
  }

  function isInjectIntent(prompt) {
    const p = String(prompt || '').toLowerCase();
    if (/\b(simulat|stimulat|inject|break|chaos|cause|trigger)\w*\b/.test(p)) return true;
    return /\b(probe|crash|oom|outage|failure|readiness|liveness|startup)\b/.test(p)
      && /\b(simulat|stimulat|inject|cause|break)\w*\b/.test(p);
  }

  function needsAppPicker(prompt) {
    const p = String(prompt || '').toLowerCase();
    if (/\b(same\s+outage|inject\s+(the\s+)?same|outage\s+again)\b/.test(p)) {
      return false;
    }
    if (isInjectIntent(p) && !extractAppTarget(p)) {
      return true;
    }
    if (/\b(deploy|auto-?fix|heal|reset|what broke|explain|show|open links|capabilities|list all|failure modes)\b/.test(p)) {
      return false;
    }
    if (/\b(pod status|show status|cluster status|apps status)\b/.test(p)) {
      return false;
    }
    if (extractAppTarget(p)) {
      return false;
    }
    return /\b(simulat|stimulat|inject|break|chaos|crash|oom|outage|failure|network|dns|latency|meltdown|storm|disaster|pending|volume|policy|restart|continuously|cause|traffic|receiving|readiness|probe|unhealthy)\b/.test(p);
  }

  function showAppTargetPicker({ prompt, modes, title }) {
    if (busy) return;
    enterChatState();
    const label = title || 'this scenario';
    const buildPrompt = (appId) => {
      const appPhrase = appId === 'both' ? 'both apps' : appId;
      if (modes?.length) {
        return `Simulate ${modesToSimulatePhrase(modes)} on ${appPhrase}`;
      }
      return rewritePromptForApp(prompt, appId);
    };
    const choices = [
      { label: 'FastAPI API', prompt: buildPrompt('fastapi') },
      { label: 'Nginx Web', prompt: buildPrompt('nginx') },
      { label: 'Both apps', prompt: buildPrompt('both') },
    ];
    appendMsg('bot', `Which workload should I apply **${label}** to?`, {
      ui: 'choices',
      choices,
      system: true,
    });
  }

  function handlePromptClick(btn) {
    const prompt = btn.getAttribute('data-prompt');
    if (!prompt) return;
    sendMessage(prompt);
  }

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
    const re = /```(\w*)\n?([\s\S]*?)```/g;
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
        return `<pre class="el-code-block">${esc(b.v.trim())}</pre>`;
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
      s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
      s = s.replace(/\x00LNK(\d+)\x00/g, (_, i) => anchors[+i] || '');
      s = s.replace(/^- (.+)$/gm, '<li>$1</li>');
      if (s.includes('<li>')) s = '<ul>' + s + '</ul>';
      s = s.replace(/\n\n/g, '</p><p>');
      if (!s.startsWith('<ul') && !s.startsWith('<p')) s = '<p>' + s + '</p>';
      return s;
    }).join('');
  }

  function renderInjectSummary(items) {
    if (!items?.length) return '';
    const cards = items.map(s => {
      const modes = (s.modes || []).map(m =>
        `<span class="el-inject-mode">${esc(m.label || m.id || m)}</span>`
      ).join('');
      const expected = s.expected_summary
        ? `<p class="el-inject-expected">${esc(s.expected_summary)}</p>`
        : (s.expected_signals?.length
          ? `<p class="el-inject-expected">Expected: ${esc(s.expected_signals.join(', '))}</p>` : '');
      const podLine = String(s.pod_line || '');
      const podRunning = /running/i.test(podLine) && /\d+\/\d+/.test(podLine);
      const serviceNote = (s.service_level || (s.injected && podRunning))
        ? '<p class="el-inject-expected">Note: pod may stay Running — outage is at network/service level. Open app URL to see 502/timeout.</p>'
        : '';
      const badge = (s.injected || !s.healthy)
        ? '<span class="el-inject-badge">Outage active</span>'
        : '<span class="el-inject-badge">Still healthy</span>';
      const errors = (s.container_errors?.length
        ? s.container_errors.map(e =>
            `<div class="el-inject-err"><strong>${esc(e.container)}</strong> · ${esc(e.reason)}</div>`
          ).join('')
        : `<code class="el-inject-pod">${esc(podLine || 'checking pods…')}</code>`);
      const openLink = s.links?.dashboard
        ? `<div class="el-inject-actions"><a href="${esc(s.links.dashboard)}" target="_blank" rel="noopener noreferrer">Open app</a>`
          + (s.links?.argocd_app ? ` · <a href="${esc(s.links.argocd_app)}" target="_blank" rel="noopener noreferrer">Argo CD</a>` : '')
          + `</div>`
        : '';
      return `<div class="el-inject-card">
        <div class="el-inject-card-top"><strong>${esc(s.label || s.app)}</strong>${badge}</div>
        <div class="el-inject-modes">${modes}</div>
        ${expected}${serviceNote}
        <div>${errors}</div>
        ${openLink}
      </div>`;
    }).join('');
    return `<div class="el-inject-deck"><div class="el-inject-deck-label">Failure injected</div><div class="el-inject-grid">${cards}</div></div>`;
  }

  function renderFailureCatalog(catalog) {
    if (!catalog?.by_category) return '';
    const catLabels = {
      pod: 'Pod', deployment: 'Deployment', network: 'Network',
      node: 'Node', storage: 'Storage', application: 'Application',
    };
    const sections = Object.entries(catalog.by_category).map(([cat, items]) => {
      const cards = (items || []).map(m => `
        <div class="el-fail-card">
          <div class="el-fail-card-top">
            <strong>${esc(m.label)}</strong>
            <code class="el-fail-id">${esc(m.id)}</code>
          </div>
          <p class="el-fail-blurb">${esc(m.layman || m.blurb || '')}</p>
          <div class="el-fail-try-row">
            <button type="button" class="el-fail-try" data-choice-prompt="${esc(m.sample_prompt || `Simulate ${m.id} on fastapi`)}">
              Try on FastAPI
            </button>
            <button type="button" class="el-fail-try" data-choice-prompt="${esc(m.sample_prompt_nginx || `Simulate ${m.id} on nginx`)}">
              Try on Nginx
            </button>
          </div>
        </div>`).join('');
      return `<div class="el-fail-section">
        <div class="el-fail-section-title">${esc(catLabels[cat] || cat)} (${items.length})</div>
        <div class="el-fail-grid">${cards}</div>
      </div>`;
    }).join('');
    return `<div class="el-fail-deck">
      <div class="el-fail-deck-label">${esc(catalog.count || 40)} failure modes — click Try to inject</div>
      ${sections}
    </div>`;
  }

  function renderHealSummary(items) {
    if (!items?.length) return '';
    const icons = { ok: '✓', warn: '◷', bad: '✗' };
    const cards = items.map(s => {
      const key = s.status_key === 'ok' ? 'ok' : s.status_key === 'warn' ? 'warn' : 'bad';
      const gitops = s.gitops
        ? `<div class="el-heal-meta">GitOps <span>${esc(s.gitops)}</span></div>` : '';
      const links = [];
      if (s.links?.dashboard) links.push(`<a href="${esc(s.links.dashboard)}" target="_blank" rel="noopener noreferrer">Open app</a>`);
      if (s.links?.argocd_app) links.push(`<a href="${esc(s.links.argocd_app)}" target="_blank" rel="noopener noreferrer">Argo CD</a>`);
      if (s.links?.health) links.push(`<a href="${esc(s.links.health)}" target="_blank" rel="noopener noreferrer">Health</a>`);
      const actions = links.length ? `<div class="el-heal-actions">${links.join('')}</div>` : '';
      return `<div class="el-heal-card el-heal-card--${key}">
        <div class="el-heal-card-top">
          <span class="el-heal-icon" aria-hidden="true">${icons[key] || '·'}</span>
          <div>
            <strong>${esc(s.label)}</strong>
            <span class="el-heal-status">${esc(s.status)}</span>
          </div>
        </div>
        <code class="el-heal-pod">${esc(s.pod_line)}</code>
        ${gitops}
        ${s.detail ? `<p class="el-heal-detail">${esc(s.detail)}</p>` : ''}
        ${actions}
      </div>`;
    }).join('');
    return `<div class="el-heal-deck"><div class="el-heal-deck-label">Recovery summary</div><div class="el-heal-grid">${cards}</div></div>`;
  }

  function renderStatusCards(apps) {
    if (!apps?.length) return '';
    const cards = apps.map(a => {
      const key = a.injected ? 'bad' : (
        a.state_key === 'ok' ? 'ok'
        : a.state_key === 'warn' ? 'warn'
        : a.state_key === 'bad' ? 'bad' : 'idle');
      const stateLabel = a.injected ? (a.state || 'Outage active') : a.state;
      const gitops = a.gitops
        ? `<div class="el-status-meta">GitOps <span>${esc(a.gitops)}</span></div>` : '';
      const links = [];
      if (a.links?.dashboard) links.push(`<a href="${esc(a.links.dashboard)}" target="_blank" rel="noopener noreferrer">Open app</a>`);
      if (a.links?.argocd_app) links.push(`<a href="${esc(a.links.argocd_app)}" target="_blank" rel="noopener noreferrer">Argo CD</a>`);
      if (a.links?.health) links.push(`<a href="${esc(a.links.health)}" target="_blank" rel="noopener noreferrer">Health</a>`);
      const actions = links.length ? `<div class="el-status-actions">${links.join('')}</div>` : '';
      return `<div class="el-status-card el-status-card--${key}">
        <div class="el-status-card-top">
          <strong>${esc(a.label)}</strong>
          <span class="el-status-pill el-status-pill--${key}">${esc(stateLabel)}</span>
        </div>
        ${a.blurb ? `<p class="el-status-blurb">${esc(a.blurb)}</p>` : ''}
        <code class="el-status-pod">${esc(a.pod_line)}</code>
        ${gitops}
        ${actions}
      </div>`;
    }).join('');
    return `<div class="el-status-deck"><div class="el-status-deck-label">Live app health</div><div class="el-status-grid">${cards}</div></div>`;
  }

  function renderChoicesPanel(choices) {
    if (!choices?.length) return '';
    return `<div class="el-choice-deck" role="group" aria-label="Follow-up choices">
      <div class="el-choice-label">Pick an action</div>
      <div class="el-choice-panel">
      ${choices.map(c => `
        <button type="button" class="el-choice-chip" data-choice-prompt="${esc(c.prompt)}">
          ${esc(c.label)}
        </button>`).join('')}
      </div>
    </div>`;
  }

  function typingDotsHtml(label) {
    const text = label || 'Thinking…';
    return `<div class="el-typing-dots" aria-hidden="true"><span></span><span></span><span></span></div>
      <span class="el-typing-text">${esc(text)}</span>`;
  }

  function moveComposeToChat() {
    const bar = document.getElementById('holmes-form');
    if (bar && composeChat && !composeChat.contains(bar)) {
      composeChat.appendChild(bar);
    }
  }

  function enterChatState() {
    if (chatStarted) return;
    chatStarted = true;
    if (heroStateEl) heroStateEl.hidden = true;
    if (chatStateEl) chatStateEl.hidden = false;
    if (pageEl) pageEl.classList.add('el-chat-started');
    moveComposeToChat();
    window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
    if (input) {
      input.placeholder = 'Ask anything, e.g. show pod status, simulate image pull on nginx...';
    }
  }

  function seedWelcomeIfNeeded() {
    if (seededWelcome) return;
    seededWelcome = true;
    appendMsg('bot', FIRST_BOT_MESSAGE, { system: true });
  }

  function isNearBottom(el, threshold = 96) {
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  }

  function scrollToBottom(force) {
    const scrollEl = messagesPane || messagesEl;
    if (!scrollEl) return;
    requestAnimationFrame(() => {
      if (force || isNearBottom(scrollEl)) {
        scrollEl.scrollTop = scrollEl.scrollHeight;
      }
    });
  }

  function isLightweightMessage(msg) {
    const m = String(msg || '').trim().toLowerCase();
    return /^(hi+|hey+|hello+|howdy+|yo+|sup+)(\s+there)?[!?.]*$/.test(m)
      || /\b(pod status|show status|cluster status|how many apps|are my apps healthy)\b/.test(m)
      || /\b(show|cluster)\b.*\b(both apps|all apps|all workloads)\b/.test(m);
  }

  function showLoadingBubble(msg) {
    enterChatState();
    removeLoadingBubble();
    liveSteps = [];
    const label = isLightweightMessage(msg) ? 'Checking cluster…' : 'Working on cluster…';
    const row = document.createElement('div');
    row.className = 'el-msg-row el-msg-row--bot';
    row.id = 'ccd-loading-bubble';
    row.innerHTML = `<div class="el-msg-bot el-typing">${typingDotsHtml(label)}</div>`;
    messagesEl.appendChild(row);
    loadingEl = row;
    scrollToBottom(true);
  }

  function setLoadingText(text) {
    const label = loadingEl?.querySelector('.el-typing-text');
    if (label && text) label.textContent = text;
  }

  function removeLoadingBubble() {
    loadingEl?.remove();
    loadingEl = null;
    document.getElementById('ccd-loading-bubble')?.remove();
  }

  function appendMsg(role, text, meta) {
    const isFirstUser = role === 'user' && !chatStarted;
    if (role === 'user') {
      enterChatState();
      if (!meta?.system) seedWelcomeIfNeeded();
    }

    const row = document.createElement('div');
    const isUser = role === 'user';
    const isError = role === 'error';
    row.className = `el-msg-row el-msg-row--${isUser ? 'user' : 'bot'}`;

    let extra = '';
    if (role === 'bot' && meta?.inject_summary?.length) {
      extra += renderInjectSummary(meta.inject_summary);
    } else if (role === 'bot' && meta?.heal_summary?.length) {
      extra += renderHealSummary(meta.heal_summary);
    } else if (role === 'bot' && meta?.apps_status?.length) {
      extra += renderStatusCards(normalizeAppsStatus(meta.apps_status));
    }
    if (role === 'bot' && meta?.ui === 'choices' && meta?.choices) {
      extra += renderChoicesPanel(meta.choices);
    }
    if (role === 'bot' && meta?.ui === 'failure_catalog' && meta?.failure_catalog) {
      extra += renderFailureCatalog(meta.failure_catalog);
    }

    const foot = role === 'bot' && meta?.degraded
      ? `<p style="margin-top:8px;font-size:12px;color:#B45309">Fallback — ${esc(meta?.gemini_error?.user_message || 'AI unavailable')}</p>`
      : '';

    const body = isError ? esc(text) : formatReply(text);
    const bubbleCls = isError ? 'el-msg-error' : (isUser ? 'el-msg-user' : 'el-msg-bot');

    if (isUser) {
      row.innerHTML = `<div class="${bubbleCls}">${body}</div>`;
    } else {
      row.innerHTML = `<div class="el-bot-block"><div class="${bubbleCls}">${body}${foot}</div>${extra}</div>`;
    }
    messagesEl.appendChild(row);
    if (!meta?.system) markChatHasMessages();

    row.querySelectorAll('[data-choice-prompt]').forEach(btn => {
      btn.addEventListener('click', () => sendMessage(btn.getAttribute('data-choice-prompt')));
    });

    scrollToBottom(true);

    if ((role === 'user' || role === 'bot') && !meta?.system) {
      chatHistory.push({ role: role === 'user' ? 'user' : 'assistant', content: text });
      if (chatHistory.length > 16) chatHistory.splice(0, chatHistory.length - 16);
    }

    if (isFirstUser && role === 'user') {
      /* welcome seeded above before user bubble */
    }
  }

  function setSendSpinner(on) {
    if (!sendBtn) return;
    sendBtn.classList.toggle('el-send-btn--busy', on);
    if (on) {
      sendBtn.innerHTML = '<span class="el-spin-icon"></span>';
    } else {
      sendBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
        <path d="M3 11L21 3L13 21L11 13L3 11Z" fill="currentColor"/>
      </svg>`;
    }
  }

  function setBusy(on) {
    busy = on;
    syncSendButton();
    input.disabled = on;
    setSendSpinner(on);
    document.querySelectorAll('.el-quick-btn').forEach(b => { b.disabled = on; });
    if (on) {
      refreshClusterStrip();
      stripPoll = setInterval(refreshClusterStrip, 3000);
    } else if (stripPoll) {
      clearInterval(stripPoll);
      stripPoll = null;
    }
  }

  function syncSendButton() {
    const ready = !busy && input.value.trim().length > 0;
    sendBtn.disabled = !ready;
    sendBtn.classList.toggle('el-send-btn--ready', ready);
  }

  function normalizeAppsStatus(apps) {
    if (!apps?.length) return [];
    return apps.map(a => {
      if (a.injected) {
        return { ...a, state_key: 'bad', state: a.state || 'Outage active', healthy: false };
      }
      const state_key = a.state_key || (a.healthy ? 'ok' : (a.deployed === false ? 'idle' : 'bad'));
      const state = a.state || (state_key === 'ok' ? 'Healthy' : state_key === 'warn' ? 'Recovering' : state_key === 'bad' ? 'Unhealthy' : 'Not deployed');
      return { ...a, state_key, state, healthy: state_key === 'ok' };
    });
  }

  async function fetchAppsStatus() {
    try {
      const j = await (await fetch('/api/holmes/snapshot')).json();
      const snap = j.data || j;
      return normalizeAppsStatus(snap.apps || []);
    } catch (_) {
      return [];
    }
  }

  function shouldAttachStatusCards(reply, source) {
    const r = String(reply || '').toLowerCase();
    const clusterSources = new Set(['agent', 'action', 'telemetry', 'gemini']);
    if (!clusterSources.has(source)) return false;
    if (/\b(outage active|failure injected|simulate|injected|active on fastapi|active on nginx|active on both)\b/.test(r)) {
      return false;
    }
    return /\b(nginx|fastapi|healthy|unhealthy|pod|argocd|gitops|deployed|recovered|auto-?fix|status|replica)\b/.test(r);
  }

  async function refreshClusterStrip() {
    try {
      const j = await (await fetch('/api/holmes/snapshot')).json();
      if (!j.ok) return;
      const snap = j.data || j;
      const apps = snap.apps || [];
      const healthy = apps.filter(a => a.healthy && !a.injected).length;
      const critical = apps.filter(a => a.deployed && (!a.healthy || a.injected)).length;
      const ns = snap.namespace || 'enlight-staging';
      if (clusterStrip) {
        clusterStrip.textContent = critical
          ? `${ns} · ${critical} critical · ${healthy}/${apps.length} healthy`
          : `${ns} · ${healthy}/${apps.length} healthy`;
      }
      if (clusterDot) {
        clusterDot.className = 'el-cluster-dot' + (
          critical ? ' el-cluster-dot--bad' : healthy < apps.length ? ' el-cluster-dot--warn' : ''
        );
      }
    } catch (_) { /* optional */ }
  }

  function streamDeadlineMs(msg) {
    const m = String(msg || '').toLowerCase();
    if (/\b(auto-?fix|self-?heal|heal|fix both|fix my|recover)\b/.test(m)) return 540000;
    return 360000;
  }

  const HIDDEN_STEP_RE = /clearing prior|pause gitops|preparing .* simulation|gitops for injection/i;

  function pushLiveStep(step) {
    if (!step?.title || isTechnicalStep(step)) return;
    if (HIDDEN_STEP_RE.test(step.title)) {
      setLoadingText('Applying failure scenario…');
      return;
    }
    const label = step.detail ? `${step.title} — ${step.detail}` : step.title;
    setLoadingText(label);
    liveSteps.push({ ...step, status: step.status || 'running' });
  }

  async function streamChat(msg, history) {
    const resp = await fetch('/api/holmes/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history, mode: CHAT_MODE }),
    });
    if (resp.status === 404) return null;
    if (!resp.ok || !resp.body) throw new Error('Stream failed (HTTP ' + resp.status + ')');

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let result = null;
    const deadline = Date.now() + streamDeadlineMs(msg);

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
        if (data.type === 'step' && data.step) {
          if (!DONE_STEP_TITLES.has(data.step.title)) setLoadingText(data.step.title);
          pushLiveStep(data.step);
        } else if (data.type === 'complete') {
          result = data.data;
        } else if (data.type === 'error') {
          throw new Error(data.error);
        }
      }
    }
    return result;
  }

  async function postChat(msg, history) {
    const resp = await fetch('/api/holmes/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, history, mode: CHAT_MODE }),
    });
    const j = await resp.json();
    if (!j.ok) throw new Error(j.error || 'Request failed');
    return j.data;
  }

  async function sendMessage(text) {
    const msg = String(text || '').trim();
    if (!msg || busy) return null;

    if (needsAppPicker(msg)) {
      showAppTargetPicker({ prompt: msg });
      return null;
    }

    const history = chatHistory.slice();
    appendMsg('user', msg);
    input.value = '';
    syncSendButton();
    setBusy(true);
    const isAutoFix = /\bauto-?fix\b/i.test(msg);
    showLoadingBubble(msg);
    if (isAutoFix) setLoadingText('Checking cluster health…');

    try {
      if (isAutoFix) {
        await new Promise(r => setTimeout(r, 1100));
        const apps = await fetchAppsStatus();
        const deployed = apps.filter(a => a.deployed !== false);
        const targetApp = extractAppTarget(msg);
        const scopedDeployed = targetApp && targetApp !== 'all'
          ? deployed.filter(a => a.id === targetApp)
          : deployed;
        const needy = scopedDeployed.filter(a => !a.healthy || a.injected);
        if (scopedDeployed.length && !needy.length) {
          removeLoadingBubble();
          const lines = scopedDeployed.map(a =>
            `- **${a.label}** — ${a.state || 'Healthy'} · \`${a.pod_line}\``,
          );
          const headline = scopedDeployed.length === 1
            ? '**1 of 1 demo applications are healthy**'
            : `**All demo apps are already healthy** (${scopedDeployed.length}/${scopedDeployed.length})`;
          appendMsg('bot',
            `${headline}.\n\n`
            + 'No auto-fix needed — pods are Running and there is no active chaos.\n\n'
            + lines.join('\n'),
            { apps_status: scopedDeployed, ui: 'status_cards' },
          );
          return { ok: true };
        }
        setLoadingText('Working on cluster…');
      }

      let data = await streamChat(msg, history);
      if (!data) data = await postChat(msg, history);
      removeLoadingBubble();
      if (data?.ok) {
        let appsStatus = null;
        const actionTarget = data.action_target || data.target || extractAppTarget(msg);
        const hasScopedSummary = !!(data.inject_summary?.length || data.heal_summary?.length);
        if (!hasScopedSummary) {
          appsStatus = data.apps_status;
          if (appsStatus?.length && actionTarget && actionTarget !== 'all') {
            appsStatus = appsStatus.filter(a => a.id === actionTarget);
          }
          if (!appsStatus?.length && shouldAttachStatusCards(data.reply, data.source)) {
            appsStatus = await fetchAppsStatus();
            if (actionTarget && actionTarget !== 'all') {
              appsStatus = appsStatus.filter(a => a.id === actionTarget);
            }
          }
        }
        appendMsg('bot', data.reply || '(empty)', {
          degraded: data.degraded,
          gemini_error: data.gemini_error,
          ui: data.ui || (appsStatus?.length ? 'status_cards' : (data.inject_summary?.length ? 'inject_summary' : undefined)),
          choices: data.choices,
          heal_summary: data.heal_summary,
          inject_summary: data.inject_summary,
          failure_catalog: data.failure_catalog,
          apps_status: appsStatus,
        });
        return data;
      }
      appendMsg('error', data?.error || 'Request failed');
      return null;
    } catch (e) {
      removeLoadingBubble();
      appendMsg('error', String(e.message || e));
      return null;
    } finally {
      setBusy(false);
      refreshClusterStrip();
      syncSendButton();
      input.focus();
    }
  }

  async function runClientDemo() {
    if (busy) return;
    const acts = [
      { prompt: 'Simulate image pull failure on fastapi', wait: 2500 },
      { prompt: 'What actually broke? Explain in plain English.', wait: 2000 },
      { prompt: 'Auto-fix fastapi', wait: 0 },
    ];
    for (const act of acts) {
      const result = await sendMessage(act.prompt);
      if (!result?.ok && act.prompt.includes('Auto-fix')) break;
      if (act.wait) await new Promise(r => setTimeout(r, act.wait));
    }
  }

  form.addEventListener('submit', e => {
    e.preventDefault();
    sendMessage(input.value);
  });

  input.addEventListener('input', syncSendButton);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  document.querySelectorAll('[data-prompt]').forEach(btn => {
    btn.addEventListener('click', () => handlePromptClick(btn));
  });

  document.getElementById('client-demo-btn-hero')?.addEventListener('click', () => runClientDemo());
  document.getElementById('client-demo-btn-chat')?.addEventListener('click', () => runClientDemo());

  document.getElementById('cluster-status-btn')?.addEventListener('click', async () => {
    if (busy) return;
    const apps = await fetchAppsStatus();
    const deployed = apps.filter(a => a.deployed !== false);
    const needy = deployed.filter(a => !a.healthy || a.injected);
    enterChatState();
    if (deployed.length && !needy.length) {
      seedWelcomeIfNeeded();
      appendMsg('user', 'Are my apps healthy?');
      setBusy(true);
      showLoadingBubble('Are my apps healthy?');
      setLoadingText('Checking cluster…');
      await new Promise(r => setTimeout(r, 700));
      removeLoadingBubble();
      const lines = deployed.map(a =>
        `- **${a.label}** — ${a.state || 'Healthy'} · \`${a.pod_line}\``,
      );
      appendMsg('bot',
        `**Yes — ${deployed.length}/${deployed.length} apps are healthy.**\n\n`
        + lines.join('\n')
        + '\n\nSay **simulate outage** to run a demo failure, or **Run client demo** for the full story.',
        { apps_status: deployed, ui: 'status_cards' },
      );
      setBusy(false);
      refreshClusterStrip();
      return;
    }
    sendMessage('Show pod status for all workloads');
  });

  syncSendButton();
  refreshClusterStrip();

  const urlPrompt = new URLSearchParams(window.location.search).get('prompt');
  const urlDemo = new URLSearchParams(window.location.search).get('demo');
  if (urlPrompt) {
    window.history.replaceState({}, '', window.location.pathname);
    sendMessage(urlPrompt);
  } else if (urlDemo === 'client') {
    window.history.replaceState({}, '', window.location.pathname);
    runClientDemo();
  }
})();
