import assert from 'node:assert/strict';
import { Blob as NodeBlob } from 'node:buffer';
import { afterEach, beforeEach, test, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import App from '../src/ui/App.svelte';
import type { HostMessage, OrcaBridge } from '../src/ui/types';

let app: ReturnType<typeof mount>;
let receive: (data: HostMessage) => void;
let sent: Parameters<OrcaBridge['postMessage']>[0][];
let activeURLs: Set<string>;

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  activeURLs = new Set();
  let serial = 0;
  vi.stubGlobal('Blob', NodeBlob);
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn((_blob: Blob) => {
        const url = 'blob:frame-' + ++serial;
        activeURLs.add(url);
        return url;
      });
      static revokeObjectURL = vi.fn((url: string) => activeURLs.delete(url));
    },
  );
  sent = [];
  window.orca = {
    onMessage: (callback) => {
      receive = callback;
    },
    postMessage: (data) => sent.push(data),
  };
  app = mount(App, { target: document.body });
  flushSync();
});

afterEach(async () => {
  await unmount(app);
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

// These helpers interact with the rendered page and host protocol, never Camera internals.
const element = (selector: string) => {
  const result = document.querySelector<HTMLElement>(selector);
  assert.ok(result, `Expected rendered element: ${selector}`);
  return result;
};
const field = (id: string) => element(`#${id}`);
const visible = (node: HTMLElement) => !node.closest('[hidden]');
const button = (action: 'play' | 'stop') =>
  element(`button[aria-label*="${action}" i]`) as HTMLButtonElement;
const light = () => element('[role="switch"]') as HTMLButtonElement;
const image = () => element('img[alt]') as HTMLImageElement;
const progress = () => element('[role="progressbar"]');
const numbers = (node: HTMLElement) => (node.textContent?.match(/\d+\.\d+|\d+/g) ?? []).map(Number);
const publish = (data: HostMessage) => {
  if (data.image && !data.image.startsWith('data:'))
    data = { ...data, image: 'data:image/jpeg;base64,' + btoa(data.image) };
  if (data.stats && !data.stats.tools) {
    const stats = { ...data.stats };
    data = { ...data, stats };
    stats.tools = Array.from({ length: 4 }, (_, id) => ({
      state: id === stats.tool ? 'connected' : 'unknown',
      ...(id === stats.tool
        ? {
            material: stats.material,
            material_vendor: stats.material_vendor,
            material_subtype: stats.material_subtype,
            material_profile: stats.material_profile,
            color: stats.color,
            heater: stats.hotend,
          }
        : {}),
    }));
    if (stats.tool === undefined && stats.hotend)
      stats.tools[0] = { state: 'connected', heater: stats.hotend };
  }
  flushSync(() => receive(data));
};
const click = (node: HTMLElement) => flushSync(() => node.click());
const advance = (milliseconds: number) => flushSync(() => vi.advanceTimersByTime(milliseconds));
const settleFrame = (outcome: 'load' | 'error' = 'load') =>
  flushSync(() => image().dispatchEvent(new Event(outcome)));
const setVisible = (shown: boolean) => {
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(!shown);
  flushSync(() => document.dispatchEvent(new Event('visibilitychange')));
};
const selectPrinter = (generation = 1, target = 'Workshop printer') =>
  publish({ type: 'target', generation, target });
const lastMessage = () => {
  assert.ok(sent.length);
  return sent.at(-1)!;
};
const frames = () => sent.filter((message) => message.type === 'frame');
const metrics = {
  state: 'printing',
  phase: 'printing' as const,
  planned_total: 2081,
  progress: 0.426,
  current_layer: 128,
  total_layer: 300,
  elapsed: 4200,
  remaining: 6000,
  tool: 2,
  material: 'PETG',
  color: '#12ab34',
  bed: { temperature: 69.7, target: 70 },
  hotend: { temperature: 234.5, target: 240 },
};
const start = () => {
  selectPrinter();
  click(button('play'));
};

test('opening the page monitors status but requires an explicit Play to fetch camera frames', () => {
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: false });
  assert.equal(button('play').disabled, true);
  selectPrinter();
  assert.equal(button('play').disabled, false);
  assert.equal(visible(button('play')), true);
  assert.equal(visible(button('stop')), false);
  click(button('play'));
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: true, generation: 1 });
  assert.equal(visible(button('play')), false);
  assert.equal(visible(button('stop')), true);
});

test('activity renewals carry the selected generation while a reappearing page handshakes afresh', () => {
  selectPrinter(4);
  advance(1000);
  assert.deepEqual(lastMessage(), {
    type: 'activity',
    active: true,
    playing: false,
    generation: 4,
  });
  setVisible(false);
  assert.deepEqual(lastMessage(), { type: 'activity', active: false, generation: 4 });
  setVisible(true);
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: false });
  publish({ type: 'selection_changed' });
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: false });
});

test('an unavailable target restarts the handshake so corrected configuration can recover', () => {
  selectPrinter(7);
  publish({ error: 'Connection details are missing.', supported: true });
  advance(1000);
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: false });
  selectPrinter(8, 'Recovered printer');
  publish({ generation: 8, stats: metrics, light: false });
  advance(1000);
  assert.deepEqual(lastMessage(), {
    type: 'activity',
    active: true,
    playing: false,
    generation: 8,
  });
  assert.equal(button('play').disabled, false);
  assert.equal(visible(field('hardware-stat')), true);
});

test('status measurements expire at the ten-second boundary even for an epoch-zero receipt', () => {
  vi.setSystemTime(0);
  selectPrinter();
  publish({ generation: 1, stats: metrics, light: true });
  advance(9999);
  assert.equal(visible(field('hardware-stat')), true);
  advance(1);
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(visible(progress()), false);
  assert.equal(light().disabled, true);
});

test('header status uses compact labels and only elapsed time after a successful reading is interrupted', () => {
  assert.equal(document.querySelector('header')!.contains(field('status-delivery')), true);
  assert.equal(field('status-delivery-value').textContent, 'Waiting');
  selectPrinter();
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(visible(field('status-delivery-value')), false);
  assert.equal(field('printer').textContent, field('stats-state').textContent);
  assert.equal(visible(field('status-age')), true);
  publish({ generation: 1, stats: metrics });
  assert.equal(field('status-delivery').dataset.state, 'fresh');
  assert.equal(visible(field('status-delivery-value')), false);
  assert.equal(visible(field('status-age')), false);
  advance(3000);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(visible(field('status-delivery-value')), false);
  assert.equal(field('status-age').textContent, '…0s');
  assert.equal(field('printer').nextElementSibling, field('status-age'));
  assert.equal(field('status-age').parentElement?.matches('.printer-meta'), true);
  assert.equal(field('status-age').closest('[aria-live]:not([aria-live="off"])'), null);
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('status-delivery-value')), false);
  assert.equal(visible(field('status-age')), false);
});

test('first failure starts an interruption clock without inventing a successful reading', () => {
  vi.setSystemTime(0);
  selectPrinter();
  assert.equal(visible(field('status-age')), false);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(visible(field('status-age')), true);
  assert.equal(field('status-age').textContent, '…0s');
  advance(3000);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(field('status-age').textContent, '…3s');
});

test('continuous fresh printing to paused starts only an observed pause clock', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  advance(3000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(field('status-age').textContent, '…0s');
  assert.equal(visible(field('status-age')), true);
  assert.match(field('status-age').getAttribute('title')!, /observed/i);
  advance(2000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(field('status-age').textContent, '…2s');
});

test('an observed pause at epoch zero survives fresh samples and camera frames', () => {
  vi.setSystemTime(0);
  start();
  publish({ generation: 1, stats: metrics });
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  advance(7000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  publish({ generation: 1, image: 'live camera', fps: 4 });
  settleFrame();
  advance(7000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(field('status-age').textContent, '…14s');
  assert.match(field('status-age').title, /observed/i);
});

test('late failed status anchors interruption to the known expiry deadline', () => {
  vi.setSystemTime(0);
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  vi.setSystemTime(15000);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(field('status-age').textContent, '…5s');
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('status-age')), false);
  advance(2000);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(field('status-age').textContent, '…0s');
  advance(3000);
  publish({ generation: 1, stats: null, status_error: 'access_denied' });
  selectPrinter();
  assert.equal(field('status-age').textContent, '…3s');
});

for (const previous of [
  { ...metrics, phase: 'preparing' as const },
  { ...metrics, state: 'preparing' },
]) {
  test('fresh preparation can establish an observed pause transition', () => {
    selectPrinter();
    publish({ generation: 1, stats: previous });
    advance(1000);
    publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
    assert.equal(field('status-age').textContent, '…0s');
    assert.equal(visible(field('status-age')), true);
  });
}

test('an already paused reading cannot reveal its current pause duration from cumulative clocks', () => {
  selectPrinter();
  publish({
    generation: 1,
    stats: { ...metrics, state: 'paused', elapsed: 50000, printing_duration: 2 },
  });
  assert.equal(visible(field('status-age')), false);
  advance(2000);
  publish({
    generation: 1,
    stats: { ...metrics, state: 'paused', elapsed: 90000, printing_duration: 5 },
  });
  assert.equal(visible(field('status-age')), false);
  publish({ generation: 1, stats: metrics });
  advance(1000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(visible(field('status-age')), true);
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('status-age')), false);
});

for (const end of ['standby', 'complete', 'cancelled', 'error', 'unknown']) {
  test(`a ${end} reading breaks the observed pause run`, () => {
    selectPrinter();
    publish({ generation: 1, stats: metrics });
    publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
    assert.equal(visible(field('status-age')), true);
    publish({ generation: 1, stats: { ...metrics, state: end } });
    assert.equal(visible(field('status-age')), false);
    publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
    assert.equal(visible(field('status-age')), false);
  });
}

test('status loss replaces observed pause age and paused recovery leaves pause start unknown', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  advance(3000);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(field('status-age').textContent, '…0s');
  assert.match(field('status-age').getAttribute('title')!, /unavailable/i);
  advance(1000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(visible(field('status-age')), false);
});

test('a fresh paused sample after an unobserved expiry gap cannot establish or continue a pause clock', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  vi.setSystemTime(Date.now() + 10000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(visible(field('status-age')), false);
  publish({ generation: 1, stats: metrics });
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(visible(field('status-age')), true);
  vi.setSystemTime(Date.now() + 10000);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.equal(visible(field('status-age')), false);
});

for (const reset of ['generation', 'target', 'selection', 'hide', 'unsupported']) {
  test(`${reset} clears an observed pause clock`, () => {
    selectPrinter();
    publish({ generation: 1, stats: metrics });
    publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
    assert.equal(visible(field('status-age')), true);
    let generation = 1;
    if (reset === 'generation') selectPrinter((generation = 2));
    if (reset === 'target') selectPrinter(1, 'Another target');
    if (reset === 'selection') {
      publish({ type: 'selection_changed' });
      selectPrinter((generation = 2));
    }
    if (reset === 'hide') {
      setVisible(false);
      setVisible(true);
      selectPrinter((generation = 2));
    }
    if (reset === 'unsupported') {
      publish({ error: 'Unsupported target', supported: false });
      selectPrinter((generation = 2));
    }
    publish({ generation, stats: { ...metrics, state: 'paused' } });
    assert.equal(visible(field('status-age')), false);
  });
}

test('status delivery waits for an actual reading rather than a target, heartbeat or camera frame', () => {
  assert.equal(visible(field('status-delivery')), true);
  assert.equal(field('status-delivery').dataset.state, 'waiting');
  assert.equal(visible(field('status-age')), false);
  selectPrinter();
  click(button('play'));
  advance(3000);
  publish({ generation: 1, image: 'live camera', fps: 4 });
  settleFrame();
  assert.equal(field('camera').dataset.state, 'live');
  assert.equal(field('status-delivery').dataset.state, 'waiting');
  assert.equal(visible(field('status-age')), false);
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(visible(progress()), false);
});

test('a failed first status shows interruption time without inventing prior success', () => {
  selectPrinter();
  publish({ generation: 1, stats: null, status_error: 'invalid_response' });
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.equal(visible(field('status-age')), true);
  assert.match(field('stats-message').textContent!, /response/i);
  assert.equal(field('status-age').textContent, '…0s');
  advance(15000);
  assert.equal(field('status-age').textContent, '…15s');
  assert.equal(visible(field('status-age')), true);
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(light().disabled, true);
});

test('status interruption shows only its interruption age while camera frames remain live', () => {
  start();
  publish({ generation: 1, stats: { ...metrics, psu_fan: { speed: 0.6 } }, light: true });
  assert.equal(field('status-delivery').dataset.state, 'fresh');
  advance(3000);
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.equal(visible(field('status-age')), true);
  assert.match(field('status-age').textContent!, /\b0s\b/);
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(visible(progress()), false);
  assert.equal(visible(field('elapsed-value')), false);
  assert.equal(document.querySelector('.fan-islands'), null);
  assert.equal(light().disabled, true);
  advance(2000);
  publish({ generation: 1, image: 'continuing camera', fps: 5 });
  settleFrame();
  assert.equal(field('camera').dataset.state, 'live');
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.match(field('status-age').textContent!, /\b2s\b/);
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(light().disabled, true);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' }, light: false });
  assert.equal(field('status-delivery').dataset.state, 'fresh');
  assert.equal(visible(field('status-age')), false);
  assert.equal(visible(field('hardware-stat')), true);
  assert.equal(light().disabled, false);
  assert.equal(field('stats-state').dataset.state, 'paused');
});

test('camera failures and Stop do not erase or renew fresh printer status', () => {
  start();
  publish({ generation: 1, stats: metrics, light: true });
  advance(6000);
  publish({ generation: 1, failed: true, camera: 'Camera connection interrupted' });
  assert.equal(field('status-delivery').dataset.state, 'fresh');
  assert.equal(visible(field('hardware-stat')), true);
  click(button('stop'));
  assert.equal(field('status-delivery').dataset.state, 'fresh');
  advance(4000);
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.match(field('status-age').textContent!, /\b0s\b/);
  assert.equal(visible(field('hardware-stat')), false);
});

test('camera frames cannot postpone status or independent light expiry', () => {
  start();
  publish({ generation: 1, stats: metrics, light: true });
  advance(9000);
  publish({ generation: 1, image: 'fresh camera', fps: 3 });
  settleFrame();
  advance(1000);
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.match(field('status-age').textContent!, /\b0s\b/);
  assert.equal(light().disabled, true);
  publish({ generation: 1, light: false });
  assert.equal(light().disabled, false);
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  advance(10000);
  publish({ generation: 1, image: 'another camera frame', fps: 3 });
  settleFrame();
  assert.equal(light().disabled, true);
  assert.equal(visible(field('hardware-stat')), false);
  assert.match(field('status-age').textContent!, /\b10s\b/);
});

for (const [seconds, expected] of [
  [12, '12s'],
  [61, '1m'],
  [3600, '1h'],
] as const) {
  test(`interruption age uses elapsed time and units at ${seconds} seconds`, () => {
    selectPrinter();
    publish({ generation: 1, stats: metrics });
    publish({ generation: 1, stats: null, status_error: 'timeout' });
    vi.setSystemTime(Date.now() + seconds * 1000);
    flushSync(() => window.dispatchEvent(new Event('pageshow')));
    assert.equal(field('status-age').textContent, '…' + expected);
    assert.doesNotMatch(field('status-age').textContent!, /NaN|undefined/);
  });
}

for (const reset of ['generation', 'target', 'selection', 'hide', 'unsupported'] as const) {
  test(`${reset} resets printer status history rather than attributing it to the next session`, () => {
    selectPrinter();
    publish({ generation: 1, stats: metrics, light: true });
    const previousJob = field('printer').textContent;
    advance(3000);
    publish({ generation: 1, stats: null, status_error: 'timeout' });
    assert.equal(visible(field('status-age')), true);
    if (reset === 'generation') selectPrinter(2);
    if (reset === 'target') selectPrinter(1, 'Another target');
    if (reset === 'selection') publish({ type: 'selection_changed' });
    if (reset === 'hide') {
      setVisible(false);
      setVisible(true);
    }
    if (reset === 'unsupported') {
      publish({ error: 'Selected target is unavailable.', supported: false });
      assert.equal(visible(field('status-delivery')), false);
      selectPrinter(2, 'Available target');
    }
    assert.equal(field('status-delivery').dataset.state, 'waiting');
    assert.equal(visible(field('status-age')), false);
    assert.equal(visible(field('hardware-stat')), false);
    assert.equal(visible(progress()), false);
    assert.equal(light().disabled, true);
    assert.notEqual(field('printer').textContent, previousJob);
  });
}

test('repeated current announcements and stale generations cannot fake fresh status', () => {
  selectPrinter(2, 'Current target');
  publish({ generation: 2, stats: metrics });
  advance(3000);
  publish({ generation: 2, stats: null, status_error: 'timeout' });
  selectPrinter(2, 'Current target');
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.match(field('status-age').textContent!, /\b0s\b/);
  selectPrinter(1, 'Old target');
  publish({ generation: 1, stats: metrics, light: true });
  assert.equal(field('target').textContent, 'Current target');
  assert.equal(field('status-delivery').dataset.state, 'retrying');
  assert.equal(light().disabled, true);
  publish({ generation: 2, stats: metrics });
  assert.equal(field('status-delivery').dataset.state, 'fresh');
});

for (const timer of ['interruption', 'observed pause']) {
  test(`${timer} age does not mutate the accessible state announcement`, async () => {
    selectPrinter();
    publish({ generation: 1, stats: metrics });
    publish(
      timer === 'interruption'
        ? { generation: 1, stats: null, status_error: 'timeout' }
        : { generation: 1, stats: { ...metrics, state: 'paused' } },
    );
    const label = field('status-delivery-label');
    const age = field('status-age');
    assert.equal(label.getAttribute('role'), 'status');
    assert.equal(label.getAttribute('aria-atomic'), 'true');
    assert.equal(age.getAttribute('aria-live'), 'off');
    assert.equal(age.closest('[role="status"]'), null);
    const mutations: MutationRecord[] = [];
    const observer = new MutationObserver((records) => mutations.push(...records));
    observer.observe(label, { childList: true, characterData: true, subtree: true });
    advance(3000);
    await tick();
    observer.disconnect();
    assert.equal(mutations.length, 0);
    assert.match(age.textContent!, /\b3s\b/);
  });
}

test('print statistics display incoming measurements while the camera is stopped', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(progress()), true);
  assert.equal(
    Number(progress().getAttribute('aria-valuenow')),
    42,
    'Progress rounds down to a whole percent',
  );
  assert.deepEqual(numbers(field('layers-value')), [128, 300]);
  assert.equal(field('elapsed-value').textContent, '1:10:00');
  assert.deepEqual(numbers(field('remaining-value')), [1, 40]);
  assert.equal(
    element('.chosen .material-copy strong').textContent,
    metrics.material,
    'Reported material reaches the user',
  );
  assert.deepEqual(numbers(element('.chosen .index-tab b')), [metrics.tool + 1]);
  assert.deepEqual(numbers(field('bed-value')), [70]);
  assert.deepEqual(numbers(field('hotend-value')), [240, 235]);
  assert.equal(visible(element('.chosen .swatch')), true);
  const swatch = element('.chosen .swatch').style.backgroundColor;
  publish({ generation: 1, stats: { ...metrics, color: '#ffffff' } });
  assert.notEqual(
    element('.chosen .swatch').style.backgroundColor,
    swatch,
    'The swatch follows the reported material color',
  );
  assert.equal(lastMessage().playing, false);
});

test('elapsed time shows whole reported seconds without inventing a ticking clock', () => {
  selectPrinter();
  for (const [elapsed, clock] of [
    [0, '00:00'],
    [59.9, '00:59'],
    [60, '01:00'],
    [3599, '59:59'],
    [3600, '1:00:00'],
    [4207.9, '1:10:07'],
    [NaN, '—'],
  ] as const) {
    publish({ generation: 1, stats: { ...metrics, elapsed } });
    assert.equal(field('elapsed-value').textContent, clock);
    advance(1000);
    assert.equal(field('elapsed-value').textContent, clock);
  }
});

test('preparation gives a dated after-time from the current slicer plan and no false percentage', () => {
  vi.setSystemTime(new Date(2026, 10, 4, 23, 50, 0));
  selectPrinter();
  publish({
    generation: 1,
    stats: { ...metrics, phase: 'preparing', current_layer: 0, remaining: null },
  });
  const baseline = new Date(Date.now() + metrics.planned_total * 1000);
  const expected =
    'after ' +
    baseline.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ', ' +
    baseline.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  assert.equal(field('finish-value').textContent, expected);
  assert.equal(progress().hasAttribute('aria-valuenow'), false);
  publish({
    generation: 1,
    stats: {
      ...metrics,
      phase: 'preparing',
      current_layer: 0,
      remaining: null,
      planned_total: null,
    },
  });
  assert.equal(
    visible(field('finish-value')),
    false,
    'Missing plans cannot create a finish baseline',
  );
  publish({
    generation: 1,
    stats: { ...metrics, state: 'paused', phase: 'preparing', remaining: null },
  });
  assert.equal(visible(field('finish-value')), false, 'A pause has no finish forecast');
  assert.equal(progress().getAttribute('aria-valuenow'), '42');
});

test('pausing hides moving estimates and resuming restores them', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  const activeLabel = field('stats-state').textContent;
  assert.equal(visible(field('remaining-value')), true);
  assert.equal(visible(field('finish-value')), true);
  publish({ generation: 1, stats: { ...metrics, state: 'paused' } });
  assert.notEqual(
    field('stats-state').textContent,
    activeLabel,
    'The user can distinguish a paused job',
  );
  assert.equal(visible(field('remaining-value')), false);
  assert.equal(visible(field('finish-value')), false);
  assert.equal(progress().hasAttribute('data-shimmer'), true);
  assert.equal(visible(progress()), true, 'Pausing retains completed progress');
  assert.equal(visible(element('.chosen .index-tab b')), true);
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('remaining-value')), true);
  assert.equal(visible(field('finish-value')), true);
});

for (const state of ['complete', 'cancelled', 'error']) {
  test(`${state} jobs retain measurements but hide running estimates and tool, and show cooling targets`, () => {
    selectPrinter();
    publish({
      generation: 1,
      stats: {
        ...metrics,
        state,
        progress: state === 'complete' ? 1 : metrics.progress,
        hotend: { temperature: 235, target: 0 },
      },
    });
    assert.equal(visible(field('remaining-value')), false);
    assert.equal(visible(field('finish-value')), false);
    assert.equal(document.querySelectorAll('[data-tool]').length, 4);
    assert.equal(progress().hasAttribute('data-shimmer'), false);
    assert.deepEqual(numbers(field('hotend-value')), [235]);
    assert.equal(field('hotend-value').querySelector('sub'), null);
    const cooling = field('hotend-value').querySelector('.temperature-arrow.cooling');
    assert.ok(cooling);
    assert.ok(cooling.closest('[title]')?.getAttribute('title')?.trim());
    assert.equal(visible(progress()), true);
    if (state === 'complete') {
      assert.equal(Number(progress().getAttribute('aria-valuenow')), 100);
      assert.equal(visible(element('.index-stack')), false);
      assert.equal(visible(field('hotend-value')), false);
      assert.equal(visible(field('bed-value')), true);
      assert.equal(document.querySelector('.filament-traces'), null);
    }
  });
}

test('heater targets show the direction only outside the temperature tolerance', () => {
  selectPrinter();
  for (const [temperature, target, displayed] of [
    [230, 240, [240, 230]],
    [238, 240, [238]],
    [250, 240, [240, 250]],
    [242, 240, [242]],
    [90, 0, [90]],
  ] as const) {
    publish({ generation: 1, stats: { ...metrics, hotend: { temperature, target } } });
    assert.deepEqual(numbers(field('hotend-value')), displayed);
    const changing = Math.abs(temperature - target) > 2;
    const tag = temperature < target ? 'sup' : 'sub';
    assert.equal(
      field('hotend-value').querySelector(tag)?.textContent ?? null,
      changing && target > 0 ? target + ' °C' : null,
    );
    assert.equal(
      field('hotend-value').querySelector('.temperature-arrow')?.textContent ?? null,
      changing ? (temperature < target ? '↑' : '↓') : null,
    );
  }
});

test('off heaters hide cooling indicators after a minute within one degree and resume on movement', () => {
  selectPrinter();
  const update = (bed: number, hotend: number, target = 0) =>
    publish({
      generation: 1,
      stats: {
        state: 'standby',
        bed: { temperature: bed, target },
        hotend: { temperature: hotend, target },
      },
    });
  update(24, 90);
  for (let seconds = 5; seconds <= 60; seconds += 5) {
    advance(5000);
    update(seconds % 10 === 0 ? 24 : 25, 90 - seconds / 5);
    assert.equal(field('bed-value').querySelector('.temperature-arrow') === null, seconds === 60);
    assert.ok(
      field('hotend-value').querySelector('.temperature-arrow'),
      'Falling temperatures keep cooling',
    );
  }
  assert.deepEqual(numbers(field('bed-value')), [24]);
  advance(5000);
  update(26, 77);
  assert.ok(field('bed-value').querySelector('.temperature-arrow'), 'Movement restores cooling');
  for (let seconds = 5; seconds <= 60; seconds += 5) {
    advance(5000);
    update(26, 77);
  }
  assert.equal(field('bed-value').querySelector('.temperature-arrow'), null);
  assert.equal(field('hotend-value').querySelector('.temperature-arrow'), null);
  advance(5000);
  update(26, 77, 20);
  assert.equal(field('bed-value').querySelector('sub')?.textContent, '20 °C');
  advance(5000);
  update(26, 77);
  assert.ok(
    field('bed-value').querySelector('.temperature-arrow'),
    'Changing the target resets stability',
  );
});

test('missing readings and printer changes cannot establish cooling stability', () => {
  selectPrinter();
  const update = (hotend = { temperature: 25, target: 0 }, generation = 1) =>
    publish({
      generation,
      stats: { state: 'standby', hotend },
    });
  update();
  for (let seconds = 5; seconds <= 55; seconds += 5) {
    advance(5000);
    update();
  }
  update({ temperature: NaN, target: 0 });
  advance(5000);
  update();
  assert.ok(field('hotend-value').querySelector('.temperature-arrow'));
  advance(11000);
  update();
  assert.ok(
    field('hotend-value').querySelector('.temperature-arrow'),
    'A status gap resets observations',
  );
  for (let seconds = 5; seconds <= 60; seconds += 5) {
    advance(5000);
    update();
  }
  assert.equal(field('hotend-value').querySelector('.temperature-arrow'), null);
  selectPrinter(2, 'Other printer');
  update(undefined, 2);
  assert.ok(
    field('hotend-value').querySelector('.temperature-arrow'),
    'A different printer starts fresh',
  );
});

test('the preparation bar is warm only while a reported heater is below target', () => {
  selectPrinter();
  for (const [bed, hotend, heating] of [
    [{ temperature: 40, target: 60 }, { temperature: 210, target: 210 }, true],
    [{ temperature: 58, target: 60 }, { temperature: 209, target: 210 }, false],
    [{ temperature: 60, target: 60 }, { temperature: 205, target: 210 }, true],
    [{ temperature: NaN, target: 60 }, { temperature: 210, target: 0 }, false],
  ] as const) {
    publish({
      generation: 1,
      stats: { ...metrics, phase: 'preparing', current_layer: 0, remaining: null, bed, hotend },
    });
    assert.equal(progress().hasAttribute('data-heating'), heating);
    assert.equal(progress().hasAttribute('aria-valuenow'), false);
  }
  publish({ generation: 1, stats: metrics });
  assert.equal(
    progress().hasAttribute('data-heating'),
    false,
    'Printing stays blue even if reheating',
  );
  assert.equal(field('progress-fill').style.width, metrics.progress * 100 + '%');
});

test('layer-zero purge keeps preparation and its plan separate from remaining time', () => {
  selectPrinter();
  publish({
    generation: 1,
    stats: {
      ...metrics,
      phase: 'preparing',
      printing_duration: 60,
      current_layer: 0,
      remaining: null,
    },
  });
  assert.equal(field('stats-state').textContent, 'Preparing');
  assert.equal(visible(progress()), true);
  assert.equal(progress().hasAttribute('aria-valuenow'), false);
  assert.match(progress().getAttribute('aria-valuetext')!, /prepar/i);
  assert.equal(visible(field('remaining-value')), false);
  assert.equal(visible(field('plan-value')), true);
  assert.match(field('plan-stat').textContent!, /35m\s*\+ preparation/);
  assert.equal(visible(field('elapsed-value')), true);
  assert.equal(visible(field('finish-value')), true);
  assert.match(field('finish-value').textContent!, /^after /);
  publish({ generation: 1, stats: { ...metrics, remaining: 1331.84 } });
  assert.equal(visible(progress()), true);
  assert.deepEqual(numbers(field('remaining-value')), [23]);
  assert.equal(visible(field('finish-value')), true);
  assert.equal(visible(field('plan-value')), false);
  const finish = field('finish-value').textContent;
  advance(3000);
  assert.equal(field('finish-value').textContent, finish, 'No countdown between snapshots');
});

for (const phase of ['printing', 'unknown', 'finishing'] as const) {
  test(`${phase} without an eligible estimate shows the known plan and hides unavailable estimates`, () => {
    selectPrinter();
    publish({ generation: 1, stats: { ...metrics, phase, remaining: null } });
    assert.equal(visible(field('remaining-value')), false);
    assert.equal(visible(field('plan-value')), true);
    assert.deepEqual(numbers(field('plan-value')), [35]);
    assert.equal(visible(field('finish-value')), false);
    if (phase === 'unknown') assert.equal(visible(field('stats-message')), false);
    if (phase === 'finishing') assert.equal(field('stats-state').textContent, 'Finishing');
    assert.equal(progress().hasAttribute('data-shimmer'), true);
  });
}

test('idle and missing status never present old job details as current', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  publish({ generation: 1, stats: { ...metrics, state: 'standby' } });
  assert.equal(visible(element('.index-stack')), false);
  assert.equal(document.querySelector('.filament-traces'), null);
  assert.equal(visible(field('bed-value')), true);
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(element('.index-stack')), true);
  publish({ generation: 1, stats: { state: 'standby', color: 'url(untrusted)' } });
  assert.equal(document.querySelector('.chosen .material-copy'), null);
  assert.equal(visible(field('layers-value')), false);
  assert.equal(visible(progress()), false);
  assert.equal(document.querySelector('.chosen .swatch'), null);
  assert.equal(
    progress().hasAttribute('aria-valuenow'),
    false,
    'Missing progress is not announced as zero',
  );
  publish({ generation: 1, stats: null });
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(visible(field('hotend-value')), false);
  assert.equal(visible(field('elapsed-value')), false);
  assert.equal(visible(field('stats-message')), true);
  assert.ok(field('stats-message').textContent?.trim(), 'Missing status has an explanation');
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('hotend-value')), true);
  assert.equal(visible(field('elapsed-value')), true);
  advance(11000);
  assert.equal(visible(field('hardware-stat')), false, 'Status expires even without playback');
  assert.equal(visible(field('elapsed-value')), false);
});

for (const [category, meaning] of [
  ['timeout', /time.*out/i],
  ['access_denied', /access|denied/i],
  ['invalid_response', /response/i],
  ['unreachable', /address|connection/i],
  ['untrusted-secret', /address|connection/i],
  ['__proto__', /address|connection/i],
  [undefined, /address|connection/i],
] as const) {
  test(`status failure ${category} shows safe guidance and recovers independently of the camera`, () => {
    start();
    publish({ generation: 1, stats: metrics, light: true });
    publish({
      generation: 1,
      printer: 'Status unavailable',
      stats: null,
      light: null,
      status_error: category,
    });
    const guidance = field('stats-message').textContent!.trim();
    assert.equal(visible(field('stats-message')), true);
    assert.ok(guidance);
    assert.match(guidance, meaning);
    assert.equal(visible(field('hardware-stat')), false);
    assert.equal(light().disabled, true);
    publish({ generation: 1, image: 'still live' });
    settleFrame();
    publish({ generation: 1, camera: 'Camera unavailable', failed: true });
    assert.equal(field('stats-message').textContent!.trim(), guidance);
    assert.doesNotMatch(document.body.textContent!, /untrusted-secret|__proto__/);
    publish({ generation: 1, stats: metrics, light: false });
    assert.equal(visible(field('stats-message')), false);
    assert.equal(visible(field('hardware-stat')), true);
    assert.equal(light().disabled, false);
  });
}

test('known status categories have distinct guidance and unknown inputs use the generic fallback', () => {
  selectPrinter();
  const guidance = ['timeout', 'access_denied', 'invalid_response', 'unreachable'].map(
    (category) => {
      publish({ generation: 1, stats: null, status_error: category });
      assert.equal(visible(field('stats-message')), true);
      const text = field('stats-message').textContent!.trim();
      assert.ok(text);
      return text;
    },
  );
  assert.equal(new Set(guidance).size, guidance.length);
  const generic = guidance.at(-1);
  for (const category of ['untrusted-secret', '__proto__', undefined]) {
    publish({ generation: 1, stats: null, status_error: category });
    assert.equal(field('stats-message').textContent!.trim(), generic);
    assert.doesNotMatch(document.body.textContent!, /untrusted-secret|__proto__/);
  }
  publish({ generation: 1, stats: null });
  assert.equal(field('stats-message').textContent!.trim(), generic);
});

test('status failures reset on selection and target changes and reject old generations', () => {
  selectPrinter();
  publish({ generation: 1, stats: null, status_error: 'timeout' });
  const guidance = field('stats-message').textContent;
  assert.ok(guidance?.trim());
  publish({ type: 'selection_changed' });
  assert.equal(visible(field('stats-message')), false);
  assert.notEqual(field('stats-message').textContent, guidance);
  publish({ generation: 1, stats: null, status_error: 'access_denied' });
  assert.equal(
    visible(field('stats-message')),
    false,
    'Selection rejects the previous generation before the next target arrives',
  );
  selectPrinter(2, 'Next printer');
  publish({ generation: 2, stats: metrics, light: true });
  publish({ generation: 1, stats: null, light: null, status_error: 'access_denied' });
  assert.equal(visible(field('hardware-stat')), true);
  assert.equal(light().disabled, false);
  publish({ generation: 2, stats: null, status_error: 'timeout' });
  assert.equal(field('stats-message').textContent, guidance);
  selectPrinter(3, 'Third printer');
  assert.equal(visible(field('stats-message')), false);
  assert.notEqual(field('stats-message').textContent, guidance);
});

test('unknown or invalid measurements stay unknown rather than becoming NaN or zero progress', () => {
  selectPrinter();
  publish({
    generation: 1,
    stats: {
      state: 'printing',
      progress: NaN,
      current_layer: -1,
      tool: 9,
      color: 'url(untrusted)',
    },
  });
  assert.equal(progress().hasAttribute('aria-valuenow'), false);
  for (const id of ['layers-value', 'remaining-value']) {
    assert.equal(visible(field(id)), false, `${id} hides missing measurements`);
    assert.deepEqual(numbers(field(id)), [], `${id} does not invent a measurement`);
    assert.doesNotMatch(field(id).textContent!, /NaN|undefined|null/);
  }
  assert.equal(document.querySelector('.chosen .swatch'), null);
  assert.equal(document.querySelector('.chosen .material-copy'), null);
  assert.equal(visible(field('elapsed-value')), false);
  assert.equal(visible(field('progress-value')), false);
  assert.equal(progress().hasAttribute('data-shimmer'), true);
  publish({ generation: 1, stats: { ...metrics, progress: 1.5 } });
  assert.equal(
    Number(progress().getAttribute('aria-valuenow')),
    100,
    'Progress cannot exceed completion',
  );
});

test('a known heater target is shown without inventing a current temperature', () => {
  selectPrinter();
  publish({ generation: 1, stats: { ...metrics, bed: { target: 60 }, hotend: { target: 210 } } });
  assert.equal(field('bed-value').textContent, 'Target 60 °C');
  assert.equal(field('hotend-value').textContent, 'Target 210 °C');
  assert.equal(visible(field('hotend-value')), true);
  publish({ generation: 1, stats: { ...metrics, state: 'complete', bed: {}, hotend: {} } });
  assert.equal(visible(field('bed-value')), false);
  assert.equal(visible(field('hotend-value')), false);
});

test('light toggles are host-confirmed and work without starting playback', () => {
  selectPrinter();
  publish({ generation: 1, light: false });
  click(light());
  assert.deepEqual(lastMessage(), { type: 'light', enabled: true, generation: 1 });
  assert.equal(light().getAttribute('aria-checked'), 'false', 'A request is not confirmation');
  publish({ generation: 1, light: true });
  assert.equal(light().getAttribute('aria-checked'), 'true');
  click(light());
  assert.deepEqual(lastMessage(), { type: 'light', enabled: false, generation: 1 });
  const failure = 'The printer rejected the light command';
  publish({ generation: 1, light_error: failure });
  assert.equal(
    field('light-error').textContent,
    failure,
    'Light failures appear beside the control',
  );
  publish({ generation: 1, light: false });
  assert.equal(
    field('light-error').textContent,
    failure,
    'Polling does not dismiss a failed action',
  );
  click(light());
  assert.equal(visible(field('light-error')), false, 'Retry clears the previous action error');
  assert.equal(
    sent.some((message) => message.type === 'activity' && message.playing),
    false,
  );
});

for (const confirmed of [false, true]) {
  for (const withStats of [false, true]) {
    test(`light ${confirmed} expires and recovers with ${withStats ? 'combined' : 'light-only'} updates`, () => {
      selectPrinter();
      publish({ generation: 1, light: confirmed, ...(withStats ? { stats: metrics } : {}) });
      assert.equal(light().disabled, false);
      advance(6000);
      click(button('play'));
      publish({ generation: 1, image: 'frame' });
      settleFrame();
      selectPrinter();
      publish({ generation: 1, stats: metrics });
      advance(5000);
      assert.equal(light().disabled, true, 'Unrelated messages cannot renew the light reading');
      assert.equal(
        light().getAttribute('aria-checked'),
        'false',
        'Expired light has no current value',
      );
      const commands = sent.filter((message) => message.type === 'light').length;
      click(light());
      assert.equal(sent.filter((message) => message.type === 'light').length, commands);
      publish({ generation: 0, light: confirmed });
      assert.equal(light().disabled, true, 'An old generation cannot restore the control');
      publish({ generation: 1, light: !confirmed });
      assert.equal(light().disabled, false);
      assert.equal(light().getAttribute('aria-checked'), String(!confirmed));
      click(light());
      assert.deepEqual(lastMessage(), { type: 'light', enabled: confirmed, generation: 1 });
    });
  }
}

test('light-only updates renew light freshness while stopped without renewing statistics', () => {
  selectPrinter();
  publish({ generation: 1, light: true, stats: metrics });
  advance(6000);
  publish({ generation: 1, light: false });
  advance(5000);
  assert.equal(light().disabled, false);
  assert.equal(light().getAttribute('aria-checked'), 'false');
  assert.equal(visible(field('hardware-stat')), false, 'Light updates do not renew statistics');
  advance(6000);
  assert.equal(light().disabled, true);
  publish({ generation: 1, light: true });
  assert.equal(light().disabled, false);
  assert.equal(light().getAttribute('aria-checked'), 'true');
  assert.equal(
    sent.some((message) => message.type === 'activity' && message.playing),
    false,
  );
});

test('hidden light updates cannot restore an expired light on return', () => {
  selectPrinter();
  publish({ generation: 1, light: true });
  setVisible(false);
  advance(11000);
  publish({ generation: 1, light: true });
  setVisible(true);
  publish({ generation: 1, stats: metrics });
  assert.equal(light().disabled, true);
  assert.equal(light().getAttribute('aria-checked'), 'false');
  assert.equal(visible(button('play')), true, 'Returning does not start camera playback');
  selectPrinter(2);
  publish({ generation: 2, light: false });
  assert.equal(light().disabled, false);
  assert.equal(light().getAttribute('aria-checked'), 'false');
});

test('an unavailable light cannot send commands and keeps its last confirmed state', () => {
  assert.equal(light().disabled, true);
  const initialMessages = sent.length;
  click(light());
  assert.equal(sent.length, initialMessages);
  selectPrinter();
  publish({ generation: 1, light: true });
  publish({ generation: 1, error: 'Printer is unreachable' });
  assert.equal(light().disabled, true);
  assert.equal(
    light().getAttribute('aria-checked'),
    'true',
    'Loss of connectivity does not imply the lamp turned off',
  );
  const disconnectedMessages = sent.length;
  click(light());
  assert.equal(sent.length, disconnectedMessages);
  publish({ generation: 1, light: false });
  assert.equal(light().disabled, false);
  assert.equal(light().getAttribute('aria-checked'), 'false');
  click(light());
  assert.deepEqual(lastMessage(), { type: 'light', enabled: true, generation: 1 });
});

for (const outcome of ['load', 'error'] as const) {
  test(`frames are acknowledged after image ${outcome}, allowing the next frame`, () => {
    start();
    publish({ generation: 1, image: 'frame-one' });
    assert.equal(frames().length, 0);
    settleFrame(outcome);
    assert.deepEqual(frames(), [{ type: 'frame', generation: 1 }]);
    assert.equal(visible(image()), outcome === 'load');
    if (outcome === 'error')
      assert.ok(field('camera').textContent?.trim(), 'A failed image has an explanation');
    publish({ generation: 1, image: 'frame-two' });
    settleFrame();
    assert.equal(visible(image()), true, 'The feed recovers with the next frame');
    assert.equal(frames().length, 2);
  });
}

test('camera frames use a decoded image Blob and release its URL after loading', async () => {
  start();
  publish({ generation: 1, image: 'jpeg bytes', fps: 4 });
  assert.match(image().getAttribute('src')!, /^blob:/);
  const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
  assert.equal(blob.type, 'image/jpeg');
  assert.equal(await blob.text(), 'jpeg bytes');
  assert.equal(activeURLs.size, 1);
  settleFrame();
  assert.equal(activeURLs.size, 0);
  assert.equal(visible(image()), true, 'Revoking the URL retains the loaded frame');
  assert.equal(image().onload, null, 'A finished frame releases its callback payload');
  assert.equal(image().onerror, null);
});

for (const action of ['error', 'stop', 'hide', 'generation', 'unmount'] as const) {
  test('a pending frame URL is released on ' + action, async () => {
    start();
    publish({ generation: 1, image: 'pending' });
    assert.equal(activeURLs.size, 1);
    if (action === 'error') settleFrame('error');
    if (action === 'stop') click(button('stop'));
    if (action === 'hide') setVisible(false);
    if (action === 'generation') selectPrinter(2);
    if (action === 'unmount') await unmount(app);
    assert.equal(activeURLs.size, 0);
    assert.equal(vi.mocked(URL.revokeObjectURL).mock.calls.length, 1);
  });
}

test('replacing a pending frame releases it and ignores its late load callback', () => {
  start();
  publish({ generation: 1, image: 'first' });
  const oldLoad = image().onload!;
  const oldSource = image().getAttribute('src');
  publish({ generation: 1, image: 'second' });
  assert.equal(activeURLs.size, 1);
  assert.notEqual(image().getAttribute('src'), oldSource);
  flushSync(() => oldLoad.call(image(), new Event('load')));
  assert.equal(frames().length, 0);
  assert.equal(activeURLs.size, 1, 'An old callback cannot release the replacement frame');
  settleFrame();
  assert.equal(frames().length, 1);
  assert.equal(activeURLs.size, 0);
});

test('repeated playback and visibility cycles leave no frame URLs outstanding', () => {
  let generation = 1;
  selectPrinter(generation);
  for (let cycle = 0; cycle < 20; cycle++) {
    click(button('play'));
    selectPrinter(++generation);
    publish({ generation, image: 'frame' });
    if (cycle % 2 === 0) settleFrame();
    if (cycle % 3 === 0) {
      setVisible(false);
      setVisible(true);
      selectPrinter(++generation);
    } else click(button('stop'));
    assert.equal(activeURLs.size, 0);
    assert.equal(visible(button('play')), true);
  }
  assert.equal(vi.mocked(URL.createObjectURL).mock.calls.length, 20);
  assert.equal(vi.mocked(URL.revokeObjectURL).mock.calls.length, 20);
});

for (const invalid of ['data:image/jpeg;base64,%%%', 'data:text/html;base64,aGVsbG8=']) {
  test('invalid frame data is acknowledged and recovers: ' + invalid, () => {
    start();
    flushSync(() => receive({ generation: 1, image: invalid }));
    assert.equal(activeURLs.size, 0);
    assert.deepEqual(frames(), [{ type: 'frame', generation: 1 }]);
    assert.equal(visible(image()), false);
    publish({ generation: 1, image: 'recovered' });
    settleFrame();
    assert.equal(visible(image()), true);
  });
}

test('a camera 404 retains the last frame through retries and recovers on the next image', () => {
  start();
  publish({ generation: 1, image: 'last-good-frame', fps: 4 });
  const lastGoodSource = image().getAttribute('src');
  settleFrame();
  publish({ generation: 1, failed: true, camera: 'Printer returned HTTP 404.' });
  assert.equal(visible(image()), true);
  assert.equal(image().getAttribute('src'), lastGoodSource);
  assert.equal(field('camera').dataset.state, 'waiting');
  assert.doesNotMatch(field('camera').textContent!, /404/);
  advance(5000);
  assert.equal(field('camera').dataset.state, 'waiting');
  publish({ generation: 1, failed: true, camera: 'Printer returned HTTP 404.' });
  advance(7000);
  assert.equal(visible(image()), true);
  assert.equal(image().getAttribute('src'), lastGoodSource);
  assert.equal(field('camera').dataset.state, 'error');
  assert.match(field('camera').textContent!, /404/);
  assert.doesNotMatch(field('camera').textContent!, /404\./);
  publish({ generation: 1, image: 'recovered-frame', fps: 4 });
  settleFrame();
  assert.notEqual(image().getAttribute('src'), lastGoodSource);
  assert.equal(field('camera').dataset.state, 'live');
  assert.match(field('camera').textContent!, /4.0 FPS/);
  assert.doesNotMatch(field('camera').textContent!, /404/);
});

test('Stop removes the current frame and late images cannot restart playback', () => {
  start();
  publish({ generation: 1, image: 'frame' });
  settleFrame();
  click(button('stop'));
  assert.equal(lastMessage().playing, false);
  assert.equal(visible(image()), false);
  assert.equal(visible(button('play')), true);
  publish({ generation: 1, image: 'late' });
  assert.equal(image().hasAttribute('src'), false);
  const acknowledged = frames().length;
  settleFrame();
  assert.equal(frames().length, acknowledged);
});

test('changing generation rejects old frames and status without losing playback intent', () => {
  start();
  publish({ generation: 1, stats: metrics, light: true, image: 'old frame' });
  const oldLoad = image().onload!;
  selectPrinter(2, 'Other printer');
  const staleFailure = 'Previous printer failure';
  publish({
    generation: 1,
    stats: metrics,
    light: false,
    image: 'late frame',
    error: staleFailure,
  });
  assert.equal(field('status-delivery').dataset.state, 'waiting');
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(visible(progress()), false);
  assert.equal(image().hasAttribute('src'), false);
  assert.equal(light().disabled, true);
  assert.ok(!field('camera').textContent!.includes(staleFailure));
  flushSync(() => oldLoad.call(image(), new Event('load')));
  assert.equal(frames().length, 0, 'An old load callback cannot acknowledge the new generation');
  publish({ generation: 2, image: 'new frame' });
  assert.equal(
    image().getAttribute('src'),
    vi.mocked(URL.createObjectURL).mock.results.at(-1)!.value,
    'A target announcement preserves playback intent',
  );
  settleFrame();
  assert.deepEqual(lastMessage(), { type: 'frame', generation: 2 });
});

test('a new generation for the same printer waits for its own statistics and light', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics, light: true });
  click(button('play'));
  selectPrinter(2);
  assert.equal(field('status-delivery').dataset.state, 'waiting');
  assert.equal(visible(progress()), false);
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(light().disabled, true);
  publish({ generation: 2, stats: metrics, light: false });
  assert.equal(field('status-delivery').dataset.state, 'fresh');
  assert.equal(visible(progress()), true);
  assert.equal(light().disabled, false);
  assert.equal(light().getAttribute('aria-checked'), 'false');
});

test('visibility stops monitoring once, ignores late loads, and returning requires Play again', () => {
  start();
  publish({ generation: 1, image: 'frame' });
  setVisible(false);
  assert.deepEqual(lastMessage(), { type: 'activity', active: false, generation: 1 });
  const inactiveCount = sent.length;
  settleFrame();
  advance(5000);
  click(light());
  assert.equal(
    sent.length,
    inactiveCount,
    'Hidden pages do not acknowledge frames, send commands, or keep monitoring',
  );
  assert.equal(visible(image()), false);
  setVisible(true);
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: false });
  assert.equal(visible(button('play')), true);
});

test('window focus does not control playback, but a stale image is no longer presented as live', () => {
  start();
  publish({ generation: 1, image: 'frame' });
  settleFrame();
  window.dispatchEvent(new Event('blur'));
  advance(1000);
  assert.equal(lastMessage().playing, true);
  assert.equal(visible(image()), true);
  advance(6000);
  assert.equal(visible(image()), true, 'A stalled feed retains its last frame');
  assert.equal(field('camera').dataset.state, 'error');
  assert.ok(field('camera').textContent?.trim(), 'A stalled feed has an explanation');
});

test('selection changes stop playback, explain unavailable targets, and allow later recovery', () => {
  start();
  publish({ type: 'selection_changed' });
  assert.equal(lastMessage().playing, false);
  assert.equal(button('play').disabled, true);
  for (const supported of [false, true]) {
    const reason = supported
      ? 'A configured address is required'
      : 'This preset cannot supply a camera';
    publish({ error: reason, supported });
    assert.equal(visible(button('play')), false);
    assert.equal(light().disabled, true);
    const warning = [...document.querySelectorAll<HTMLElement>('[role="status"]')].find(
      (node) => visible(node) && node.textContent?.includes(reason),
    );
    assert.ok(warning, 'The host-provided reason is visible');
    const messageCount = sent.length;
    click(button('play'));
    assert.equal(sent.length, messageCount);
    setVisible(false);
    setVisible(true);
    assert.equal(visible(button('play')), false, 'Returning does not bypass target resolution');
  }
  selectPrinter(2);
  assert.equal(button('play').disabled, false);
  assert.equal(visible(button('play')), true);
  click(button('play'));
  assert.equal(lastMessage().playing, true);
  publish({ generation: 2, light: false });
  assert.equal(light().disabled, false);
  publish({ error: 'Temporary connection failure' });
  assert.equal(
    visible(button('stop')),
    true,
    'A transport error does not reclassify the selected preset',
  );
  assert.equal(visible(field('warning')), false);
});

test('unmount ends monitoring and later browser events or host messages cannot restart it', async () => {
  start();
  publish({ generation: 1, image: 'frame' });
  const pendingLoad = image().onload!;
  const pendingImage = image();
  await unmount(app);
  assert.deepEqual(lastMessage(), { type: 'activity', active: false, generation: 1 });
  const messageCount = sent.length;
  advance(5000);
  flushSync(() => {
    window.dispatchEvent(new Event('pageshow'));
    document.dispatchEvent(new Event('visibilitychange'));
    pendingLoad.call(pendingImage, new Event('load'));
  });
  publish({ type: 'target', generation: 2, target: 'After unmount' });
  assert.equal(sent.length, messageCount);
});

test('resolved consent clears its warning before playback starts', () => {
  publish({ error: 'Reset allow_preset_connection to null', supported: true });
  assert.equal(visible(field('warning')), true);
  selectPrinter();
  assert.equal(visible(field('warning')), false);
  assert.equal(field('warning-label').textContent, '');
  assert.equal(visible(field('notice')), false);
  assert.equal(visible(button('play')), true);
});

test('brief stream failures recover without exposing error text', () => {
  start();
  publish({ generation: 1, image: 'good', fps: 4 });
  settleFrame();
  publish({ generation: 1, failed: true, camera: 'HTTP 404.' });
  advance(4000);
  assert.equal(field('camera').dataset.state, 'waiting');
  assert.doesNotMatch(field('camera').textContent!, /404/);
  publish({ generation: 1, image: 'recovered', fps: 3.2 });
  settleFrame();
  assert.equal(field('camera').dataset.state, 'live');
  assert.match(field('camera').textContent!, /3.2 FPS/);
});

test('downloaded stale images do not reset the interruption timer', () => {
  start();
  publish({ generation: 1, image: 'stale', frozen: true });
  settleFrame();
  advance(4000);
  publish({ generation: 1, image: 'still-stale', frozen: true });
  settleFrame();
  advance(2000);
  assert.equal(field('camera').dataset.state, 'error');
  assert.ok(field('camera').textContent?.trim(), 'A stalled feed has an explanation');
  publish({ generation: 1, image: 'fresh', frozen: false, fps: 2 });
  settleFrame();
  assert.equal(field('camera').dataset.state, 'live');
});

test('startup errors wait five seconds and Stop clears the stream status', () => {
  start();
  const connectingGuidance = field('empty').textContent;
  assert.ok(connectingGuidance?.trim());
  advance(5000);
  assert.equal(field('camera').dataset.state, 'waiting');
  advance(1000);
  assert.equal(field('camera').dataset.state, 'error');
  const failure = field('camera').textContent;
  assert.ok(failure?.trim());
  assert.ok(field('empty').textContent?.trim(), 'The retrying state has guidance');
  assert.notEqual(field('empty').textContent, connectingGuidance);
  click(button('stop'));
  assert.equal(visible(field('camera')), false);
  click(button('play'));
  assert.equal(field('camera').dataset.state, 'waiting');
  assert.notEqual(field('camera').textContent, failure);
});

test('starting playback moves a connection failure into the stream pill without a duplicate notice', () => {
  selectPrinter();
  publish({ generation: 1, failed: true, camera: 'Cannot reach printer' });
  advance(6000);
  assert.equal(visible(field('notice')), true);
  click(button('play'));
  assert.equal(visible(field('notice')), false);
  publish({ generation: 1, failed: true, camera: 'Cannot reach printer' });
  advance(6000);
  assert.equal(field('camera').dataset.state, 'error');
  assert.equal(visible(field('notice')), false);
});

test('configuration warnings separate the title from advice and mark config properties as code', () => {
  const title = 'Missing endpoint.';
  const property = 'host';
  const advice = `Configure "${property}" to reach the printer.`;
  publish({ error: `${title} ${advice}`, supported: true });
  assert.equal(field('warning-title').textContent, title);
  assert.equal(field('warning-recommendation').textContent?.trim(), advice.replaceAll('"', ''));
  assert.equal(field('warning-recommendation').querySelector('code')?.textContent, property);
});

test('the header repeats the print-state label and dot beside the address', () => {
  selectPrinter();
  publish({ generation: 1, printer: 'printing' });
  assert.equal(field('printer').textContent, 'Printing');
  assert.equal(field('printer').parentElement, field('target').parentElement);
  for (const [state, phase, label] of [
    ['printing', 'printing', 'Printing'],
    ['printing', 'preparing', 'Preparing'],
    ['printing', 'finishing', 'Finishing'],
    ['paused', 'printing', 'Paused'],
    ['complete', 'printing', 'Complete'],
    ['error', 'printing', 'Error'],
    ['cancelled', 'printing', 'Cancelled'],
    ['standby', 'printing', 'Standby'],
    ['unavailable', 'printing', 'Status unavailable'],
  ] as const) {
    publish({ generation: 1, stats: { ...metrics, state, phase } });
    assert.equal(field('printer').textContent, label);
    assert.equal(field('printer').textContent, field('stats-state').textContent);
    assert.equal(field('printer').dataset.state, field('stats-state').dataset.state);
  }
});

test('the connecting view disappears when the first camera image displays', () => {
  start();
  assert.equal(visible(field('empty')), true);
  assert.equal(field('camera').dataset.state, 'waiting');
  assert.ok(field('empty').textContent?.trim(), 'The initial connection has guidance');
  publish({ generation: 1, image: 'first-frame', fps: 4 });
  settleFrame();
  assert.equal(visible(field('empty')), false);
});

test('toolhead details preserve reported material and expose slicing and layer height on focus or tap', () => {
  selectPrinter();
  const details = {
    layer_height: 0.2,
    material: 'PLA',
    material_vendor: 'Snapmaker',
    material_subtype: 'SnapSpeed',
    material_profile: 'Snapmaker PLA <b>SnapSpeed</b> @U1',
  };
  publish({ generation: 1, stats: { ...metrics, ...details } });
  const tab = element('[data-tool="2"]');
  const material = `${details.material} ${details.material_subtype}`;
  assert.ok(tab.getAttribute('aria-label')!.includes(material));
  assert.ok(field('tool-details-2').textContent!.includes(`${details.layer_height} mm`));
  assert.ok(field('tool-details-2').textContent!.includes(details.material_profile));
  assert.equal(
    field('tool-details-2').querySelector('b'),
    null,
    'Profile text is not parsed as HTML',
  );
  assert.ok(tab.nextElementSibling!.textContent!.includes(material));
  assert.deepEqual(
    Array.from(document.querySelectorAll('.index-tab b'), (node) => node.textContent),
    ['1', '2', '3', '4'],
  );
  for (const id of [0, 1, 3]) {
    assert.equal(document.querySelector('#tool-details-' + id), null);
    assert.equal(element('[data-tool="' + id + '"]').getAttribute('aria-describedby'), null);
  }
  const before = sent.length;
  click(tab);
  assert.equal(tab.getAttribute('aria-expanded'), 'true');
  assert.equal(sent.length, before, 'Inspecting a tab never sends a tool-change command');
  tab.focus();
  assert.equal(document.activeElement, tab);
});

test('four toolheads follow physical status and never display another head temperature', () => {
  selectPrinter();
  const tools = [
    { state: 'parked' as const, material: 'PLA', heater: { temperature: 25, target: 0 } },
    {
      state: 'connected' as const,
      material: 'PETG',
      nozzle_diameter: 0.6,
      heater: { temperature: 180, target: 240 },
    },
    { state: 'unknown' as const },
    { state: 'unavailable' as const },
  ];
  publish({ generation: 1, stats: { ...metrics, tools } });
  assert.equal(document.querySelectorAll('[data-tool]').length, 4);
  assert.equal(element('[data-active="true"]').getAttribute('data-tool'), '1');
  assert.deepEqual(numbers(field('hotend-value')), [240, 180]);
  assert.deepEqual(numbers(element('.parked-temperature')), [25]);
  assert.equal(element('.parked-temperature').getAttribute('aria-label'), 'Toolhead 1 temperature');
  assert.equal(document.querySelectorAll('.parked-temperature').length, 1);
  assert.match(element('[data-tool="3"]').getAttribute('aria-label')!, /unavailable/);
  for (const [target, direction, arrow] of [
    [210, 'heating', '↗'],
    [100, 'cooling', '↘'],
  ] as const) {
    publish({
      generation: 1,
      stats: {
        ...metrics,
        tools: tools.map((tool, id) =>
          id === 0 ? { ...tool, heater: { temperature: 157, target } } : tool,
        ),
      },
    });
    assert.equal(element('.parked-temperature .' + direction).textContent, arrow);
    assert.ok(element('.parked-temperature').classList.contains('changing'));
  }
  for (const temperature of [undefined, NaN, Infinity, -1]) {
    publish({
      generation: 1,
      stats: {
        ...metrics,
        tools: tools.map((tool, id) => (id === 0 ? { ...tool, heater: { temperature } } : tool)),
      },
    });
    assert.equal(document.querySelector('.parked-temperature'), null);
  }
  publish({
    generation: 1,
    stats: { ...metrics, tools: tools.map((tool) => ({ ...tool, state: 'parked' })) },
  });
  assert.equal(document.querySelector('.index-tab[data-active="true"]'), null);
  assert.equal(visible(field('hotend-value')), false);
  publish({
    generation: 1,
    stats: { state: 'unavailable', tools, bed: { temperature: 60, target: 60 } },
  });
  assert.equal(visible(field('hardware-stat')), false);
  assert.equal(visible(field('bed-value')), false);
  assert.equal(visible(element('[data-tool="0"]')), false);
  assert.equal(document.querySelector('.index-tab[data-active="true"]'), null);
});

test('filament paths show flow only during confirmed model printing', async () => {
  selectPrinter();
  for (const [state, phase, flowing] of [
    ['printing', 'preparing', false],
    ['printing', 'printing', true],
    ['paused', 'printing', false],
    ['printing', 'unknown', false],
    ['printing', 'finishing', false],
    ['printing', 'printing', true],
  ] as const) {
    publish({ generation: 1, stats: { ...metrics, state, phase } });
    await tick();
    flushSync();
    assert.equal(Boolean(document.querySelector('.trace-shimmer')), flowing);
    assert.equal(Boolean(document.querySelector('.trace-draw')), flowing);
    assert.equal(
      element('.trace-base[data-active="true"]').getAttribute('stroke'),
      flowing ? metrics.color : '#a0aab4',
    );
  }
});

test('fan readings show reported percent and RPM and clear missing status', () => {
  selectPrinter();
  publish({
    generation: 1,
    stats: { ...metrics, aux_fan: { speed: 0.7, rpm: 3200 }, part_fan: { speed: 0, rpm: 0 } },
  });
  assert.deepEqual(numbers(element('[aria-label="Aux fan"]')), [70, 3200]);
  assert.equal(element('[aria-label="Aux fan"] .fan-tooltip strong').textContent, 'Aux fan');
  assert.deepEqual(numbers(element('[aria-label="Part fan"]')), [0]);
  assert.equal(document.querySelector('[aria-label="Part fan"] .fan-tooltip'), null);
  assert.equal(element('[aria-label="Part fan"]').hasAttribute('tabindex'), false);
  publish({
    generation: 1,
    stats: { ...metrics, aux_fan: { speed: null, rpm: 1234 }, part_fan: { speed: 0.5 } },
  });
  assert.deepEqual(numbers(element('[aria-label="Aux fan"]')), [1234]);
  assert.deepEqual(numbers(element('[aria-label="Part fan"]')), [50]);
  publish({ generation: 1, stats: { ...metrics, aux_fan: { speed: 2, rpm: -1 } } });
  assert.equal(document.querySelector('.fan-speed'), null);
  publish({
    generation: 1,
    stats: { ...metrics, state: 'unavailable', aux_fan: { speed: 1, rpm: 3200 } },
  });
  assert.equal(document.querySelector('.fan-speed'), null);
});

test('fan islands show reported PSU, hotend, and top cover status and clear unavailable data', () => {
  selectPrinter();
  publish({
    generation: 1,
    stats: {
      ...metrics,
      psu_fan: { speed: 0.6 },
      hotend_fan: { speed: 0.75, rpm: 16761 },
      exhaust_fan: { speed: 0.6 },
      circulation_fan: { speed: 0, rpm: 0 },
    },
  });
  assert.equal(document.querySelectorAll('.fan-island').length, 3);
  assert.deepEqual(numbers(element('[aria-label="PSU fan"]')), [60]);
  assert.deepEqual(numbers(element('[aria-label="Hotend fan"]')), [75, 16761]);
  assert.deepEqual(numbers(element('[aria-label="Exhaust fan"]')), [60]);
  assert.deepEqual(numbers(element('[aria-label="Circulation fan"]')), [0]);
  assert.equal(document.querySelector('[aria-label="Circulation fan"] .fan-tooltip'), null);
  publish({
    generation: 1,
    stats: {
      ...metrics,
      psu_fan: { speed: 0.6 },
      hotend_fan: { speed: 0.75, rpm: 16761 },
      exhaust_fan: { speed: null, rpm: null },
      circulation_fan: { speed: null, rpm: null },
    },
  });
  assert.equal(document.querySelector('.cover-island'), null);
  assert.equal(document.querySelectorAll('.fan-island').length, 2);
  assert.deepEqual(numbers(element('[aria-label="PSU fan"]')), [60]);
  assert.deepEqual(numbers(element('[aria-label="Hotend fan"]')), [75, 16761]);
  publish({
    generation: 1,
    stats: {
      ...metrics,
      psu_fan: { speed: 0 },
      exhaust_fan: { speed: null },
      circulation_fan: { rpm: null },
    },
  });
  assert.equal(document.querySelectorAll('.fan-island').length, 1);
  assert.deepEqual(numbers(element('[aria-label="PSU fan"]')), [0]);
  publish({ generation: 1, stats: { ...metrics, state: 'unavailable', psu_fan: { speed: 0.6 } } });
  assert.equal(document.querySelector('.fan-islands'), null);
  publish({ generation: 1, stats: { ...metrics, psu_fan: { rpm: 0 } } });
  assert.equal(document.querySelector('.fan-islands'), null);
  publish({ generation: 1, stats: { ...metrics, psu_fan: { speed: 2 } } });
  assert.equal(document.querySelector('.fan-islands'), null);
});

test('all fan readouts are hidden after a job ends and while idle', () => {
  selectPrinter();
  const fans = {
    aux_fan: { speed: 0.7, rpm: 3200 },
    part_fan: { speed: 1, rpm: 8200 },
    psu_fan: { speed: 0.6 },
    hotend_fan: { speed: 0.75, rpm: 16761 },
    exhaust_fan: { speed: 0.6 },
    circulation_fan: { speed: 0.5, rpm: 1500 },
  };
  for (const state of [
    'printing',
    'paused',
    'complete',
    'standby',
    'cancelled',
    'error',
    'unavailable',
    'printing',
  ]) {
    publish({ generation: 1, stats: { ...metrics, ...fans, state } });
    const active = state === 'printing' || state === 'paused';
    assert.equal(document.querySelectorAll('.fan-speed').length, active ? 6 : 0, state);
    assert.equal(document.querySelectorAll('.fan-island').length, active ? 3 : 0, state);
    assert.equal(Boolean(document.querySelector('.bed-fans')), active, state);
  }
});
