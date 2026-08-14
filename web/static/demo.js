/* EnlightLab guided self-heal demo — live SSE + ops theater */
(() => {
  let busy = false;
  let wizardStep = 1;
  let stepCount = 0;
  let runningTlId = null;
  let resetLockUntil = 0;

  const APP_CONFIG = {
    fastapi: {
      id: 'fastapi',
      label: 'FastAPI API',
      argocdName: 'fastapi-staging',
      deployLabel: 'Deploy fastapi-staging',
      resetLabel: 'Reset — remove fastapi-staging',
    },
    nginx: {
      id: 'nginx',
      label: 'Nginx Web',
      argocdName: 'nginx-staging',
      deployLabel: 'Deploy nginx-staging',
      resetLabel: 'Reset — remove nginx-staging',
    },
  };

  const DEMO_APP_KEY = 'enlightlab.demoApp';
  let selectedDemoApp = 'fastapi';

  function currentApp() {
    return APP_CONFIG[selectedDemoApp] || APP_CONFIG.fastapi;
  }

  function wizardStorageKey() {
    return `enlightlab.demoWizardStep.${selectedDemoApp}`;
  }

  function stepLabels() {
    const a = currentApp();
    return ['', a.deployLabel, 'Simulate outage', 'Explain with AI (HolmesGPT)', 'Auto-fix'];
  }

  function isClean(d) {
    return !!(d.app_clean ?? d.staging_clean);
  }

  function isDeployed(d) {
    return !!(d.app_deployed ?? d.staging_deployed);
  }

  function statusMessage(d) {
    return d.app_status_message || d.staging_status_message || '';
  }

  function appStatusUrl() {
    return `/api/status?demo_app=${encodeURIComponent(selectedDemoApp)}`;
  }

  function appStreamUrl(action) {
    return `/api/apps/${encodeURIComponent(selectedDemoApp)}/${action}/stream`;
  }

  const PHASE_ORDER = ['git', 'argocd', 'k8s', 'break', 'ai', 'health'];

  const logEl = document.getElementById('log');
  const timelineEl = document.getElementById('timeline');
  const theater = document.getElementById('opsTheater');
  const liveRunner = document.getElementById('liveRunner');
  const runnerIdle = document.getElementById('runnerIdle');
  const runnerPip = document.getElementById('runnerPip');
  const opsTitle = document.getElementById('opsCurrentTitle');
  const opsDetail = document.getElementById('opsCurrentDetail');
  const opsSpin = document.getElementById('opsSpin');
  const opsBar = document.getElementById('opsProgressBar');
  let runningStepNum = 0;
  let demoInProgress = false;
  let statusFailCount = 0;
  let autoDeployAttempted = { fastapi: false, nginx: false };
  let autoAdvancedDeploy = { fastapi: false, nginx: false };
  let autoDeployEnabled = true;
  let resetMode = false;

  function saveWizardStep(n) {
    try { sessionStorage.setItem(wizardStorageKey(), String(n)); } catch (_) {}
  }

  function loadWizardStep() {
    try {
      const v = parseInt(sessionStorage.getItem(wizardStorageKey()), 10);
      return Number.isFinite(v) ? Math.max(1, Math.min(5, v)) : 1;
    } catch (_) { return 1; }
  }

  function clearWizardStep() {
    try { sessionStorage.removeItem(wizardStorageKey()); } catch (_) {}
  }

  function minimumStepFromCluster(d) {
    if (isClean(d)) return 1;
    if (isDeployed(d)) return 2;
    if (!d.argocd_app_exists && d.workloads_exist) return 1;
    if (!isDeployed(d) && d.argocd_app_exists && (d.app_health_check === 'fail' || d.app_health === 'fail' || !String(d.pod || '').includes('1/1'))) {
      return 3;
    }
    return 1;
  }

  function syncTheaterForStep(step, d) {
    if (step >= 5) activatePhase('health', 'done');
    else if (step >= 4) activatePhase('ai', 'done');
    else if (step >= 3 && !isDeployed(d)) activatePhase('break', 'break');
    else if (step >= 2 || isDeployed(d)) activatePhase('health', 'done');
  }

  function reconcileWizardStep(d) {
    if (busy || demoInProgress) return;
    if (resetLockUntil && Date.now() < resetLockUntil) {
      setWizardStep(1);
      return;
    }
    const saved = loadWizardStep();
    const floor = minimumStepFromCluster(d);
    let step;
    if (isClean(d)) {
      step = 1;
      clearWizardStep();
    } else {
      step = Math.max(saved, floor);
    }
    if (step !== wizardStep) setWizardStep(step);
    else saveWizardStep(step);
    syncTheaterForStep(step, d);
  }

  function updateAppChrome() {
    const app = currentApp();
    document.querySelectorAll('.demo-app-tab').forEach(btn => {
      const on = btn.dataset.app === selectedDemoApp;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    const wiz1Title = document.getElementById('wiz1Title');
    if (wiz1Title) wiz1Title.textContent = app.deployLabel;
    const btnDeploy = document.getElementById('btnDeploy');
    if (btnDeploy && !btnDeploy.hidden) btnDeploy.textContent = app.deployLabel + ' →';
    const btnReset = document.getElementById('btnReset');
    if (btnReset) btnReset.textContent = app.resetLabel;
    const resetHelp = document.getElementById('resetHelp');
    if (resetHelp) resetHelp.innerHTML = `Reset removes <strong>${app.argocdName}</strong> from Argo CD <strong>and</strong> deletes workloads so Step 1 is a true deploy from zero.`;
    const liveTitle = document.getElementById('demoAlreadyLiveTitle');
    if (liveTitle) liveTitle.textContent = `${app.argocdName} is already in Argo CD`;
    const resetHint = document.getElementById('demoResetHint');
    if (resetHint) resetHint.textContent = app.resetLabel;
    try { sessionStorage.setItem(DEMO_APP_KEY, selectedDemoApp); } catch (_) {}
  }

  function switchDemoApp(appId) {
    if (!APP_CONFIG[appId] || appId === selectedDemoApp) return;
    if (busy || demoInProgress) {
      log(`Finish the current ${currentApp().label} step before switching apps`, 'warn');
      return;
    }
    selectedDemoApp = appId;
    wizardStep = loadWizardStep();
    updateAppChrome();
    hideExplainReport();
    statusFailCount = 0;
    setWizardStep(wizardStep);
    refreshStatus();
  }

  function focusRunner() {
    liveRunner?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    liveRunner?.classList.add('live-runner--focus');
    setTimeout(() => liveRunner?.classList.remove('live-runner--focus'), 1200);
  }

  function updateStepLive(stepNum, title, detail, on) {
    const resetEl = document.getElementById('resetLive');
    if (resetEl && (resetMode || stepNum === 0)) {
      if (on) {
        resetEl.hidden = false;
        resetEl.innerHTML = `<span class="step-live-spin"></span><span><strong>${esc(title)}</strong>${detail ? ' — ' + esc(detail) : ''}</span>`;
      } else if (!resetMode) {
        resetEl.hidden = true;
        resetEl.innerHTML = '';
      }
    }
    [1, 2, 3, 4].forEach(i => {
      const el = document.getElementById('stepLive' + i);
      if (!el) return;
      if (on && i === stepNum) {
        el.hidden = false;
        el.innerHTML = `<span class="step-live-spin"></span><span><strong>${esc(title)}</strong>${detail ? ' — ' + esc(detail) : ''}</span>`;
      } else {
        el.hidden = true;
        el.innerHTML = '';
      }
    });
  }

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function log(msg, cls = '') {
    const line = document.createElement('div');
    line.className = cls;
    line.textContent = new Date().toLocaleTimeString() + ' — ' + msg;
    logEl.prepend(line);
  }

  function resolveDemoUrl(url) {
    if (!url || url === '#') return '';
    const s = String(url).trim();
    if (s.startsWith('/')) return `${window.location.origin}${s}`;
    try {
      const p = new URL(s);
      if (!/^https?:\/\//i.test(s) || !p.hostname) return '';
      if (/localhost|example\.com|cluster\.local/i.test(p.hostname)) return '';
      return s;
    } catch (_) { return ''; }
  }

  function isValidDemoUrl(url) {
    return !!resolveDemoUrl(url);
  }

  function applyLinks(links) {
    if (!links) return;
    ['linkGitOps:argocd', 'linkAppHealth:app_health', 'linkAppDashboard:app_dashboard', 'linkArgoApp:argocd_app'].forEach(pair => {
      const [id, key] = pair.split(':');
      const el = document.getElementById(id);
      const url = resolveDemoUrl(links[key]);
      if (!el) return;
      if (!url) { el.href = '#'; el.dataset.disabled = '1'; el.setAttribute('aria-disabled', 'true'); }
      else { el.href = url; delete el.dataset.disabled; el.removeAttribute('aria-disabled'); }
    });
  }

  function setOpenLink(url) {
    const link = document.getElementById('openLink');
    if (!link) return;
    link.style.display = isValidDemoUrl(url) ? 'inline-block' : 'none';
    if (isValidDemoUrl(url)) link.href = url;
  }

  document.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-disabled="1"]');
    if (!a) return;
    e.preventDefault();
  });

  function setBusy(on) {
    busy = on;
    const badge = document.getElementById('liveBadge');
    if (on) {
      badge.textContent = 'Live…';
      badge.className = 'badge busy';
      theater.classList.add('active');
      liveRunner?.classList.add('active');
      runnerIdle && (runnerIdle.textContent = resetMode ? currentApp().resetLabel : (stepLabels()[runningStepNum] || 'Running…'));
      runnerPip?.classList.add('on');
      opsSpin.style.display = 'inline-block';
    } else {
      theater.classList.remove('active');
      liveRunner?.classList.remove('active');
      runnerIdle && (runnerIdle.textContent = 'Click a step above to start');
      runnerPip?.classList.remove('on');
      opsSpin.style.display = 'none';
      updateStepLive(0, '', '', false);
      const resetEl = document.getElementById('resetLive');
      if (resetEl) { resetEl.hidden = true; resetEl.innerHTML = ''; }
      runningStepNum = 0;
    }
    document.querySelectorAll('.wizard-step .action-btn, #btnReset').forEach(b => {
      if (b.id === 'btnReset') b.disabled = on;
      else if (!b.closest('.wizard-step.locked')) b.disabled = on || (wizardStep < 5 && Number(b.closest('.wizard-step')?.dataset.step) > wizardStep);
    });
  }

  function setWizardStep(n) {
    wizardStep = Math.max(1, Math.min(5, n));
    saveWizardStep(wizardStep);
    document.getElementById('stepBadge').textContent =
      wizardStep >= 5 ? 'Demo complete ✓' : `Step ${wizardStep} — ${stepLabels()[wizardStep]}`;
    [1, 2, 3, 4].forEach(i => {
      const el = document.getElementById('wiz' + i);
      if (!el) return;
      el.classList.remove('active', 'done', 'locked');
      if (wizardStep >= 5) el.classList.add('done');
      else if (i < wizardStep) el.classList.add('done');
      else if (i === wizardStep) el.classList.add('active');
      else el.classList.add('locked');
      const btn = el.querySelector('.action-btn:not(#btnContinue)');
      if (btn) btn.disabled = busy || (wizardStep < 5 && i > wizardStep);
    });
    const btnContinue = document.getElementById('btnContinue');
    if (btnContinue) btnContinue.disabled = busy || wizardStep > 2;
  }

  function applyDeployedState(d) {
    const deployed = isDeployed(d);
    const app = currentApp();
    const detected = document.getElementById('deployDetected');
    const btnDeploy = document.getElementById('btnDeploy');
    const btnContinue = document.getElementById('btnContinue');
    const wiz1Desc = document.getElementById('wiz1Desc');

    if (wizardStep === 1 && !busy && !demoInProgress && deployed
        && !autoAdvancedDeploy[selectedDemoApp]
        && !(resetLockUntil && Date.now() < resetLockUntil)) {
      autoAdvancedDeploy[selectedDemoApp] = true;
      setWizardStep(2);
      activatePhase('health', 'done');
    }

    if (deployed) {
      const banner = document.getElementById('demoAlreadyLive');
      if (banner) banner.hidden = false;
      if (detected) {
        detected.hidden = false;
        detected.textContent = `✓ ${app.argocdName} live — Argo CD ${d.gitops_app || 'Healthy'}`;
      }
      if (wiz1Desc) wiz1Desc.textContent = `Detected from cluster — skip deploy unless you ${app.resetLabel} first.`;
      if (btnDeploy) btnDeploy.hidden = true;
      if (btnContinue) btnContinue.hidden = wizardStep > 2;
      if (wizardStep <= 2 && !busy && !isClean(d)) {
        const msg = statusMessage(d) || `${app.label} is live. Continue to Step 2 — Simulate outage.`;
        showResult(msg, 'ok');
        opsTitle.textContent = `${app.label} already deployed`;
        opsDetail.textContent = `Argo CD ${d.gitops_app || 'Healthy'} · health checks pass. Use Step 2 to simulate an outage.`;
        if (runnerIdle && !liveRunner?.classList.contains('active')) {
          runnerIdle.textContent = 'App live — ready for Step 2';
        }
      }
    } else if (!busy && !demoInProgress) {
      const banner = document.getElementById('demoAlreadyLive');
      if (banner) banner.hidden = true;
      if (detected) detected.hidden = true;
      if (btnDeploy) { btnDeploy.hidden = false; btnDeploy.textContent = app.deployLabel + ' →'; }
      if (btnContinue) btnContinue.hidden = true;
      if (wiz1Desc) wiz1Desc.textContent = `Registers ${app.argocdName} in Argo CD and syncs from Git.`;
      const clean = isClean(d);
      const outage = !clean && (d.workloads_exist || d.argocd_app_exists) && !deployed;
      if (outage && wizardStep >= 5) {
        // Heal finished — status poll may lag behind; keep the recovery outcome visible.
        return;
      }
      if (outage && wizardStep >= 3 && !(resetLockUntil && Date.now() < resetLockUntil)) {
        if (btnDeploy) btnDeploy.hidden = true;
      } else if (clean || (resetLockUntil && Date.now() < resetLockUntil)) {
        if (btnDeploy) btnDeploy.hidden = false;
      }
      const msg = statusMessage(d) || (
        outage
          ? 'Outage in progress — app is down on purpose. Continue to Step 3 Explain.'
          : clean
            ? 'Clean slate — app is down and not in Argo CD. Click Deploy for a full GitOps deploy.'
            : `${app.label} not deployed — click Deploy or wait for auto-deploy.`
      );
      showResult(msg, outage && !(resetLockUntil && Date.now() < resetLockUntil) ? 'err' : (clean ? 'warn' : ''));
      opsTitle.textContent = outage && !(resetLockUntil && Date.now() < resetLockUntil)
        ? 'Outage active — app down'
        : (clean ? 'App not registered — ready for deploy' : `${app.label} not deployed`);
      opsDetail.textContent = outage && !(resetLockUntil && Date.now() < resetLockUntil)
        ? `Argo CD ${d.gitops_app || 'Degraded'} · health down · use Step 3 Explain then Step 4 Auto-fix.`
        : (clean
          ? 'Not in Argo CD · no workloads · health down. Step 1 brings everything back from Git.'
          : 'Step 1 registers the Argo CD app and creates the workload from Git.');
    }
  }

  function onContinueToStep2() {
    setWizardStep(2);
    document.querySelector('#wiz2')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    log('Continuing to Step 2 — app already deployed', 'ok');
  }

  function resetTheater() {
    stepCount = 0;
    runningTlId = null;
    opsBar.style.width = '0%';
    document.querySelectorAll('.ops-node').forEach(n => n.classList.remove('active', 'done', 'break'));
    document.querySelectorAll('.ops-connector').forEach(c => c.classList.remove('active'));
  }

  function activatePhase(phase, mode) {
    const node = document.querySelector(`.ops-node[data-phase="${phase}"]`);
    if (!node) return;
    node.classList.remove('active', 'done', 'break');
    if (mode === 'break') node.classList.add('break');
    else if (mode === 'done') node.classList.add('done');
    else node.classList.add('active');

    const idx = PHASE_ORDER.indexOf(phase);
    document.querySelectorAll('.ops-connector').forEach((c, i) => {
      c.classList.toggle('active', i < idx);
    });
  }

  function handleLiveStep(step) {
    const ph = step.phase || 'k8s';
    const isRunning = step.status === 'running';
    const isTeardown = /remove|reset|tear down|unregister|prune|uninstall/i.test(step.title || '');
    const isBreak = !isTeardown && (
      ph === 'break' || /simulate outage|outage|bad image|crashloop|errimage/i.test(step.title || '')
    );

    opsTitle.textContent = step.title;
    opsDetail.textContent = step.detail || '';
    if (resetMode) {
      updateStepLive(0, step.title, step.detail, step.status === 'running');
    } else if (runningStepNum) {
      updateStepLive(runningStepNum, step.title, step.detail, step.status === 'running');
    }
    activatePhase(ph, isRunning && isBreak ? 'break' : isRunning ? 'active' : 'done');

    if (isRunning) {
      if (runningTlId) {
        const prev = document.getElementById(runningTlId);
        if (prev) { prev.classList.remove('running'); prev.querySelector('.tl-dot')?.classList.add('done-dot'); }
      }
      runningTlId = 'tl-' + Date.now();
      const div = document.createElement('div');
      div.id = runningTlId;
      div.className = 'tl-item running';
      div.innerHTML = `<span class="tl-dot"></span><div><div class="tl-title">${esc(step.title)}</div>${step.detail ? `<div class="tl-detail">${esc(step.detail)}</div>` : ''}</div>`;
      timelineEl.appendChild(div);
      timelineEl.scrollTop = timelineEl.scrollHeight;
    } else {
      let el = runningTlId ? document.getElementById(runningTlId) : null;
      if (el) {
        el.classList.remove('running');
        el.classList.add('done');
      } else {
        el = document.createElement('div');
        el.className = 'tl-item done';
        el.innerHTML = `<span class="tl-dot"></span><div><div class="tl-title">${esc(step.title)}</div>${step.detail ? `<div class="tl-detail">${esc(step.detail)}</div>` : ''}</div>`;
        timelineEl.appendChild(el);
      }
      runningTlId = null;
      stepCount++;
      opsBar.style.width = Math.min(95, stepCount * 12) + '%';
      timelineEl.scrollTop = timelineEl.scrollHeight;
    }
  }

  function showResult(mainText, cls, extraHtml, findingsHtml) {
    document.getElementById('resultMain').textContent = mainText;
    document.getElementById('resultMain').className = 'result-main ' + (cls || '');
    document.getElementById('resultExtra').innerHTML = extraHtml || '';
    document.getElementById('findings').innerHTML = findingsHtml || '';
  }

  function hideExplainReport() {
    const el = document.getElementById('explainReport');
    if (el) el.hidden = true;
  }

  function healthClass(h) {
    const s = String(h || '').toLowerCase();
    if (['healthy', 'running', 'synced'].includes(s)) return 'ok';
    if (['progressing', 'pending', 'missing'].includes(s)) return 'warn';
    return 'fail';
  }

  function renderExplain(d) {
    hideExplainReport();
    const report = document.getElementById('explainReport');
    if (!report) {
      showResult(d.summary || d.message, 'ok');
      return;
    }

    document.getElementById('explainHeadline').textContent = d.summary || d.message || 'Diagnosis complete';
    document.getElementById('explainSimple').textContent = d.simple_explanation || d.summary || '';

    const badges = document.getElementById('explainBadges');
    const tree = d.argocd_tree || {};
    badges.innerHTML = [
      d.root_cause ? `<span class="explain-badge cause">Root cause: ${esc(d.root_cause)}</span>` : '',
      d.k8sgpt_ok !== false ? `<span class="explain-badge sync">k8sgpt</span>` : '',
      d.holmes_ok ? `<span class="explain-badge holmes">HolmesGPT</span>` : '',
      tree.sync_status ? `<span class="explain-badge sync">Sync: ${esc(tree.sync_status)}</span>` : '',
      tree.health_status ? `<span class="explain-badge ${healthClass(tree.health_status)}">Health: ${esc(tree.health_status)}</span>` : '',
    ].join('');

    const holmesPanel = document.getElementById('holmesPanel');
    const holmesText = document.getElementById('holmesText');
    if (holmesPanel && holmesText) {
      const h = d.holmes_investigation || '';
      if (h) {
        holmesPanel.hidden = false;
        holmesText.textContent = h;
      } else if (d.holmes_enabled && d.holmes_raw) {
        holmesPanel.hidden = false;
        holmesText.textContent = 'HolmesGPT could not complete: ' + String(d.holmes_raw).slice(0, 600);
      } else {
        holmesPanel.hidden = true;
        holmesText.textContent = '';
      }
    }

    const bullets = document.getElementById('explainBullets');
    bullets.innerHTML = (d.what_happened || []).map(t => `<li>${esc(t)}</li>`).join('');

    document.getElementById('argocdTreeSummary').textContent =
      tree.tree_summary || 'Live view of the GitOps application and its Kubernetes resources.';

    const src = document.getElementById('argocdSourceRow');
    src.innerHTML = tree.source_repo ? `
      <img src="/static/assets/logos/github.svg" alt="" width="18" height="18" />
      <code>${esc(tree.source_repo)}</code>
      <span class="argocd-src-path">/${esc(tree.source_path || '')}</span>
      <span class="argocd-src-arrow">→</span>
      <img src="/static/assets/logos/kubernetes.svg" alt="" width="18" height="18" />
      <span>${esc(tree.destination_namespace || '')}</span>
    ` : '';

    const treeEl = document.getElementById('argocdResourceTree');
    treeEl.innerHTML = (tree.resources || []).map(r => {
      const hc = healthClass(r.health);
      const hl = r.highlight ? ' argo-node--highlight' : '';
      const detail = r.detail ? `<span class="argo-node-detail">${esc(r.detail)}</span>` : '';
      const sync = r.sync ? `<span class="argo-pill sync">${esc(r.sync)}</span>` : '';
      return `<div class="argo-node depth-${r.depth}${hl}" style="--depth:${r.depth}">
        <span class="argo-kind">${esc(r.kind)}</span>
        <span class="argo-name">${esc(r.name)}</span>
        ${detail}
        <span class="argo-pill ${hc}">${esc(r.health)}</span>
        ${sync}
      </div>`;
    }).join('');

    const tech = document.getElementById('explainTechList');
    const techItems = d.findings || [];
    tech.innerHTML = techItems.length
      ? techItems.map(t => `<div class="finding">${esc(t)}</div>`).join('')
      : '<div class="finding tip">No extra technical noise — plain-English summary above is based on live pod and deployment state.</div>';

    document.getElementById('explainNext').innerHTML = d.next_step
      ? `<strong>Recommended next step:</strong> ${esc(d.next_step)}`
      : '';

    const argoLink = document.getElementById('explainArgoLink');
    const cfgLinks = document.getElementById('linkArgoApp');
    if (argoLink && cfgLinks?.href && !cfgLinks.dataset.disabled) {
      argoLink.href = cfgLinks.href;
      argoLink.style.display = '';
    } else if (argoLink) {
      argoLink.style.display = 'none';
    }

    report.hidden = false;
    showResult('AI explain complete — scroll down for full client report', 'ok');
    report.scrollIntoView({ behavior: 'smooth', block: 'start' });

    document.getElementById('resultExtra').innerHTML = '';
    document.getElementById('findings').innerHTML = '';
  }

  async function maybeAutoDeploy(d) {
    const appId = selectedDemoApp;
    if (autoDeployAttempted[appId] || busy || demoInProgress) return;
    if (isDeployed(d) || d.cluster !== 'ok') return;
    if (!autoDeployEnabled) return;
    if (new URLSearchParams(location.search).get('auto_deploy') === '0') return;
    if (!isClean(d)) return;

    autoDeployAttempted[appId] = true;
    log(`Auto-deploy: clean slate for ${currentApp().label} — running Step 1`, 'info');
    opsTitle.textContent = `Auto-deploying ${currentApp().label}…`;
    opsDetail.textContent = 'Registering Argo CD app and syncing from Git';
    await onDeploy();
  }

  async function loadConfig() {
    try {
      const j = await (await fetch('/api/config')).json();
      if (!j.ok) return;
      const d = j.data || {};
      applyLinks(d.links);
      autoDeployEnabled = d.auto_deploy_on_load !== false;
      document.getElementById('argoUser').textContent = d.argocd_user || 'admin';
      const passEl = document.getElementById('argoPass');
      const hintEl = document.getElementById('argoPassHint');
      if (d.argocd_password_set && d.argocd_password) {
        passEl.textContent = d.argocd_password;
        hintEl.textContent = d.argocd_password_source === 'demo-login-secret'
          ? 'Synced from cluster — use Copy, then paste in Argo CD login.'
          : (d.argocd_password_hint || 'If login fails, run setup-argocd-demo-login.sh in Cloud Shell.');
      } else {
        passEl.textContent = '(not configured)';
        hintEl.textContent = d.argocd_password_hint || 'Run deploy/oci/setup-argocd-demo-login.sh in Cloud Shell.';
      }
    } catch (_) { /* ignore */ }
  }

  function renderStatus(d) {
    if (d.links) applyLinks(d.links);
    const pod = String(d.pod || '');
    const podOk = pod.includes('1/1') && pod.includes('Running');
    const healthOk = d.app_health === 'ok' || d.app_health_check === 'ok' || (podOk && isDeployed(d));
    const statColor = (st) => (st === 'ok' ? 'var(--green)' : st === 'warn' ? '#b45309' : 'var(--red)');
    const items = [
      ['App', healthOk ? 'Up' : 'Down', healthOk ? 'ok' : 'fail'],
      ['Argo CD', d.argocd_app_exists ? 'Registered' : 'None', d.argocd_app_exists ? 'ok' : 'warn'],
      ['Pod', podOk ? 'Running' : (pod.includes('no pods') ? 'None' : 'Down'), podOk ? 'ok' : 'fail'],
    ];
    document.getElementById('statusGrid').innerHTML = items.map(([l, v, st]) =>
      `<div class="stat-card"><div class="stat-label">${l}</div><div class="stat-val" style="color:${statColor(st)}">${esc(v)}</div></div>`
    ).join('');
    const healthy = isDeployed(d);
    const badge = document.getElementById('liveBadge');
    badge.textContent = healthy ? 'Systems healthy' : (isClean(d) ? 'Clean slate' : 'Outage / not ready');
    badge.className = 'badge' + (healthy ? '' : isClean(d) ? ' warn' : ' err');
    applyDeployedState(d);
    reconcileWizardStep(d);
  }

  async function refreshStatus() {
    if (busy || demoInProgress) return;
    if (statusFailCount >= 3) return;
    try {
      const r = await fetch(appStatusUrl());
      if (!r.ok) throw new Error(`Status HTTP ${r.status}`);
      const j = await r.json();
      if (j.ok) {
        statusFailCount = 0;
        renderStatus(j.data);
        await maybeAutoDeploy(j.data);
      }
    } catch (e) {
      statusFailCount += 1;
      if (statusFailCount === 1) {
        log('Status unavailable — UI pod may be restarting. Will retry…', 'err');
      } else if (statusFailCount === 3) {
        log('Status polling paused — fix selfheal-ui pod, then click Refresh status.', 'err');
      }
    }
  }

  async function streamAction(url, label, stepNum) {
    if (busy) return null;
    resetMode = stepNum === 'reset';
    demoInProgress = true;
    runningStepNum = typeof stepNum === 'number' ? stepNum : 0;
    hideExplainReport();
    setBusy(true);
    focusRunner();
    resetTheater();
    if (resetMode) {
      liveRunner?.classList.add('live-runner--reset');
      theater.classList.add('reset-mode');
    }
    timelineEl.innerHTML = '';
    log('Live: ' + label, 'info');
    opsTitle.textContent = label + '…';
    opsDetail.textContent = resetMode
      ? 'Removing GitOps app and tearing down staging workloads'
      : 'Streaming steps from live cluster in real time';
    if (resetMode) {
      updateStepLive(0, label + '…', 'Unregister from Argo CD · delete Deployment/Service/Pods', true);
    } else if (runningStepNum) {
      updateStepLive(runningStepNum, label + '…', 'Starting…', true);
    }

    let result = null;
    try {
      const resp = await fetch(url, { method: 'POST' });
      if (!resp.ok || !resp.body) throw new Error('Stream failed (HTTP ' + resp.status + ')');

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';

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
          if (data.type === 'step') handleLiveStep(data.step);
          else if (data.type === 'ping') { /* keep-alive */ }
          else if (data.type === 'complete') result = data.data;
          else if (data.type === 'error') throw new Error(data.error);
        }
      }

      opsBar.style.width = '100%';
      opsSpin.style.display = 'none';
      opsTitle.textContent = resetMode ? 'Reset complete' : label + ' complete';
      return result;
    } catch (e) {
      const msg = String(e.message || e);
      if (/failed to fetch|networkerror|load failed/i.test(msg)) {
        throw new Error(
          'Lost connection to demo UI during ' + label +
          '. The selfheal-ui pod may be Not Ready — run fix-selfheal-rollout in Cloud Shell, wait for 1/1 Running, then retry.'
        );
      }
      throw e;
    } finally {
      demoInProgress = false;
      liveRunner?.classList.remove('live-runner--reset');
      theater.classList.remove('reset-mode');
      resetMode = false;
      setBusy(false);
      setWizardStep(wizardStep);
    }
  }

  async function onDeploy() {
    try {
      const d = await streamAction(appStreamUrl('deploy'), currentApp().deployLabel, 1);
      if (!d) return;
      showResult(d.message, d.app_reachable ? 'ok' : 'err');
      if (d.architecture?.length) {
        document.getElementById('archPanel').style.display = 'block';
        document.getElementById('archList').innerHTML = d.architecture.map(a => `<li>${esc(a)}</li>`).join('');
      }
      setOpenLink(d.staging_url || d.open_url);
      setWizardStep(2);
      activatePhase('health', 'done');
      log('Deploy complete', 'ok');
      await refreshStatus();
    } catch (e) { showResult(e.message, 'err'); log('Error: ' + e.message, 'err'); setBusy(false); }
  }

  async function onReset() {
    try {
      autoDeployAttempted[selectedDemoApp] = true;
      resetLockUntil = Date.now() + 15000;
      const banner = document.getElementById('demoAlreadyLive');
      if (banner) banner.hidden = true;
      hideExplainReport();
      const d = await streamAction(appStreamUrl('reset'), currentApp().resetLabel, 'reset');
      if (!d) return;
      demoInProgress = false;
      const clean = !!(d.app_clean ?? d.staging_clean);
      clearWizardStep();
      autoAdvancedDeploy[selectedDemoApp] = false;
      setWizardStep(1);
      showResult(d.message, clean ? 'warn' : 'err');
      resetTheater();
      activatePhase('git', 'active');
      document.getElementById('btnDeploy').hidden = false;
      document.getElementById('btnContinue').hidden = true;
      document.getElementById('deployDetected').hidden = true;
      opsTitle.textContent = clean ? 'Reset complete — app not registered' : 'Reset finished — verify status';
      opsDetail.textContent = clean
        ? 'Not in Argo CD · workloads removed · health down. Stay on Step 1 until you deploy again.'
        : 'Argo CD removed but some objects may remain — health down is expected. Click Deploy to restore.';
      runnerIdle && (runnerIdle.textContent = 'Ready for Step 1 — Deploy');
      log(clean ? 'Reset complete — clean slate' : 'Reset finished — deploy to restore', clean ? 'ok' : 'warn');
      statusFailCount = 0;
      setTimeout(refreshStatus, 3000);
    } catch (e) {
      showResult(e.message, 'err');
      setBusy(false);
      resetMode = false;
      liveRunner?.classList.remove('live-runner--reset');
      theater.classList.remove('reset-mode');
    }
  }

  async function onOutage() {
    try {
      const d = await streamAction(appStreamUrl('outage'), 'Simulate outage', 2);
      if (!d) return;
      autoDeployAttempted[selectedDemoApp] = true;
      showResult(d.message, 'err');
      activatePhase('break', 'break');
      setOpenLink(d.staging_url || d.open_url);
      setWizardStep(3);
      log('Outage active', 'err');
      await refreshStatus();
    } catch (e) { showResult(e.message, 'err'); setBusy(false); }
  }

  async function onExplain() {
    try {
      const d = await streamAction(appStreamUrl('explain'), 'Explain with AI', 3);
      if (!d) return;
      renderExplain(d);
      activatePhase('ai', 'done');
      setWizardStep(4);
      await refreshStatus();
    } catch (e) { showResult(e.message, 'err'); setBusy(false); }
  }

  async function onHeal() {
    try {
      const d = await streamAction(appStreamUrl('heal'), 'Auto-fix app', 4);
      if (!d) return;
      showResult(d.message, d.app_reachable ? 'ok' : 'err');
      activatePhase('health', 'done');
      setOpenLink(d.staging_url || d.open_url);
      setWizardStep(d.app_reachable ? 5 : 4);
      await refreshStatus();
    } catch (e) { showResult(e.message, 'err'); setBusy(false); }
  }

  document.getElementById('btnDeploy').onclick = onDeploy;
  document.getElementById('btnContinue').onclick = onContinueToStep2;
  document.getElementById('btnReset').onclick = onReset;
  document.getElementById('btnOutage').onclick = onOutage;
  document.getElementById('btnExplain').onclick = onExplain;
  document.getElementById('btnHeal').onclick = onHeal;
  document.getElementById('btnClearLog').onclick = () => { logEl.innerHTML = ''; };
  document.getElementById('btnRefresh').onclick = () => { statusFailCount = 0; loadConfig(); refreshStatus(); };
  document.getElementById('btnCopyArgoPass').onclick = async () => {
    const pass = document.getElementById('argoPass')?.textContent?.trim();
    if (!pass || pass.startsWith('(')) return;
    try {
      await navigator.clipboard.writeText(pass);
      log('Argo CD password copied', 'ok');
    } catch (_) {
      log('Copy failed — select password manually', 'err');
    }
  };
  document.getElementById('footerYear').textContent = new Date().getFullYear();

  try {
    const savedApp = sessionStorage.getItem(DEMO_APP_KEY);
    if (savedApp && APP_CONFIG[savedApp]) selectedDemoApp = savedApp;
  } catch (_) {}
  updateAppChrome();
  document.querySelectorAll('.demo-app-tab').forEach(btn => {
    btn.addEventListener('click', () => switchDemoApp(btn.dataset.app));
  });

  wizardStep = loadWizardStep();
  setWizardStep(wizardStep);
  loadConfig().then(refreshStatus);
  setInterval(refreshStatus, 20000);
})();
