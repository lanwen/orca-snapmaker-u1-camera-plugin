import assert from 'node:assert/strict';
import { afterEach, beforeEach, test, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import App from '../src/ui/App.svelte';
import type { HostMessage, OrcaBridge } from '../src/ui/types';

let app: ReturnType<typeof mount>;
let receive: (data: HostMessage) => void;
let sent: Parameters<OrcaBridge['postMessage']>[0][];

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
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
const publish = (data: HostMessage) => flushSync(() => receive(data));
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
  assert.deepEqual(lastMessage(), { type: 'activity', active: true, playing: true });
  assert.equal(visible(button('play')), false);
  assert.equal(visible(button('stop')), true);
});

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
    field('material-value').textContent,
    metrics.material,
    'Reported material reaches the user',
  );
  assert.deepEqual(numbers(field('tool-value')), [metrics.tool]);
  assert.deepEqual(numbers(field('bed-value')), [70]);
  assert.deepEqual(numbers(field('hotend-value')), [235, 240]);
  assert.equal(visible(field('material-swatch')), true);
  const swatch = field('material-swatch').style.backgroundColor;
  publish({ generation: 1, stats: { ...metrics, color: '#ffffff' } });
  assert.notEqual(
    field('material-swatch').style.backgroundColor,
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
  assert.equal(visible(progress()), true, 'Pausing retains completed progress');
  assert.equal(visible(field('tool-value')), true);
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('remaining-value')), true);
  assert.equal(visible(field('finish-value')), true);
});

for (const state of ['complete', 'cancelled', 'error']) {
  test(`${state} jobs retain measurements but hide running estimates, tool, and heater targets`, () => {
    selectPrinter();
    publish({
      generation: 1,
      stats: { ...metrics, state, progress: state === 'complete' ? 1 : metrics.progress },
    });
    assert.equal(visible(field('remaining-value')), false);
    assert.equal(visible(field('finish-value')), false);
    assert.equal(visible(field('tool-value')), false);
    assert.deepEqual(numbers(field('hotend-value')), [235]);
    assert.equal(visible(progress()), true);
    if (state === 'complete') assert.equal(Number(progress().getAttribute('aria-valuenow')), 100);
  });
}

test('heater targets appear only while an active heater is away from its target', () => {
  selectPrinter();
  for (const [temperature, target, displayed] of [
    [230, 240, [230, 240]],
    [238, 240, [238]],
    [90, 0, [90]],
  ] as const) {
    publish({ generation: 1, stats: { ...metrics, hotend: { temperature, target } } });
    assert.deepEqual(numbers(field('hotend-value')), displayed);
  }
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
  assert.equal(progress().getAttribute('aria-valuetext'), 'Preparation in progress');
  assert.equal(visible(field('remaining-value')), false);
  assert.equal(visible(field('plan-value')), true);
  assert.match(field('plan-value').textContent!, /35m print plan \+ preparation/);
  assert.equal(visible(field('elapsed-value')), true);
  assert.equal(visible(field('finish-value')), true);
  assert.match(field('finish-value').textContent!, /^after /);
  assert.equal(visible(field('finish-unknown')), false);
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
  test(`${phase} without an eligible estimate shows an original plan and unknown finish`, () => {
    selectPrinter();
    publish({ generation: 1, stats: { ...metrics, phase, remaining: null } });
    assert.equal(visible(field('remaining-value')), false);
    assert.equal(visible(field('plan-value')), true);
    assert.match(field('plan-stat').textContent!, /Slicer plan at normal speed/);
    assert.equal(visible(field('finish-value')), false);
    assert.equal(field('finish-unknown').textContent?.trim(), 'Finish unknown');
    if (phase === 'unknown') assert.match(field('stats-message').textContent!, /phase unknown/);
    if (phase === 'finishing')
      assert.match(field('stats-message').textContent!, /Finishing; ETA unknown/);
  });
}

test('idle and missing status never present old job details as current', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics });
  publish({ generation: 1, stats: { state: 'standby', color: 'url(untrusted)' } });
  assert.equal(visible(field('material-value')), false);
  assert.equal(visible(field('layers-value')), false);
  assert.equal(visible(progress()), false);
  assert.equal(visible(field('material-swatch')), false);
  assert.equal(
    progress().hasAttribute('aria-valuenow'),
    false,
    'Missing progress is not announced as zero',
  );
  publish({ generation: 1, stats: null });
  assert.equal(visible(field('hotend-value')), false);
  assert.equal(visible(field('elapsed-value')), false);
  assert.equal(visible(field('stats-message')), true);
  assert.ok(field('stats-message').textContent?.trim(), 'Missing status has an explanation');
  publish({ generation: 1, stats: metrics });
  assert.equal(visible(field('hotend-value')), true);
  assert.equal(visible(field('elapsed-value')), true);
  advance(11000);
  assert.equal(visible(field('hotend-value')), false, 'Status expires even without playback');
  assert.equal(visible(field('elapsed-value')), false);
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
  for (const id of ['layers-value', 'tool-value', 'hotend-value', 'remaining-value']) {
    assert.deepEqual(numbers(field(id)), [], `${id} does not invent a measurement`);
    assert.doesNotMatch(field(id).textContent!, /NaN|undefined|null/);
  }
  assert.equal(visible(field('material-swatch')), false);
  publish({ generation: 1, stats: { ...metrics, progress: 1.5 } });
  assert.equal(
    Number(progress().getAttribute('aria-valuenow')),
    100,
    'Progress cannot exceed completion',
  );
});

test('light toggles are host-confirmed and work without starting playback', () => {
  selectPrinter();
  publish({ generation: 1, light: false });
  click(light());
  assert.deepEqual(lastMessage(), { type: 'light', enabled: true });
  assert.equal(light().getAttribute('aria-checked'), 'false', 'A request is not confirmation');
  publish({ generation: 1, light: true });
  assert.equal(light().getAttribute('aria-checked'), 'true');
  click(light());
  assert.deepEqual(lastMessage(), { type: 'light', enabled: false });
  const failure = 'The printer rejected the light command';
  publish({ generation: 1, failed: true, camera: failure });
  assert.equal(field('camera').textContent, failure, 'Host failures remain visible while stopped');
  assert.equal(
    sent.some((message) => message.type === 'activity' && message.playing),
    false,
  );
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
  assert.deepEqual(lastMessage(), { type: 'light', enabled: true });
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
  publish({
    generation: 1,
    stats: metrics,
    light: false,
    image: 'late frame',
    error: 'Stale failure',
  });
  assert.equal(visible(field('stats')), false);
  assert.equal(image().hasAttribute('src'), false);
  assert.equal(light().disabled, true);
  assert.notEqual(field('camera').textContent, 'Stale failure');
  flushSync(() => oldLoad.call(image(), new Event('load')));
  assert.equal(frames().length, 0, 'An old load callback cannot acknowledge the new generation');
  publish({ generation: 2, image: 'new frame' });
  assert.equal(
    image().getAttribute('src'),
    'new frame',
    'A target announcement preserves playback intent',
  );
  settleFrame();
  assert.deepEqual(lastMessage(), { type: 'frame', generation: 2 });
});

test('a playback generation announcement for the same printer preserves its statistics and light', () => {
  selectPrinter();
  publish({ generation: 1, stats: metrics, light: true });
  click(button('play'));
  selectPrinter(2);
  assert.equal(visible(progress()), true);
  assert.equal(light().disabled, false);
  assert.equal(light().getAttribute('aria-checked'), 'true');
});

test('visibility stops monitoring once, ignores late loads, and returning requires Play again', () => {
  start();
  publish({ generation: 1, image: 'frame' });
  setVisible(false);
  assert.deepEqual(lastMessage(), { type: 'activity', active: false });
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
  assert.equal(visible(image()), false);
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
  assert.deepEqual(lastMessage(), { type: 'activity', active: false });
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
