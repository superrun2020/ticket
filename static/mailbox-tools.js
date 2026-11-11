(() => {
  function init({ state, api, esc, selectMailbox }) {
    const $ = (selector) => document.querySelector(selector);
    const searchDialog = $('#mailboxSearchDialog');
    const settingsDialog = $('#mailboxSettingsDialog');
    const searchInput = $('#mailboxSearchInput');
    const searchResults = $('#mailboxSearchResults');
    let statusMailboxId = '';
    let statusRequest = 0;
    let settingsMailboxId = '';

    const activeMailboxes = () => (state.mailboxes || []).filter((mailbox) => mailbox.enabled);
    const selectedMailboxId = () => {
      const quickSwitcher = $('#mailboxQuickSwitcher');
      return state.mailbox && state.mailbox !== 'all' ? state.mailbox : quickSwitcher?.value || '';
    };
    const pill = (element, label, status) => {
      element.textContent = label;
      element.className = `mailbox-state-pill ${status}`;
    };
    const settingsError = (message) => {
      $('#mailboxSettingsContent').innerHTML = `<div class="mail-service-empty"><b>暂时无法读取邮箱设置</b>${esc(message)}</div>`;
      $('#mailboxSettingsCheckedAt').textContent = '';
      pill($('#mailboxSettingsDnsPill'), 'DNS 未确认', 'unknown');
      pill($('#mailboxSettingsMxPill'), 'MX 未确认', 'unknown');
    };

    function renderSearchResults(query = '') {
      const normalized = query.trim().toLocaleLowerCase();
      const matches = activeMailboxes().filter((mailbox) =>
        [mailbox.email, mailbox.name, mailbox.mailbox_tag, mailbox.domain_name]
          .some((value) => String(value || '').toLocaleLowerCase().includes(normalized)));
      if (!matches.length) {
        searchResults.innerHTML = `<div class="mail-service-empty"><b>${normalized ? '没有匹配邮箱' : '暂无可访问邮箱'}</b>${normalized ? '试试完整邮箱地址、名称或标签。' : '当前工作区没有已启用且可访问的邮箱。'}</div>`;
        return;
      }
      searchResults.innerHTML = matches.map((mailbox) => `<article class="mailbox-search-result">
        <div class="mailbox-search-result-main"><b>${esc(mailbox.email)}</b><small>${esc(mailbox.name || mailbox.mailbox_tag || mailbox.email)}${mailbox.mailbox_tag ? ` · ${esc(mailbox.mailbox_tag)}` : ''}</small></div>
        <div class="mailbox-search-result-actions"><button type="button" class="tool-button" data-open-mailbox="${esc(mailbox.id)}">打开邮箱</button><button type="button" class="tool-button" data-open-mailbox-settings="${esc(mailbox.id)}">设置</button></div>
      </article>`).join('');
    }

    async function loadSettings(mailboxId, refresh = false) {
      settingsMailboxId = mailboxId;
      const content = $('#mailboxSettingsContent');
      content.innerHTML = '<div class="mail-service-empty"><b>正在检测邮箱与 DNS…</b>请稍候。</div>';
      $('#mailboxSettingsSubtitle').textContent = '正在读取邮箱接入、DNS 与 MX 状态。';
      $('#mailboxSettingsCheckedAt').textContent = '';
      pill($('#mailboxSettingsDnsPill'), 'DNS 检查中', 'unknown');
      pill($('#mailboxSettingsMxPill'), 'MX 检查中', 'unknown');
      if (!settingsDialog.open) settingsDialog.showModal();
      try {
        const data = await api(`/api/mailboxes/${encodeURIComponent(mailboxId)}/settings${refresh ? '?refresh=true' : ''}`);
        if (settingsMailboxId !== mailboxId) return;
        const mailbox = data.mailbox || {};
        const domain = data.domain || {};
        const readiness = domain.readiness || {};
        const dns = readiness.dns || {};
        const dnsActive = Boolean(dns.active);
        const mxActive = Boolean(dns.mx_active);
        pill($('#mailboxSettingsDnsPill'), dnsActive ? 'DNS 已生效' : 'DNS 未生效', dnsActive ? 'ready' : 'pending');
        pill($('#mailboxSettingsMxPill'), mxActive ? 'MX 已生效' : 'MX 未生效', mxActive ? 'ready' : 'pending');
        $('#mailboxSettingsSubtitle').textContent = `${mailbox.email || ''} · ${domain.name || '未识别域名'}`;
        $('#mailboxSettingsCheckedAt').textContent = readiness.checked_at ? `检测时间：${readiness.checked_at}` : '暂无检测时间';
        const recordGroup = (label, values) => `<section class="mailbox-dns-record"><h3>${label}</h3>${values.length ? values.map((value) => `<code>${esc(value)}</code>`).join('') : '<small>未检测到记录</small>'}</section>`;
        const dkim = (dns.dkim || []).flatMap((item) => (item.records || []).map((record) => `${item.selector}: ${record}`));
        const checks = (readiness.checks || []).map((check) => `<div class="domain-check ${check.passed ? 'passed' : 'pending'}"><span>${check.passed ? '✓' : '!'}</span><div><b>${esc(check.name)}</b><small>${esc(check.detail)}</small></div><em>${check.passed ? '已通过' : '待检查'}</em></div>`).join('');
        content.innerHTML = `<div class="mailbox-settings-overview">
          <section><span>工单邮箱</span><b>${esc(mailbox.email || '')}</b><small>${esc(mailbox.name || '')}${mailbox.mailbox_tag ? ` · ${esc(mailbox.mailbox_tag)}` : ''}</small></section>
          <section><span>邮箱账户</span><b class="${data.service_account?.enabled ? 'text-ready' : 'text-pending'}">${data.service_account?.enabled ? '已启用' : data.service_account?.exists ? '已停用' : '未关联 Mail2'}</b><small>${data.service_account?.synced_at ? `同步于 ${esc(data.service_account.synced_at)}` : '账户状态来自 Mail2 同步信息'}</small></section>
          <section><span>域名服务</span><b class="${domain.enabled ? 'text-ready' : 'text-pending'}">${domain.enabled ? '已启用' : domain.exists ? '已停用' : '未配置'}</b><small>${esc(domain.description || domain.name || '未找到 Mail2 域名配置')}</small></section>
        </div>
        <div class="mailbox-dns-records">${recordGroup('NS 域名服务器', dns.nameservers || [])}${recordGroup('A 地址记录', dns.addresses || [])}${recordGroup('MX 收信记录', dns.mx_records || [])}${recordGroup('SPF 发信授权', dns.spf_records || [])}${recordGroup('DMARC 策略', dns.dmarc_records || [])}${recordGroup('DKIM 签名', dkim)}</div>
        ${checks ? `<section class="mailbox-settings-checks"><h3>邮件可用性检查</h3><div class="domain-checks">${checks}</div></section>` : '<div class="mail-service-empty"><b>域名尚无检测结果</b>请确认 Mail2 中已配置该域名。</div>'}`;
      } catch (error) {
        if (settingsMailboxId !== mailboxId) return;
        settingsError(error.message === 'REQUEST_TIMEOUT' ? '检测超时，请稍后重新检测。' : '请求失败或当前账户无权查看此邮箱。');
      }
    }

    async function syncHeaderStatus() {
      const tools = $('#mailboxTopTools');
      const mailboxId = selectedMailboxId();
      tools.hidden = !mailboxId;
      if (!mailboxId || mailboxId === statusMailboxId) return;
      statusMailboxId = mailboxId;
      const requestId = ++statusRequest;
      pill($('#mailboxDnsStatus'), 'DNS 检查中', 'unknown');
      pill($('#mailboxMxStatus'), 'MX 检查中', 'unknown');
      try {
        const data = await api(`/api/mailboxes/${encodeURIComponent(mailboxId)}/settings`);
        if (requestId !== statusRequest) return;
        const dns = data.domain?.readiness?.dns || {};
        pill($('#mailboxDnsStatus'), dns.active ? 'DNS 已生效' : 'DNS 未生效', dns.active ? 'ready' : 'pending');
        pill($('#mailboxMxStatus'), dns.mx_active ? 'MX 已生效' : 'MX 未生效', dns.mx_active ? 'ready' : 'pending');
      } catch (error) {
        if (requestId !== statusRequest) return;
        pill($('#mailboxDnsStatus'), 'DNS 未确认', 'unknown');
        pill($('#mailboxMxStatus'), 'MX 未确认', 'unknown');
      }
    }

    $('#mailboxSearchButton').addEventListener('click', () => {
      renderSearchResults(searchInput.value);
      searchDialog.showModal();
      searchInput.focus();
    });
    $('#closeMailboxSearch').addEventListener('click', () => searchDialog.close());
    searchInput.addEventListener('input', () => renderSearchResults(searchInput.value));
    searchResults.addEventListener('click', async (event) => {
      const settingsButton = event.target.closest('[data-open-mailbox-settings]');
      if (settingsButton) {
        searchDialog.close();
        await loadSettings(settingsButton.dataset.openMailboxSettings);
        return;
      }
      const openButton = event.target.closest('[data-open-mailbox]');
      if (openButton) {
        searchDialog.close();
        await selectMailbox(openButton.dataset.openMailbox);
      }
    });
    $('#mailboxSettingsButton').addEventListener('click', () => {
      const mailboxId = selectedMailboxId();
      if (mailboxId) void loadSettings(mailboxId);
    });
    $('#closeMailboxSettings').addEventListener('click', () => settingsDialog.close());
    $('#refreshMailboxSettings').addEventListener('click', () => {
      if (settingsMailboxId) void loadSettings(settingsMailboxId, true);
    });

    const nav = $('#mailboxNav');
    if (nav) new MutationObserver(() => { void syncHeaderStatus(); }).observe(nav, { childList: true, subtree: true });
    const switcher = $('#mailboxQuickSwitcher');
    if (switcher) switcher.addEventListener('change', () => { statusMailboxId = ''; void syncHeaderStatus(); });
    void syncHeaderStatus();
  }

  window.TicketMailboxUI = { init };
})();
