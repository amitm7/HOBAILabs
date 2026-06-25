/* HOBAILabs — shared app shell (step wizard, drawer, preview panel). No pipeline logic. */
'use strict';

const Shell = {
  steps: [],
  current: 0,
  maxReached: 0,

  init(config) {
    this.steps = config.steps || [];
    this.current = 0;
    this.maxReached = 0;

    document.querySelectorAll('.step-pill').forEach((pill, i) => {
      pill.addEventListener('click', () => {
        if (i <= this.maxReached) this.goTo(i);
      });
    });

    const back = document.getElementById('step-back');
    const next = document.getElementById('step-next');
    if (back) back.addEventListener('click', () => this.goTo(this.current - 1));
    if (next) next.addEventListener('click', () => this.goTo(this.current + 1));

    this._wireDrawer();
    this._wireCostChip();
    this._wireLogToggle();
    this._wireFrameExpand();
    this.goTo(0, true);
    this.refreshIcons();
  },

  goTo(index, silent) {
    if (index < 0 || index >= this.steps.length) return;
    this.current = index;
    if (index > this.maxReached) this.maxReached = index;

    document.querySelectorAll('.step-panel').forEach((panel, i) => {
      panel.classList.toggle('active', i === index);
    });

    document.querySelectorAll('.step-pill').forEach((pill, i) => {
      pill.classList.toggle('active', i === index);
      pill.classList.toggle('done', i < index);
      pill.setAttribute('aria-current', i === index ? 'step' : 'false');
    });

    const back = document.getElementById('step-back');
    const next = document.getElementById('step-next');
    if (back) back.disabled = index === 0;
    if (next) next.disabled = index >= this.steps.length - 1;

    if (!silent) {
      const panel = document.querySelector(`.step-panel[data-step="${this.steps[index]}"]`);
      panel?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  },

  goToStep(name) {
    const i = this.steps.indexOf(name);
    if (i >= 0) this.goTo(i);
  },

  onFramesReady() {
    this.goToStep('frames');
    this.moveTimelineToPreview();
    this.setPreviewEmpty(false);
  },

  onRenderStart() {
    this.goToStep('render');
    const panel = document.getElementById('progress-panel');
    if (panel) {
      panel.style.display = 'block';
      panel.classList.add('visible');
    }
    this.setProgressStep(0);
  },

  moveTimelineToPreview() {
    const strip = document.getElementById('timeline-strip');
    const host = document.getElementById('preview-timeline-host');
    if (strip && host && strip.parentElement !== host) {
      host.appendChild(strip);
    }
  },

  setPreviewEmpty(show) {
    const empty = document.getElementById('preview-empty');
    if (empty) empty.classList.toggle('hidden', !show);
  },

  setProgressStep(index) {
    document.querySelectorAll('.progress-step').forEach((el, i) => {
      el.classList.toggle('active', i === index);
      el.classList.toggle('done', i < index);
    });
  },

  updateProgressFromLog(text) {
    const t = (text || '').toLowerCase();
    if (t.includes('parse') || t.includes('frame')) this.setProgressStep(0);
    else if (t.includes('still') || t.includes('image') || t.includes('scene')) this.setProgressStep(1);
    else if (t.includes('clip') || t.includes('kling') || t.includes('animat')) this.setProgressStep(2);
    else if (t.includes('assembl') || t.includes('caption') || t.includes('music')) this.setProgressStep(3);
    else if (t.includes('complete') || t.startsWith('✓')) this.setProgressStep(4);
  },

  updateCostChip(text) {
    const chip = document.getElementById('cost-chip');
    if (!chip) return;
    chip.textContent = text || '';
  },

  refreshIcons() {
    if (typeof lucide !== 'undefined') lucide.createIcons();
  },

  _wireDrawer() {
    const open = document.getElementById('settings-open');
    const close = document.getElementById('settings-close');
    const backdrop = document.getElementById('drawer-backdrop');
    const drawer = document.getElementById('settings-drawer');

    const show = () => {
      drawer?.classList.add('open');
      backdrop?.classList.add('open');
    };
    const hide = () => {
      drawer?.classList.remove('open');
      backdrop?.classList.remove('open');
    };

    open?.addEventListener('click', show);
    close?.addEventListener('click', hide);
    backdrop?.addEventListener('click', hide);
  },

  _wireCostChip() {
    const chip = document.getElementById('cost-chip');
    chip?.addEventListener('click', () => this.goToStep('render'));

    const breakdown = document.getElementById('cost-breakdown');
    if (!breakdown) return;

    const sync = () => {
      const totalLine = breakdown.innerText.match(/~\$[\d.]+/);
      if (totalLine) Shell.updateCostChip(totalLine[0] + ' est.');
    };

    new MutationObserver(sync).observe(breakdown, { childList: true, subtree: true, characterData: true });
  },

  _wireLogToggle() {
    const toggle = document.getElementById('progress-log-toggle');
    const log = document.getElementById('progress-log');
    if (!toggle || !log) return;
    toggle.addEventListener('click', () => {
      log.classList.toggle('open');
      const open = log.classList.contains('open');
      toggle.setAttribute('aria-expanded', open);
      const label = toggle.querySelector('.log-toggle-label');
      if (label) label.textContent = open ? 'Hide technical log' : 'Show technical log';
    });
  },

  _wireFrameExpand() {
    document.getElementById('frames-container')?.addEventListener('click', (e) => {
      const btn = e.target.closest('.frame-expand-btn');
      if (!btn) return;
      const card = btn.closest('.frame-card');
      if (card) {
        card.classList.toggle('expanded');
        btn.textContent = card.classList.contains('expanded') ? 'Less' : 'More';
      }
    });
  },
};

window.initShell = (config) => Shell.init(config);
window.shellGoToStep = (name) => Shell.goToStep(name);
window.shellOnFramesReady = () => Shell.onFramesReady();
window.shellOnRenderStart = () => Shell.onRenderStart();
window.shellUpdateCost = (text) => Shell.updateCostChip(text);
window.shellUpdateProgress = (text) => Shell.updateProgressFromLog(text);
window.shellRefreshIcons = () => Shell.refreshIcons();
window.shellSetPreviewEmpty = (show) => Shell.setPreviewEmpty(show);
window.shellShowOutput = () => {
  Shell.setPreviewEmpty(false);
  const out = document.getElementById('output-panel');
  if (out) out.classList.add('visible');
};
