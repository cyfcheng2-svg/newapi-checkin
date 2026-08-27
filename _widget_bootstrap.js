_WIDGET_BOOTSTRAP_JS = r"""
(() => {
  const SITEKEY = '__SITEKEY__';
  const host = document.createElement('div');
  host.id = 'ck-ts-host';
  host.setAttribute('data-state', 'init');
  host.style.cssText = 'position:fixed;left:24px;top:24px;width:320px;'
    + 'z-index:2147483647;background:#fff;padding:4px';
  const slot = document.createElement('div');
  slot.id = 'ck-ts-slot';
  host.appendChild(slot);
  document.body.appendChild(host);

  let widgetId = null;

  const render = () => {
    try {
      widgetId = window.turnstile.render(slot, {
        sitekey: SITEKEY,
        callback: (token) => {
          host.setAttribute('data-token', token);
          host.setAttribute('data-state', 'done');
        },
        'error-callback': (code) => {
          host.setAttribute('data-state', 'error');
          host.setAttribute('data-error', String(code || 'unknown'));
        },
        'timeout-callback': () => {
          host.setAttribute('data-state', 'timeout');
        },
      });
      host.setAttribute('data-state', 'rendered');
    } catch (e) {
      host.setAttribute('data-state', 'error');
      host.setAttribute('data-error', String((e && e.message) || e));
    }
  };

  // 隔离上下文（page.evaluate）拿不到页面的 window.turnstile，无法直接 reset。
  // 用 data-cmd 属性做命令通道：外部写入 reset，主世界这里执行后清回 rendered。
  // Cloudflare 文档把 600xxx 归为可重试错误，重试前必须 reset，否则 widget 会
  // 一直停在错误态，后续轮询只是空等。
  new MutationObserver(() => {
    if (host.getAttribute('data-cmd') !== 'reset') return;
    host.removeAttribute('data-cmd');
    try {
      host.removeAttribute('data-error');
      host.removeAttribute('data-token');
      host.setAttribute('data-state', 'rendered');
      if (widgetId !== null) { window.turnstile.reset(widgetId); }
      else { render(); }
    } catch (e) {
      host.setAttribute('data-state', 'error');
      host.setAttribute('data-error', 'reset failed: ' + String((e && e.message) || e));
    }
  }).observe(host, { attributes: true, attributeFilter: ['data-cmd'] });

  if (window.turnstile && window.turnstile.render) { render(); return; }
  const s = document.createElement('script');
  s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  s.async = true;
  s.onload = () => {
    let n = 0;
    const w = setInterval(() => {
      if (window.turnstile && window.turnstile.render) { clearInterval(w); render(); }
      else if (++n > 100) { clearInterval(w); host.setAttribute('data-state', 'no-global'); }
    }, 100);
  };
  s.onerror = () => {
    host.setAttribute('data-state', 'error');
    host.setAttribute('data-error', 'api.js load failed');
  };
  document.head.appendChild(s);
})();
"""