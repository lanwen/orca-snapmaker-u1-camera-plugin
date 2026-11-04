const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM, VirtualConsole } = require('jsdom');
const directory = process.argv[2];
const settle = () => new Promise((resolve) => setImmediate(resolve));

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
          'data:',
          'The preview image is self-contained',
        );
        assert.equal(visible(get('hotend-value')), true);
        assert.equal(
          visible(get('remaining-value')),
          phase === 'printing',
          'Only running prints show an ETA',
        );
        assert.equal(visible(get('progress-bar')), true);
        assert.equal(visible(get('stats-message')), phase === 'preparing');
        assert.equal(visible(get('plan-value')), phase === 'preparing');
        if (phase === 'preparing') {
          assert.match(get('plan-value').textContent, /35m print plan \+ preparation/);
          assert.equal(visible(get('finish-value')), true);
          assert.match(get('finish-value').textContent, /^after /);
          assert.equal(visible(get('finish-unknown')), false);
          assert.equal(get('progress-bar').hasAttribute('aria-valuenow'), false);
          assert.equal(
            get('progress-bar').getAttribute('aria-valuetext'),
            'Preparation in progress',
          );
          assert.equal(get('elapsed-value').textContent, '01:36');
        }
        assert.equal(visible(get('tool-value')), phase !== 'complete');
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
  console.log(
    'Four compiled screenshot states render and keyboard switching makes no host requests.',
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
