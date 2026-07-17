(() => {
  'use strict';
  const VERSION = '6.9.3';
  const VERSION_LABEL = `Private player terminal · V${VERSION}`;
  document.title = `Gold Slots Player ${VERSION}`;
  document.documentElement.dataset.gsPublicRelease = VERSION;

  let timer = null;
  let applying = false;
  let bridgeActive = false;
  let suppressControl = null;
  let suppressUntil = 0;

  function apply() {
    if (applying) return;
    applying = true;
    try {
      const width = Math.max(1, innerWidth);
      const height = Math.max(1, innerHeight);
      const portrait = height > width;
      let columns = portrait ? 2 : (width <= 820 ? 3 : width <= 1100 ? 4 : 6);
      if (portrait && width >= 1000) columns = 3;
      const rows = Math.ceil(11 / columns);
      const root = document.documentElement;
      const orientation = portrait ? 'portrait' : 'landscape';
      const density = height <= 700 ? 'compact' : height >= 1180 ? 'large' : 'standard';

      if (root.dataset.gsOrientation !== orientation) root.dataset.gsOrientation = orientation;
      if (root.dataset.gsDensity !== density) root.dataset.gsDensity = density;
      if (root.style.getPropertyValue('--gs-cols') !== String(columns)) root.style.setProperty('--gs-cols', String(columns));
      if (root.style.getPropertyValue('--gs-rows') !== String(rows)) root.style.setProperty('--gs-rows', String(rows));

      document.querySelectorAll('.brand span').forEach((node) => {
        const current = node.textContent || '';
        if (/Private player terminal/i.test(current) && current !== VERSION_LABEL) node.textContent = VERSION_LABEL;
      });
    } finally {
      applying = false;
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(apply, 35);
  }

  function controlAt(event) {
    const selector = 'button,[data-action]';
    const direct = event.target instanceof Element ? event.target.closest(selector) : null;
    if (direct && direct.closest('#app')) return direct;
    for (const element of document.elementsFromPoint(event.clientX, event.clientY)) {
      const control = element instanceof Element ? element.closest(selector) : null;
      if (control && control.closest('#app')) return control;
    }
    return null;
  }

  // Electron cabinets can occasionally lose the compatibility click after a real
  // pointer press. Convert that one trusted physical press into exactly one normal
  // button activation. This does not repeat wagers or make any game decision.
  document.addEventListener('pointerdown', (event) => {
    if (!event.isTrusted || event.button !== 0 || bridgeActive) return;
    const control = controlAt(event);
    if (!control || control.disabled || control.getAttribute('aria-disabled') === 'true') return;
    bridgeActive = true;
    suppressControl = control;
    suppressUntil = performance.now() + 800;
    event.preventDefault();
    try {
      control.focus({ preventScroll: true });
      control.click();
    } finally {
      queueMicrotask(() => { bridgeActive = false; });
    }
  }, true);

  // Suppress only the duplicate trusted compatibility click that may follow the
  // bridged pointer press. The programmatic activation above remains the one action.
  document.addEventListener('click', (event) => {
    if (!event.isTrusted) return;
    const active = suppressControl;
    const sameControl = active && (event.target === active || active.contains(event.target));
    if (sameControl && performance.now() <= suppressUntil) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
    suppressControl = null;
    suppressUntil = 0;
  }, true);

  addEventListener('resize', schedule, { passive: true });
  addEventListener('orientationchange', schedule, { passive: true });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(); });
  const appRoot = document.getElementById('app');
  if (appRoot) new MutationObserver(schedule).observe(appRoot, { childList: true, subtree: true });
  window.GoldSlotsRelease = { version: VERSION, apply, schedule };
  apply();
})();
