/* Shared hamburger nav (S31) — behaviour for templates/_nav.html.
   Standalone (no deps, no build): every surface includes the same markup + this file,
   so Canvas, Story, Brand and Library can never disagree about the menu again. */
(function () {
  const burger = document.getElementById('nav-burger');
  const menu = document.getElementById('nav-menu');
  const backdrop = document.getElementById('nav-backdrop');
  const closeBtn = document.getElementById('nav-menu-close');
  if (!burger || !menu) return;

  // Active link is resolved from the path here rather than in Jinja, so the pages that
  // don't extend _base.html (canvas, library) get it without threading `active_mode`.
  const here = location.pathname.replace(/\/$/, '') || '/';
  menu.querySelectorAll('a[data-path]').forEach((a) => {
    const p = a.dataset.path;
    if (here === p || here.startsWith(p + '/')) a.classList.add('active');
  });

  function setOpen(open) {
    menu.hidden = !open;
    if (backdrop) backdrop.hidden = !open;
    burger.setAttribute('aria-expanded', String(open));
    burger.classList.toggle('open', open);
    if (open) {
      const first = menu.querySelector('a');
      if (first) first.focus();
    }
  }

  burger.addEventListener('click', () => setOpen(menu.hidden));
  if (backdrop) backdrop.addEventListener('click', () => setOpen(false));
  if (closeBtn) closeBtn.addEventListener('click', () => { setOpen(false); burger.focus(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) { setOpen(false); burger.focus(); return; }
    // Open/close from anywhere on any page. Ignored while typing, so it can never
    // eat a keystroke meant for the brief, the chat or a caption field.
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
                         t.tagName === 'SELECT' || t.isContentEditable);
    if (!typing && (e.key === '\\' || (e.key.toLowerCase() === 'm' && !e.metaKey && !e.ctrlKey && !e.altKey))) {
      e.preventDefault();
      setOpen(menu.hidden);
    }
  });
})();
