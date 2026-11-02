const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const directory = process.argv[2];
for (const [index, state] of ['printing', 'preparing', 'paused', 'complete'].entries()) {
  const page = fs.readFileSync(`${directory}/${state}.html`, 'utf8');
  const elements = new Map(), events = {};
  const get = id => {
    if (!elements.has(id)) elements.set(id, {style:{}, hidden:false,
      setAttribute(key,value) {this[key]=value;}, removeAttribute(key) {delete this[key];},
      click() {this.onclick?.();}});
    return elements.get(id);
  };
  vm.runInNewContext([...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]).join('\n'), {
    window:{Date,addEventListener(event,callback){events[event]=callback;}},
    document:{hidden:true,getElementById:get,addEventListener(event,callback){events[event]=callback;},
      body:{classList:{toggle(){}}}},
    setInterval(){},
    orca:{postMessage(){throw new Error('Screenshot page tried to contact the host');}}
  });
  events.DOMContentLoaded();
  assert.equal(get('stats')['data-state'], state);
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
  for (const [next, expected] of ['printing','preparing','paused','complete'].entries()) {
    events.keydown({key:String(next+1),target:{closest(){return false;}}});
    assert.equal(get('stats')['data-state'], expected);
  }
}
console.log('Four screenshot states render and keyboard switching makes no host requests.');
