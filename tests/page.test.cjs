const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const page = fs.readFileSync(`${__dirname}/../src/camera.html`, 'utf8');
const script = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]).join('\n');
const elements = new Map();
const events = {};
const sent = [];
let handler, timer;
const document = {
  hidden: false,
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, { hidden: true, disabled: true,
      removeAttribute(name) { delete this[name]; },
      setAttribute(name, value) { this[name] = value; } });
    return elements.get(id);
  },
  addEventListener: (event, callback) => events[event] = callback,
};
vm.runInNewContext(script, {
  document, Date,
  window: { addEventListener: (event, callback) => events[event] = callback },
  orca: { onMessage: callback => handler = callback, postMessage: data => sent.push(data) },
  setInterval: callback => timer = callback,
});
const el = id => elements.get(id);
assert.equal(sent.at(-1).active, true, 'Visible page requests status');
assert.equal(sent.at(-1).playing, false, 'Playback starts only with Play');
handler({ type: 'target', generation: 1, target: 'printer' });
handler({ generation: 1, printer: 'standby', light: false });
el('light').onclick();
assert.equal(sent.at(-1).type, 'light');
assert.equal(sent.at(-1).enabled, true, 'Light toggle works before playing');
handler({ generation: 1, light: true });
assert.equal(el('light')['aria-checked'], 'true');
handler({ generation: 1, failed: true, camera: 'Light request failed' });
assert.equal(el('camera').textContent, 'Light request failed', 'Light errors remain visible while stopped');
el('play').onclick();
assert.equal(sent.at(-1).playing, true);
assert.equal(el('play').hidden, true);
assert.equal(el('stop').hidden, false);
handler({ type: 'target', generation: 2, target: 'printer' });
handler({ generation: 2, image: 'fresh', camera: 'Live' });
assert.notEqual(sent.at(-1).type, 'frame', 'Wait until the image has loaded');
el('image').onload();
assert.equal(el('image').hidden, false);
assert.equal(sent.at(-1).type, 'frame');
assert.equal(sent.at(-1).generation, 2);
// Simulate app focus changes. Playback depends on page visibility only.
if (events.blur) events.blur();
timer();
assert.equal(sent.at(-1).playing, true, 'Unfocusing the app does not stop playback');
el('stop').onclick();
assert.equal(sent.at(-1).playing, false, 'Stop ends playback');
assert.equal(el('image').hidden, true);
assert.equal(el('play').hidden, false);
handler({ generation: 2, image: 'late' });
assert.equal(el('image').src, undefined, 'Late frames cannot restart playback');
el('play').onclick();
handler({ type: 'target', generation: 3, target: 'other printer' });
handler({ generation: 2, image: 'wrong printer' });
assert.equal(el('image').src, undefined, 'Old target frames are discarded');
handler({ generation: 3, image: 'new printer' });
assert.equal(el('image').src, 'new printer');
document.hidden = true;
events.visibilitychange();
assert.equal(sent.at(-1).active, false, 'Leaving the page stops all monitoring');
assert.equal(el('image').hidden, true);
const count = sent.length;
el('image').onload();
timer();
assert.equal(sent.length, count, 'Hidden page sends no frame acknowledgements or keepalive');
document.hidden = false;
events.visibilitychange();
assert.equal(sent.at(-1).active, true);
assert.equal(sent.at(-1).playing, false, 'Returning presents Play instead of restarting');
handler({ type: 'target', generation: 4, target: 'printer' });
el('play').onclick();
handler({ type: 'selection_changed' });
assert.equal(sent.at(-1).playing, false, 'Changing printers stops playback immediately');
handler({ error: 'Select a Snapmaker U1 printer in Prepare.', supported: false });
assert.equal(el('play').disabled, true);
assert.equal(el('play').hidden, true, 'Wrong printer hides the Play overlay');
assert.equal(el('warning').hidden, false, 'Wrong printer shows a warning instead');
assert.equal(el('light').disabled, true);
const beforePlay = sent.length;
el('play').onclick();
assert.equal(sent.length, beforePlay, 'Other printer selections cannot start the camera');
document.hidden = true;
events.visibilitychange();
document.hidden = false;
events.visibilitychange();
assert.equal(el('play').hidden, true, 'Returning to an unsupported printer keeps Play hidden');
assert.equal(el('warning').hidden, false);
handler({ error: 'Set the selected printer address, or set "host" in plugin Config.', supported: true });
assert.equal(el('warning').hidden, false, 'A missing address shows a warning for any preset');
assert.equal(el('target').textContent, 'Printer address unavailable');
assert.equal(el('warning-label').textContent, 'Set the selected printer address, or set "host" in plugin Config.');
assert.equal(el('play').hidden, true);
assert.equal(el('play').disabled, true, 'Play waits for a configured printer address');
handler({ type: 'target', generation: 5, target: 'U1 printer' });
assert.equal(el('play').disabled, false, 'Selecting U1 makes Play available again');
assert.equal(el('play-label').textContent, 'Play camera');
assert.equal(el('play').hidden, false);
assert.equal(el('warning').hidden, true, 'Selecting U1 clears the warning');
el('play').onclick();
assert.equal(sent.at(-1).playing, true, 'A resolved explicit host enables playback');
handler({ generation: 5, light: false });
assert.equal(el('light').disabled, false, 'A resolved explicit host enables the light');
handler({ error: 'Set host in plugin Config.' });
assert.equal(el('warning').hidden, true, 'Connection errors do not label a U1 as the wrong printer');
console.log('Play/Stop, light toggle, display pacing, page visibility, and target-switch checks passed.');
