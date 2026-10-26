'use strict';
(() => {
  const root = document.getElementById('chat');
  let bridge, current = -1, pending = null, latest = null, measuring = false;
  const signatures = new WeakMap();
  function height() {
    if (!bridge || current < 0 || measuring) return;
    measuring = true;
    requestAnimationFrame(() => {
      measuring = false;
      bridge.presented(current, Math.ceil(root.getBoundingClientRect().height));
    });
  }
  function apply(data) {
    if (!data || data.version < current) return;
    latest = data;
    for (const [key, value] of Object.entries(data.colors || {}))
      document.documentElement.style.setProperty('--' + key, value);
    document.documentElement.classList.toggle('light', !!data.light);
    document.body.style.fontFamily = data.font;
    document.body.style.fontSize = data.size + 'px';
    if (window.getSelection()?.toString()) { pending = data; return; }
    pending = null;
    const fragment = DOMPurify.sanitize(data.html || '', {
      RETURN_DOM_FRAGMENT:true,
      FORBID_TAGS:['script','style','iframe','object','embed','form','input','button','audio','video','svg','math'],
      FORBID_ATTR:['srcset','onerror','onclick'],
      ALLOWED_URI_REGEXP:/^(?:pcs-chat:|data:image\/png;base64,|#)/i
    });
    const incoming = Array.from(fragment.childNodes);
    for (let i = 0; i < incoming.length; i++) {
      const next = incoming[i];
      const formulas = next.nodeType === Node.ELEMENT_NODE
        ? [ ...(next.matches('[data-math]') ? [next] : []), ...next.querySelectorAll('[data-math]') ] : [];
      const signature = (next.outerHTML || next.textContent) + JSON.stringify(formulas.map(e => data.math?.[Number(e.dataset.math)]));
      const old = root.childNodes[i];
      if (old && signatures.get(old) === signature) continue;
      for (const element of formulas) {
        const value = data.math?.[Number(element.dataset.math)];
        if (!value) { element.textContent = ''; continue; }
        try {
          katex.render(value.source, element, {displayMode:value.block, throwOnError:true,
            trust:false, strict:'error', maxExpand:1000, maxSize:20});
        } catch (_) {
          element.textContent = value.source;
          element.classList.add('formula-source');
        }
      }
      signatures.set(next, signature);
      if (old) root.replaceChild(next, old); else root.appendChild(next);
    }
    while (root.childNodes.length > incoming.length) root.lastChild.remove();
    current = data.version;
    height();
  }
  window.refreshChat = () => bridge && bridge.document(value => apply(JSON.parse(value)));
  document.addEventListener('selectionchange', () => {
    if (!window.getSelection()?.toString() && pending) apply(pending);
  });
  document.addEventListener('click', event => {
    const anchor = event.target.closest('a');
    if (!anchor) return;
    const target = anchor.getAttribute('href') || '';
    if (target.startsWith('#')) return;
    event.preventDefault();
    if (target.startsWith('pcs-chat:')) bridge.activate(target.slice(9), current);
  });
  root.addEventListener('load', height, true);
  new ResizeObserver(height).observe(root);
  document.fonts.ready.then(height);
  new QWebChannel(qt.webChannelTransport, channel => {
    bridge = channel.objects.chat;
    window.refreshChat();
  });
})();
