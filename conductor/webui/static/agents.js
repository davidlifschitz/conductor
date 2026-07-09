(function () {
  'use strict';

  const STORAGE_PREFIX = 'conductor.webui.';
  const CONDUCTOR_EXPORT = 'export ANTHROPIC_BASE_URL=http://localhost:8484 && ';
  const DEFAULT_PROJECT = '~/projects/payment-service';
  const COPIED_MS = 1600;

  const AGENTS = [
    {
      id: 'claude-code',
      name: 'Claude Code',
      avatar: 'CC',
      accent: '#d97757',
      cliCmd: 'claude',
      appLink: 'claude://open?dir=' + DEFAULT_PROJECT,
      appHost: 'claude://open?dir=…',
      realAppScheme: false,
    },
    {
      id: 'codex',
      name: 'Codex CLI',
      avatar: 'CX',
      accent: '#79c0ff',
      cliCmd: 'codex',
      appLink: 'codex://open?dir=' + DEFAULT_PROJECT,
      appHost: 'codex://open?dir=…',
      realAppScheme: false,
    },
    {
      id: 'cursor',
      name: 'Cursor',
      avatar: 'CU',
      accent: '#a371f7',
      cliCmd: 'cursor .',
      appLink: 'cursor://file/Users/you/projects/payment-service',
      appHost: 'cursor://file/…',
      realAppScheme: true,
    },
    {
      id: 'opencode',
      name: 'OpenCode',
      avatar: 'OC',
      accent: '#3fb950',
      cliCmd: 'opencode',
      appLink: 'opencode://open?dir=' + DEFAULT_PROJECT,
      appHost: 'opencode://open?dir=…',
      realAppScheme: false,
    },
    {
      id: 't3chat',
      name: 'T3 Chat',
      avatar: 'T3',
      accent: '#e3b341',
      cliCmd: 'open t3.chat',
      appLink: 'https://t3.chat/chat/last',
      appHost: 't3.chat/chat/last',
      realAppScheme: true,
      defaultMode: 'app',
    },
    {
      id: 'openclaw',
      name: 'OpenClaw',
      avatar: 'OW',
      accent: '#f47067',
      cliCmd: 'openclaw',
      appLink: 'openclaw://open?dir=' + DEFAULT_PROJECT,
      appHost: 'openclaw://open?dir=…',
      realAppScheme: false,
    },
    {
      id: 'hermes',
      name: 'Hermes',
      avatar: 'HM',
      accent: '#56d4dd',
      cliCmd: 'hermes',
      appLink: 'hermes://open?dir=' + DEFAULT_PROJECT,
      appHost: 'hermes://open?dir=…',
      realAppScheme: false,
    },
  ];

  const INTEGRATIONS = [
    { id: 'gdrive', name: 'Google Drive', avatar: 'GD', accent: '#3fb950' },
    { id: 'github', name: 'GitHub', avatar: 'GH', accent: '#8b949e' },
    { id: 'linear', name: 'Linear', avatar: 'LN', accent: '#a371f7' },
    { id: 'plaid', name: 'Plaid', avatar: 'PL', accent: '#58a6ff' },
  ];

  const DEFAULT_INTEGRATIONS = { gdrive: true, github: true, linear: false, plaid: false };

  function loadJson(key, fallback) {
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + key);
      if (raw == null) return fallback;
      return JSON.parse(raw);
    } catch (_) {
      return fallback;
    }
  }

  function saveJson(key, value) {
    localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
  }

  function defaultAgentModes() {
    const modes = {};
    for (const a of AGENTS) {
      modes[a.id] = a.defaultMode || 'cli';
    }
    return modes;
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(fallbackCopy);
    }
    return fallbackCopy(text);
  }

  function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
    return Promise.resolve();
  }

  function launchCommand(agent) {
    return (
      'cd ' +
      DEFAULT_PROJECT +
      ' && ' +
      CONDUCTOR_EXPORT +
      agent.cliCmd
    );
  }

  function targetLine(agent, mode) {
    if (mode === 'cli') {
      return '$ cd ' + DEFAULT_PROJECT;
    }
    return agent.appHost;
  }

  function primaryLabel(agent, mode, copied) {
    if (copied) return 'Copied ✓';
    if (mode === 'cli') return 'Copy launch command';
    if (agent.realAppScheme) return 'Open app ↗';
    return 'Copy launch command';
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function mount(containerEl) {
    if (!containerEl) return;

    const savedModes = loadJson('agentModes', null);
    const agentModes = defaultAgentModes();
    if (savedModes) Object.assign(agentModes, savedModes);

    let state = {
      agentModes: agentModes,
      integrations: loadJson('integrations', { ...DEFAULT_INTEGRATIONS }),
      customMcps: loadJson('customMcps', []),
      openSession: null,
      copiedAgentId: null,
      mcpFormOpen: false,
      mcpNameDraft: '',
      mcpUrlDraft: '',
    };

    let copiedTimer = null;
    let modalHost = null;
    let mcpFormHost = null;
    let boundContainer = containerEl;

    function persist() {
      saveJson('agentModes', state.agentModes);
      saveJson('integrations', state.integrations);
      saveJson('customMcps', state.customMcps);
    }

    function setState(patch) {
      state = Object.assign({}, state, patch);
      render();
    }

    function agentById(id) {
      return AGENTS.find(function (a) {
        return a.id === id;
      });
    }

    function renderAgentCards() {
      return AGENTS.map(function (agent) {
        const mode = state.agentModes[agent.id] || 'cli';
        const copied = state.copiedAgentId === agent.id;
        const target = targetLine(agent, mode);
        const label = primaryLabel(agent, mode, copied);

        return (
          '<div class="ca-agent-card" data-agent-id="' +
          escHtml(agent.id) +
          '">' +
          '<div class="ca-agent-header">' +
          '<div class="ca-agent-avatar" style="background:' +
          escHtml(agent.accent) +
          '">' +
          escHtml(agent.avatar) +
          '</div>' +
          '<div class="ca-agent-name">' +
          escHtml(agent.name) +
          '</div>' +
          '</div>' +
          '<div class="ca-segment">' +
          '<button type="button" class="ca-segment-btn' +
          (mode === 'cli' ? ' ca-segment-btn--active' : '') +
          '" data-action="set-mode" data-agent="' +
          escHtml(agent.id) +
          '" data-mode="cli">CLI</button>' +
          '<button type="button" class="ca-segment-btn' +
          (mode === 'app' ? ' ca-segment-btn--active' : '') +
          '" data-action="set-mode" data-agent="' +
          escHtml(agent.id) +
          '" data-mode="app">APP</button>' +
          '</div>' +
          '<div class="ca-target" title="' +
          escHtml(target) +
          '">' +
          escHtml(target) +
          '</div>' +
          '<button type="button" class="ca-btn-primary" style="background:' +
          escHtml(agent.accent) +
          '" data-action="launch" data-agent="' +
          escHtml(agent.id) +
          '">' +
          escHtml(label) +
          '</button>' +
          '<button type="button" class="ca-btn-secondary" data-action="preview" data-agent="' +
          escHtml(agent.id) +
          '">Preview session</button>' +
          '</div>'
        );
      }).join('');
    }

    function renderIntegrationCards() {
      return INTEGRATIONS.map(function (ig) {
        const connected = !!state.integrations[ig.id];
        const statusColor = connected ? '#3fb950' : '#6e7681';
        const statusText = connected ? 'Connected' : 'Not connected';
        const actionLabel = connected ? 'Disconnect' : 'Connect';

        return (
          '<div class="ca-mcp-card" data-integration-id="' +
          escHtml(ig.id) +
          '">' +
          '<div class="ca-mcp-header">' +
          '<div class="ca-mcp-avatar" style="background:' +
          escHtml(ig.accent) +
          '">' +
          escHtml(ig.avatar) +
          '</div>' +
          '<div class="ca-mcp-name">' +
          escHtml(ig.name) +
          '</div>' +
          '</div>' +
          '<div class="ca-mcp-status">' +
          '<span class="ca-mcp-dot" style="background:' +
          statusColor +
          '"></span>' +
          '<span class="ca-mcp-status-text" style="color:' +
          statusColor +
          '">' +
          escHtml(statusText) +
          '</span>' +
          '</div>' +
          '<button type="button" class="ca-btn-toggle" data-action="toggle-integration" data-integration="' +
          escHtml(ig.id) +
          '">' +
          escHtml(actionLabel) +
          '</button>' +
          '</div>'
        );
      }).join('');
    }

    function renderCustomMcpCards() {
      return state.customMcps
        .map(function (mcp) {
          return (
            '<div class="ca-mcp-card" data-custom-mcp-id="' +
            escHtml(mcp.id) +
            '">' +
            '<div class="ca-mcp-header">' +
            '<div class="ca-mcp-avatar ca-mcp-avatar--custom">MC</div>' +
            '<div class="ca-mcp-name">' +
            escHtml(mcp.name) +
            '</div>' +
            '</div>' +
            '<div class="ca-mcp-url" title="' +
            escHtml(mcp.url) +
            '">' +
            escHtml(mcp.url) +
            '</div>' +
            '<button type="button" class="ca-btn-remove" data-action="remove-mcp" data-mcp-id="' +
            escHtml(mcp.id) +
            '">Remove</button>' +
            '</div>'
          );
        })
        .join('');
    }

    function renderMain() {
      boundContainer.innerHTML =
        '<div class="ca-root">' +
        '<div class="ca-intro">every session below is proxied through conductor — same policy, same ledger, whichever agent you launch</div>' +
        '<div class="ca-grid ca-agent-grid">' +
        renderAgentCards() +
        '</div>' +
        '<div class="ca-mcp-section-title">plugins &amp; integrations (MCP) — shared context every agent above can call</div>' +
        '<div class="ca-grid ca-mcp-grid">' +
        renderIntegrationCards() +
        renderCustomMcpCards() +
        '<button type="button" class="ca-add-mcp-card" data-action="open-mcp-form">' +
        '<span class="ca-add-mcp-plus">+</span>' +
        '<span class="ca-add-mcp-label">Add custom MCP</span>' +
        '</button>' +
        '</div>' +
        '</div>';
    }

    function renderSessionModal() {
      if (modalHost) {
        modalHost.remove();
        modalHost = null;
      }
      if (!state.openSession) return;

      const agent = agentById(state.openSession.agentId);
      if (!agent) return;

      const isCli = state.openSession.mode === 'cli';
      const modeLabel = isCli ? '— CLI session' : '— App session';

      let body = '';
      if (isCli) {
        body =
          '<div class="ca-session-cli-body">' +
          '<div class="ca-cli-dim">$ export ANTHROPIC_BASE_URL=http://localhost:8484</div>' +
          '<div class="ca-cli-dim">$ ' +
          escHtml(agent.cliCmd) +
          '</div>' +
          '<div class="ca-cli-green">▐ routed via conductor → claude-haiku-4-5 (rule: default)</div>' +
          '<div>&nbsp;</div>' +
          '<div>Reading src/payment_service.py…</div>' +
          '<div>Found the bug: currency rounding in calculate_total(). Applying fix…</div>' +
          '<div class="ca-cli-add">+ round(amount * (1 + tax_rate), 2)</div>' +
          '<div class="ca-cli-del">- round(amount + tax_rate, 2)</div>' +
          '<div class="ca-cli-green">✓ tests passing (14/14)</div>' +
          '<div class="ca-cli-cursor-row"><span>›</span><span class="ca-cli-cursor"></span></div>' +
          '</div>';
      } else {
        body =
          '<div class="ca-session-app-body">' +
          '<div class="ca-app-transcript">' +
          '<div class="ca-app-user-bubble">Fix the failing test in payment_service.py</div>' +
          '<div class="ca-app-assistant-wrap">' +
          '<span class="ca-app-route-tag">planning-language → claude-sonnet-4-6</span>' +
          '<div class="ca-app-assistant-bubble">' +
          'Found it — <code>calculate_total()</code> was rounding before applying tax instead of after.' +
          '<div class="ca-app-diff-block">' +
          '<div class="ca-cli-add">+ round(amount * (1 + tax_rate), 2)</div>' +
          '<div class="ca-cli-del">- round(amount + tax_rate, 2)</div>' +
          '</div>' +
          '<div class="ca-app-tests-pass">All 14 tests pass.</div>' +
          '</div>' +
          '</div>' +
          '</div>' +
          '<div class="ca-app-composer">' +
          '<div class="ca-app-input">Message ' +
          escHtml(agent.name) +
          '…</div>' +
          '<div class="ca-app-send" style="background:' +
          escHtml(agent.accent) +
          '">Send</div>' +
          '</div>' +
          '</div>';
      }

      modalHost = document.createElement('div');
      modalHost.className = 'ca-modal-overlay ca-modal-overlay--session';
      modalHost.setAttribute('data-modal', 'session');
      modalHost.innerHTML =
        '<div class="ca-session-card" data-action="stop-prop">' +
        '<div class="ca-session-header">' +
        '<div class="ca-session-avatar" style="background:' +
        escHtml(agent.accent) +
        '">' +
        escHtml(agent.avatar) +
        '</div>' +
        '<span class="ca-session-name">' +
        escHtml(agent.name) +
        '</span>' +
        '<span class="ca-session-mode">' +
        escHtml(modeLabel) +
        '</span>' +
        '<span class="ca-session-route-pill">routed via conductor → claude-haiku-4-5</span>' +
        '<div class="ca-session-header-spacer"></div>' +
        '<button type="button" class="ca-modal-close" data-action="close-session" aria-label="Close">✕</button>' +
        '</div>' +
        body +
        '</div>';
      document.body.appendChild(modalHost);
    }

    function renderMcpFormModal() {
      if (mcpFormHost) {
        mcpFormHost.remove();
        mcpFormHost = null;
      }
      if (!state.mcpFormOpen) return;

      const canSubmit =
        state.mcpNameDraft.trim().length > 0 &&
        state.mcpUrlDraft.trim().length > 0;

      mcpFormHost = document.createElement('div');
      mcpFormHost.className = 'ca-modal-overlay';
      mcpFormHost.setAttribute('data-modal', 'mcp-form');
      mcpFormHost.innerHTML =
        '<div class="ca-mcp-form-card" data-action="stop-prop">' +
        '<div class="ca-mcp-form-header">' +
        '<span class="ca-mcp-form-title">Add custom MCP</span>' +
        '<button type="button" class="ca-modal-close" data-action="close-mcp-form" aria-label="Close">✕</button>' +
        '</div>' +
        '<div class="ca-form-field">' +
        '<label class="ca-form-label" for="ca-mcp-name">name</label>' +
        '<input id="ca-mcp-name" class="ca-form-input" type="text" placeholder="e.g. Notion" value="' +
        escHtml(state.mcpNameDraft) +
        '" data-field="mcp-name" />' +
        '</div>' +
        '<div class="ca-form-field">' +
        '<label class="ca-form-label" for="ca-mcp-url">server url</label>' +
        '<input id="ca-mcp-url" class="ca-form-input" type="text" placeholder="https://mcp.example.com/sse" value="' +
        escHtml(state.mcpUrlDraft) +
        '" data-field="mcp-url" />' +
        '</div>' +
        '<button type="button" class="ca-btn-submit" data-action="submit-mcp"' +
        (canSubmit ? '' : ' disabled') +
        '>Add MCP</button>' +
        '</div>';
      document.body.appendChild(mcpFormHost);

      const nameInput = mcpFormHost.querySelector('[data-field="mcp-name"]');
      const urlInput = mcpFormHost.querySelector('[data-field="mcp-url"]');
      if (nameInput) nameInput.focus();
      if (nameInput) {
        nameInput.addEventListener('input', function (e) {
          state.mcpNameDraft = e.target.value;
          renderMcpFormModal();
        });
      }
      if (urlInput) {
        urlInput.addEventListener('input', function (e) {
          state.mcpUrlDraft = e.target.value;
          renderMcpFormModal();
        });
      }
    }

    function render() {
      renderMain();
      renderSessionModal();
      renderMcpFormModal();
    }

    function handleLaunch(agentId) {
      const agent = agentById(agentId);
      if (!agent) return;
      const mode = state.agentModes[agentId] || 'cli';
      const cmd = launchCommand(agent);

      if (mode === 'cli' || !agent.realAppScheme) {
        copyText(cmd).then(function () {
          if (copiedTimer) clearTimeout(copiedTimer);
          state.copiedAgentId = agentId;
          renderMain();
          copiedTimer = setTimeout(function () {
            if (state.copiedAgentId === agentId) {
              state.copiedAgentId = null;
              renderMain();
            }
          }, COPIED_MS);
        });
      } else {
        window.open(agent.appLink, '_blank');
        if (copiedTimer) clearTimeout(copiedTimer);
        state.copiedAgentId = agentId;
        renderMain();
        copiedTimer = setTimeout(function () {
          if (state.copiedAgentId === agentId) {
            state.copiedAgentId = null;
            renderMain();
          }
        }, COPIED_MS);
      }
    }

    function onContainerClick(e) {
      const el = e.target.closest('[data-action]');
      if (!el || !boundContainer.contains(el)) return;

      const action = el.getAttribute('data-action');
      const agentId = el.getAttribute('data-agent');
      const integrationId = el.getAttribute('data-integration');
      const mcpId = el.getAttribute('data-mcp-id');

      if (action === 'set-mode' && agentId) {
        const mode = el.getAttribute('data-mode');
        const next = Object.assign({}, state.agentModes, { [agentId]: mode });
        state.agentModes = next;
        persist();
        renderMain();
      } else if (action === 'launch' && agentId) {
        handleLaunch(agentId);
      } else if (action === 'preview' && agentId) {
        const mode = state.agentModes[agentId] || 'cli';
        setState({ openSession: { agentId: agentId, mode: mode } });
      } else if (action === 'toggle-integration' && integrationId) {
        const next = Object.assign({}, state.integrations, {
          [integrationId]: !state.integrations[integrationId],
        });
        state.integrations = next;
        persist();
        renderMain();
      } else if (action === 'remove-mcp' && mcpId) {
        state.customMcps = state.customMcps.filter(function (m) {
          return m.id !== mcpId;
        });
        persist();
        renderMain();
      } else if (action === 'open-mcp-form') {
        setState({
          mcpFormOpen: true,
          mcpNameDraft: '',
          mcpUrlDraft: '',
        });
      }
    }

    function onDocumentClick(e) {
      const el = e.target.closest('[data-action]');
      if (!el) {
        if (e.target === modalHost) {
          setState({ openSession: null });
        } else if (e.target === mcpFormHost) {
          setState({ mcpFormOpen: false });
        }
        return;
      }

      const action = el.getAttribute('data-action');

      if (action === 'stop-prop') {
        e.stopPropagation();
        return;
      }

      if (action === 'close-session') {
        setState({ openSession: null });
        return;
      }

      if (action === 'close-mcp-form') {
        setState({ mcpFormOpen: false });
        return;
      }

      if (action === 'submit-mcp') {
        const name = state.mcpNameDraft.trim();
        const url = state.mcpUrlDraft.trim();
        if (!name || !url) return;
        state.customMcps = state.customMcps.concat([
          { id: 'mcp-' + Date.now(), name: name, url: url },
        ]);
        state.mcpFormOpen = false;
        state.mcpNameDraft = '';
        state.mcpUrlDraft = '';
        persist();
        render();
        return;
      }

      if (e.target === modalHost) {
        setState({ openSession: null });
      } else if (e.target === mcpFormHost) {
        setState({ mcpFormOpen: false });
      }
    }

    boundContainer.addEventListener('click', onContainerClick);
    document.addEventListener('click', onDocumentClick);

    render();

    return {
      destroy: function () {
        boundContainer.removeEventListener('click', onContainerClick);
        document.removeEventListener('click', onDocumentClick);
        if (copiedTimer) clearTimeout(copiedTimer);
        if (modalHost) modalHost.remove();
        if (mcpFormHost) mcpFormHost.remove();
        boundContainer.innerHTML = '';
      },
    };
  }

  window.ConductorAgents = { mount: mount };

  document.addEventListener('DOMContentLoaded', function () {
    const tab = document.getElementById('tab-agents');
    if (tab) mount(tab);
  });
})();