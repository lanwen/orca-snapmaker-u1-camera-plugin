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
      const get = (id) => dom.window.document.getElementById(id);
      assert.equal(get('stats').dataset.state, state);
      assert.equal(get('play').hidden, true, 'Screenshot starts playback automatically');
      assert.ok(get('image').src.startsWith('data:image/svg+xml;base64,'));
      assert.equal(get('material-value').textContent, 'PLA');
      assert.equal(get('tool-value').textContent, 'T3');
      if (state === 'complete') {
        assert.equal(get('progress-value').textContent, '100%');
        assert.equal(get('layers-value').textContent, '86 / 86');
        assert.equal(get('tool-stat').hidden, true);
      }
      if (state === 'preparing') {
        assert.equal(get('stats-message').textContent, 'Heating up');
        assert.equal(get('hotend-value').textContent, '157 / 210 °C');
      }
      if (state === 'paused') assert.equal(get('remaining-stat').hidden, true);
      for (const [next, expected] of ['printing', 'preparing', 'paused', 'complete'].entries()) {
        dom.window.document.body.dispatchEvent(
          new dom.window.KeyboardEvent('keydown', { key: String(next + 1), bubbles: true }),
        );
        await settle();
        assert.equal(get('stats').dataset.state, expected);
      }
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
