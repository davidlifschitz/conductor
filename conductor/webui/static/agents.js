(function () {
  'use strict';

  const STORAGE_PREFIX = 'conductor.webui.';
  const COPIED_MS = 1600;

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

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function shortModel(model) {
    if (!model) return '?';
    if (model.startsWith('claude-')) return model.slice(7);
    if (model.startsWith('anthropic/')) return model.slice(10);
    return model;
  }

  function fmtCost(c) {
    return c != null
      ? '$' + c.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
      : '?';
  }

  function fmtTokens(n) {
    return n != null ? n.toLocaleString('en-US') : '?';
  }

  function fmtClock(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }

  function fmtLatency(ms) {
    if (ms == null) return '?';
    if (ms >= 1000) return (ms / 1000).toFixed(1) + 's';
    return ms + 'ms';
  }

  async function apiGet(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  async function apiSend(method, path, body) {
    const res = await fetch(path, {
      method: method,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = String(res.status);
      try {
        const j = await res.json();
        detail = j.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  }

  function defaultModes(agents) {
    const modes = {};
    for (const a of agents) {
      modes[a.id] = a.default_mode || 'cli';
    }
    return modes;
  }

  function mount(containerEl) {
    if (!containerEl) return;

    let state = {
      loading: true,
      error: null,
      proxyUrl: 'http://localhost:8484',
      project: { path: '', exists: true, name: '' },
      agents: [],
      agentModes: loadJson('agentModes', {}),
      integrations: [],
      customMcps: [],
      openSession: null,
      sessionPreview: null,
      sessionLoading: false,
      copiedAgentId: null,
      mcpFormOpen: false,
      mcpNameDraft: '',
      mcpUrlDraft: '',
      projectFormOpen: false,
      projectDraft: '',
      projectError: null,
    };

    let copiedTimer = null;
    let modalHost = null;
    let mcpFormHost = null;
    let projectFormHost = null;
    let boundContainer = containerEl;

    function agentById(id) {
      return state.agents.find(function (a) {
        return a.id === id;
      });
    }

    function primaryLabel(agent, mode, copied) {
      if (copied) return 'Copied ✓';
      if (!agent.installed) return 'Not installed';
      if (mode === 'cli') return 'Copy launch command';
      if (agent.app_open_url && agent.app_scheme) return 'Open app ↗';
      return 'Copy launch command';
    }

    function targetLine(agent, mode) {
      if (mode === 'cli') return agent.target_cli || '$ cd ' + state.project.path;
      return agent.target_app || '—';
    }

    async function refresh() {
      state.loading = true;
      state.error = null;
      render();
      try {
        const [agentsPayload, mcpPayload] = await Promise.all([
          apiGet('/api/agents'),
          apiGet('/api/mcp'),
        ]);
        state.proxyUrl = agentsPayload.proxy_url;
        state.project = agentsPayload.project;
        state.agents = agentsPayload.agents || [];
        const modes = defaultModes(state.agents);
        Object.assign(modes, state.agentModes);
        state.agentModes = modes;
        saveJson('agentModes', state.agentModes);
        state.integrations = mcpPayload.integrations || [];
        state.customMcps = mcpPayload.custom || [];
        state.loading = false;
      } catch (e) {
        state.loading = false;
        state.error = String(e.message || e);
      }
      render();
    }

    function renderAgentCards() {
      return state.agents
        .map(function (agent) {
          const mode = state.agentModes[agent.id] || agent.default_mode || 'cli';
          const copied = state.copiedAgentId === agent.id;
          const target = targetLine(agent, mode);
          const label = primaryLabel(agent, mode, copied);
          const disabled = !agent.installed;
          const warn =
            mode === 'app' && !agent.routing_guaranteed
              ? '<div class="ca-warn">web app — routing not guaranteed</div>'
              : '';

          return (
            '<div class="ca-agent-card' +
            (disabled ? ' ca-agent-card--disabled' : '') +
            '" data-agent-id="' +
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
            '<span class="ca-install-chip' +
            (agent.installed ? ' ca-install-chip--ok' : '') +
            '">' +
            (agent.installed ? 'installed' : 'not installed') +
            '</span>' +
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
            warn +
            '<button type="button" class="ca-btn-primary" style="background:' +
            escHtml(agent.accent) +
            '"' +
            (disabled ? ' disabled' : '') +
            ' data-action="launch" data-agent="' +
            escHtml(agent.id) +
            '">' +
            escHtml(label) +
            '</button>' +
            '<button type="button" class="ca-btn-secondary" data-action="preview" data-agent="' +
            escHtml(agent.id) +
            '">Preview session</button>' +
            '</div>'
          );
        })
        .join('');
    }

    function renderIntegrationCards() {
      return state.integrations
        .map(function (ig) {
          const connected = !!ig.connected;
          const statusColor = connected ? '#3fb950' : '#6e7681';
          const statusText = connected ? 'Connected' : 'Not connected';
          const title = ig.connectable
            ? ''
            : connected
              ? escHtml(ig.source || 'detected')
              : 'Coming soon';

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
            '<span>' +
            statusText +
            '</span>' +
            '</div>' +
            '<button type="button" class="ca-btn-secondary" disabled title="' +
            title +
            '">' +
            (connected ? 'Connected' : 'Coming soon') +
            '</button>' +
            '</div>'
          );
        })
        .join('');
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
      if (state.loading) {
        boundContainer.innerHTML =
          '<div class="ca-root"><div class="ca-intro">loading agents…</div></div>';
        return;
      }
      if (state.error) {
        boundContainer.innerHTML =
          '<div class="ca-root"><div class="ca-intro">failed to load: ' +
          escHtml(state.error) +
          '</div></div>';
        return;
      }

      const proj = state.project.path || '—';
      boundContainer.innerHTML =
        '<div class="ca-root">' +
        '<div class="ca-intro">every session below is proxied through conductor — same policy, same ledger, whichever agent you launch</div>' +
        '<div class="ca-project-bar">' +
        '<span class="ca-project-label">project:</span>' +
        '<span class="ca-project-path" title="' +
        escHtml(proj) +
        '">' +
        escHtml(proj) +
        '</span>' +
        '<button type="button" class="ca-project-change" data-action="open-project-form">Change…</button>' +
        '</div>' +
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

    function renderSessionRows(preview) {
      if (!preview || preview.empty || !preview.rows || !preview.rows.length) {
        return (
          '<div class="ca-cli-dim">no ledger rows for this agent yet — launch it through conductor and they\'ll show up here</div>'
        );
      }
      return preview.rows
        .map(function (row) {
          const mark = row.escalated ? '⤴ ' : '';
          const line =
            mark +
            '#' +
            row.id +
            '  ' +
            fmtClock(row.ts) +
            '  ' +
            (row.rule || '—') +
            '  ' +
            shortModel(row.requested_model) +
            '→' +
            shortModel(row.routed_model) +
            '  ' +
            fmtTokens(row.input_tokens) +
            '/' +
            fmtTokens(row.output_tokens) +
            '  ' +
            fmtCost(row.cost_usd) +
            '  ' +
            fmtLatency(row.latency_ms);
          const cls = row.escalated
            ? 'ca-cli-warn'
            : row.status != null && row.status !== 200
              ? 'ca-cli-del'
              : '';
          return '<div class="' + cls + '">' + escHtml(line) + '</div>';
        })
        .join('');
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
      const preview = state.sessionPreview;
      const routeModel = preview && preview.routed_model
        ? shortModel(preview.routed_model)
        : '—';
      const routePill =
        preview && !preview.empty
          ? 'routed via conductor → ' + routeModel
          : 'no sessions yet';

      let body = '';
      if (state.sessionLoading) {
        body = '<div class="ca-session-cli-body"><div class="ca-cli-dim">loading…</div></div>';
      } else if (isCli) {
        const cliBin =
          agent.id === 'cursor' ? 'cursor .' : agent.cli_bin || agent.id;
        body =
          '<div class="ca-session-cli-body">' +
          '<div class="ca-cli-dim">$ export ANTHROPIC_BASE_URL=' +
          escHtml(state.proxyUrl) +
          '</div>' +
          '<div class="ca-cli-dim">$ cd ' +
          escHtml(state.project.path) +
          ' && ' +
          escHtml(cliBin) +
          '</div>' +
          '<div class="ca-cli-green">▐ ' +
          escHtml(routePill) +
          (preview && preview.rule ? ' (rule: ' + escHtml(preview.rule) + ')' : '') +
          '</div>' +
          '<div>&nbsp;</div>' +
          renderSessionRows(preview) +
          '<div class="ca-cli-cursor-row"><span>›</span><span class="ca-cli-cursor"></span></div>' +
          '</div>';
      } else {
        body =
          '<div class="ca-session-app-body">' +
          '<div class="ca-app-transcript">' +
          '<div class="ca-cli-green" style="margin-bottom:10px">' +
          escHtml(routePill) +
          '</div>' +
          renderSessionRows(preview) +
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
        '<span class="ca-session-route-pill">' +
        escHtml(routePill) +
        '</span>' +
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
        state.mcpNameDraft.trim().length > 0 && state.mcpUrlDraft.trim().length > 0;

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

    function renderProjectFormModal() {
      if (projectFormHost) {
        projectFormHost.remove();
        projectFormHost = null;
      }
      if (!state.projectFormOpen) return;

      projectFormHost = document.createElement('div');
      projectFormHost.className = 'ca-modal-overlay';
      projectFormHost.setAttribute('data-modal', 'project-form');
      projectFormHost.innerHTML =
        '<div class="ca-mcp-form-card" data-action="stop-prop">' +
        '<div class="ca-mcp-form-header">' +
        '<span class="ca-mcp-form-title">Change project</span>' +
        '<button type="button" class="ca-modal-close" data-action="close-project-form" aria-label="Close">✕</button>' +
        '</div>' +
        '<div class="ca-form-field">' +
        '<label class="ca-form-label" for="ca-project-path">folder path</label>' +
        '<input id="ca-project-path" class="ca-form-input" type="text" value="' +
        escHtml(state.projectDraft) +
        '" data-field="project-path" />' +
        '</div>' +
        (state.projectError
          ? '<div class="ca-warn">' + escHtml(state.projectError) + '</div>'
          : '') +
        '<button type="button" class="ca-btn-submit" data-action="submit-project">Use this folder</button>' +
        '</div>';
      document.body.appendChild(projectFormHost);
      const input = projectFormHost.querySelector('[data-field="project-path"]');
      if (input) {
        input.focus();
        input.addEventListener('input', function (e) {
          state.projectDraft = e.target.value;
        });
      }
    }

    function render() {
      renderMain();
      renderSessionModal();
      renderMcpFormModal();
      renderProjectFormModal();
    }

    function flashCopied(agentId) {
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

    function handleLaunch(agentId) {
      const agent = agentById(agentId);
      if (!agent || !agent.installed) return;
      const mode = state.agentModes[agentId] || agent.default_mode || 'cli';

      if (mode === 'app' && agent.app_open_url && agent.app_scheme) {
        window.open(agent.app_open_url, '_blank');
        flashCopied(agentId);
        return;
      }

      const cmd = agent.launch_command;
      if (!cmd) return;
      copyText(cmd).then(function () {
        flashCopied(agentId);
      });
    }

    async function openPreview(agentId) {
      const mode = state.agentModes[agentId] || 'cli';
      state.openSession = { agentId: agentId, mode: mode };
      state.sessionLoading = true;
      state.sessionPreview = null;
      render();
      try {
        state.sessionPreview = await apiGet(
          '/api/agents/' + encodeURIComponent(agentId) + '/sessions?limit=8&mode=' + mode
        );
      } catch (_) {
        state.sessionPreview = {
          agent_id: agentId,
          mode: mode,
          empty: true,
          rows: [],
          routed_model: null,
          rule: null,
        };
      }
      state.sessionLoading = false;
      render();
    }

    function onContainerClick(e) {
      const el = e.target.closest('[data-action]');
      if (!el || !boundContainer.contains(el)) return;

      const action = el.getAttribute('data-action');
      const agentId = el.getAttribute('data-agent');
      const mcpId = el.getAttribute('data-mcp-id');

      if (action === 'set-mode' && agentId) {
        const mode = el.getAttribute('data-mode');
        const next = Object.assign({}, state.agentModes, { [agentId]: mode });
        state.agentModes = next;
        saveJson('agentModes', state.agentModes);
        renderMain();
      } else if (action === 'launch' && agentId) {
        handleLaunch(agentId);
      } else if (action === 'preview' && agentId) {
        openPreview(agentId);
      } else if (action === 'remove-mcp' && mcpId) {
        apiSend('DELETE', '/api/mcp/custom/' + encodeURIComponent(mcpId))
          .then(function () {
            return refresh();
          })
          .catch(function (err) {
            state.error = String(err.message || err);
            render();
          });
      } else if (action === 'open-mcp-form') {
        state.mcpFormOpen = true;
        state.mcpNameDraft = '';
        state.mcpUrlDraft = '';
        render();
      } else if (action === 'open-project-form') {
        state.projectFormOpen = true;
        state.projectDraft = state.project.path || '';
        state.projectError = null;
        render();
      }
    }

    function onDocumentClick(e) {
      const el = e.target.closest('[data-action]');
      if (!el) {
        if (e.target === modalHost) {
          state.openSession = null;
          render();
        } else if (e.target === mcpFormHost) {
          state.mcpFormOpen = false;
          render();
        } else if (e.target === projectFormHost) {
          state.projectFormOpen = false;
          render();
        }
        return;
      }

      const action = el.getAttribute('data-action');
      if (action === 'stop-prop') {
        e.stopPropagation();
        return;
      }
      if (action === 'close-session') {
        state.openSession = null;
        render();
        return;
      }
      if (action === 'close-mcp-form') {
        state.mcpFormOpen = false;
        render();
        return;
      }
      if (action === 'close-project-form') {
        state.projectFormOpen = false;
        render();
        return;
      }
      if (action === 'submit-mcp') {
        const name = state.mcpNameDraft.trim();
        const url = state.mcpUrlDraft.trim();
        if (!name || !url) return;
        apiSend('POST', '/api/mcp/custom', { name: name, url: url })
          .then(function () {
            state.mcpFormOpen = false;
            state.mcpNameDraft = '';
            state.mcpUrlDraft = '';
            return refresh();
          })
          .catch(function (err) {
            state.error = String(err.message || err);
            render();
          });
        return;
      }
      if (action === 'submit-project') {
        const path = state.projectDraft.trim();
        if (!path) return;
        apiSend('PUT', '/api/project', { path: path })
          .then(function () {
            state.projectFormOpen = false;
            state.projectError = null;
            return refresh();
          })
          .catch(function (err) {
            state.projectError = String(err.message || err);
            render();
          });
      }
    }

    boundContainer.addEventListener('click', onContainerClick);
    document.addEventListener('click', onDocumentClick);

    refresh();

    return {
      destroy: function () {
        boundContainer.removeEventListener('click', onContainerClick);
        document.removeEventListener('click', onDocumentClick);
        if (copiedTimer) clearTimeout(copiedTimer);
        if (modalHost) modalHost.remove();
        if (mcpFormHost) mcpFormHost.remove();
        if (projectFormHost) projectFormHost.remove();
        boundContainer.innerHTML = '';
      },
    };
  }

  window.ConductorAgents = { mount: mount };
})();
