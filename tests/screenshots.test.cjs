const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
const directory = process.argv[2];
const settle = () => new Promise((resolve) => setImmediate(resolve));
const stubImageURLs = (window) => {
  let frame = 0;
  window.URL.createObjectURL = () => 'blob:preview-' + ++frame;
  window.URL.revokeObjectURL = () => {};
};

(async () => {
  for (const state of ['printing', 'preparing', 'paused', 'complete']) {
    const page = fs.readFileSync(`${directory}/${state}.html`, 'utf8');
    const errors = [];
    const console = new VirtualConsole();
    console.on('jsdomError', (error) => errors.push(error));
    const dom = new JSDOM(page, {
      runScripts: 'dangerously',
      virtualConsole: console,
      beforeParse(window) {
        window.ResizeObserver = class {
          observe() {}
          disconnect() {}
        };
        stubImageURLs(window);
        window.orca = {
          postMessage() {
            throw new Error('Screenshot contacted the host');
          },
        };
      },
    });
    try {
      await settle();
      const get = (id) => {
        const node = dom.window.document.getElementById(id);
        assert.ok(node, `Expected rendered element: ${id}`);
        return node;
      };
      const visible = (node) => !node.closest('[hidden]');
      const assertPreview = (phase) => {
        assert.equal(visible(get('stats')), true, 'A screenshot includes print statistics');
        assert.equal(visible(get('play')), false, 'The preview starts playback automatically');
        assert.equal(visible(get('stop')), true);
        assert.equal(
          new URL(get('image').src).protocol,
          'blob:',
          'The preview displays its embedded image through a Blob URL',
        );
        assert.equal(visible(get('hotend-value')), phase !== 'complete');
        assert.equal(
          visible(get('remaining-value')),
          phase === 'printing',
          'Only running prints show an ETA',
        );
        assert.equal(visible(get('progress-bar')), true);
        assert.equal(visible(get('stats-message')), phase === 'preparing');
        assert.equal(visible(get('plan-value')), phase === 'preparing');
        if (phase === 'preparing') {
          assert.match(get('plan-stat').textContent, /35m\s*\+ preparation/);
          assert.equal(visible(get('finish-value')), true);
          assert.match(get('finish-value').textContent, /^after /);
          assert.equal(get('progress-bar').hasAttribute('aria-valuenow'), false);
          assert.equal(
            get('progress-bar').getAttribute('aria-valuetext'),
            'Preparation in progress',
          );
          assert.equal(get('elapsed-value').textContent, '01:36');
        }
        assert.equal(dom.window.document.querySelectorAll('[data-tool]').length, 4);
        if (phase === 'complete') {
          const bar = get('progress-bar');
          assert.equal(
            bar.getAttribute('aria-valuenow'),
            bar.getAttribute('aria-valuemax'),
            'A completed job reaches full progress',
          );
          const [current, total] = get('layers-value').textContent.match(/\d+/g).map(Number);
          assert.equal(current, total, 'A completed job has no layers left');
        }
      };
      assertPreview(state);
      for (const [next, phase] of ['printing', 'preparing', 'paused', 'complete'].entries()) {
        dom.window.document.body.dispatchEvent(
          new dom.window.KeyboardEvent('keydown', { key: String(next + 1), bubbles: true }),
        );
        await settle();
        assertPreview(phase);
      }
      const toggle = dom.window.document.querySelector('[role="switch"]');
      const previous = toggle.getAttribute('aria-checked');
      toggle.click();
      await settle();
      assert.notEqual(
        toggle.getAttribute('aria-checked'),
        previous,
        'The offline bridge supports light interaction',
      );
      assert.deepEqual(errors, [], 'Compiled production page runs without errors');
    } finally {
      dom.window.close();
    }
  }
  const previewErrors = [];
  const previewConsole = new VirtualConsole();
  previewConsole.on('jsdomError', (error) => previewErrors.push(error));
  const preview = new JSDOM(fs.readFileSync(`${directory}/index.html`, 'utf8'), {
    runScripts: 'dangerously',
    virtualConsole: previewConsole,
    beforeParse(window) {
      stubImageURLs(window);
      window.ResizeObserver = class {
        observe() {}
        disconnect() {}
      };
    },
  });
  try {
    await settle();
    const doc = preview.window.document;
    const picker = doc.getElementById('preview-state');
    const cases = {
      printing: 'printing',
      preparing: 'preparing',
      'preparing-warm': 'preparing',
      paused: 'paused',
      finishing: 'printing',
      complete: 'complete',
      cancelled: 'cancelled',
      error: 'error',
      standby: 'standby',
      unavailable: 'unavailable',
      'phase-unknown': 'printing',
      'plan-only': 'printing',
      'preparing-no-plan': 'preparing',
      connecting: 'printing',
      'stream-waiting': 'printing',
      'stream-error': 'printing',
      'light-error': 'printing',
      'permission-denied': 'unavailable',
      'config-missing': 'unavailable',
      'printer-unavailable': 'unavailable',
    };
    assert.deepEqual(
      Array.from(
        doc.querySelectorAll('[data-preview-state]'),
        (button) => button.dataset.previewState,
      ),
      Object.keys(cases),
    );
    for (const [state, rendered] of Object.entries(cases)) {
      picker.click();
      assert.equal(picker.getAttribute('aria-expanded'), 'true');
      doc.querySelector(`[data-preview-state="${state}"]`).click();
      await settle();
      if (doc.getElementById('image').hasAttribute('src'))
        doc.getElementById('image').dispatchEvent(new preview.window.Event('load'));
      await settle();
      assert.equal(doc.getElementById('stats').dataset.state, rendered, state);
      if (state === 'connecting') {
        assert.equal(doc.getElementById('image').hidden, true);
        assert.equal(doc.getElementById('empty').hidden, false);
        assert.match(doc.getElementById('empty').textContent, /Connecting to camera/);
      }
      if (state === 'stream-waiting') {
        assert.equal(doc.getElementById('camera').dataset.state, 'waiting');
        assert.match(doc.getElementById('camera').textContent, /3s/);
        assert.doesNotMatch(doc.getElementById('camera').textContent, /404/);
      }
      if (state === 'stream-error') {
        assert.equal(doc.getElementById('camera').dataset.state, 'error');
        assert.match(doc.getElementById('camera').textContent, /404.*8s/);
      }
      assert.equal(doc.getElementById('light-error').hidden, state !== 'light-error');
      const blocked = ['permission-denied', 'config-missing'].includes(state);
      assert.equal(doc.getElementById('warning').hidden, !blocked);
      if (blocked) {
        assert.equal(doc.getElementById('play').hidden, true);
        assert.ok(doc.getElementById('warning-label').textContent.trim());
        assert.equal(doc.getElementById('stats').hidden, true);
      }
      if (state === 'printer-unavailable') {
        assert.equal(doc.getElementById('image').hidden, true);
        assert.equal(doc.getElementById('camera').dataset.state, 'error');
        assert.match(doc.getElementById('camera').textContent, /Cannot reach printer/);
      }
      assert.equal(
        doc.querySelector('[data-preview-state][aria-pressed="true"]').dataset.previewState,
        state,
      );
      assert.equal(picker.getAttribute('aria-expanded'), 'false');
      if (['phase-unknown', 'plan-only'].includes(state))
        assert.equal(doc.getElementById('printer').textContent, 'Printing');
      assert.equal(
        doc.getElementById('progress-bar').hasAttribute('data-shimmer'),
        ['printing', 'preparing', 'paused'].includes(rendered),
      );
      const bar = doc.getElementById('progress-bar');
      if (state === 'preparing') assert.equal(bar.hasAttribute('data-heating'), true);
      if (state === 'preparing-warm') assert.equal(bar.hasAttribute('data-heating'), false);
      if (state === 'finishing')
        assert.equal(doc.getElementById('stats-state').textContent, 'Finishing');
      if (state === 'phase-unknown')
        assert.equal(doc.getElementById('stats-state').textContent, 'Printing');
      if (state === 'preparing-no-plan') assert.equal(doc.getElementById('plan-stat').hidden, true);
    }
    doc.body.dispatchEvent(
      new preview.window.KeyboardEvent('keydown', { key: '1', bubbles: true }),
    );
    await settle();
    assert.equal(
      doc.querySelector('[data-preview-state][aria-pressed="true"]').dataset.previewState,
      'printing',
      'Keyboard shortcuts keep the picker in sync',
    );
    doc.querySelector('[data-preview-step="-1"]').click();
    await settle();
    assert.equal(
      doc.querySelector('[data-preview-state][aria-pressed="true"]').dataset.previewState,
      'printer-unavailable',
      'Previous wraps to the last state',
    );
    doc.querySelector('[data-preview-step="1"]').click();
    await settle();
    assert.equal(
      doc.getElementById('stats').dataset.state,
      'printing',
      'Next wraps to the first state',
    );
    picker.blur();
    doc.getElementById('preview-picker').dispatchEvent(new preview.window.Event('pointerenter'));
    assert.equal(picker.getAttribute('aria-expanded'), 'true', 'Hover opens the state choices');
    doc.getElementById('preview-picker').dispatchEvent(new preview.window.Event('pointerleave'));
    assert.equal(picker.getAttribute('aria-expanded'), 'false', 'Leaving closes the state choices');
    picker.click();
    doc.body.dispatchEvent(
      new preview.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    );
    assert.equal(picker.getAttribute('aria-expanded'), 'false', 'Escape closes the choices');
    assert.deepEqual(previewErrors, []);
  } finally {
    preview.window.close();
  }
  console.log('All 20 preview states render; the four showcase states retain keyboard switching.');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
