<script lang="ts">
  import type { Heater, PrintStats } from './types';
  import PrintState from './PrintState.svelte';
  let { stats, receivedAt }: { stats: PrintStats | null; receivedAt: number } = $props();
  const number = (value: unknown): value is number =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const layer = (value: unknown) => (number(value) ? Math.floor(value).toLocaleString() : '—');
  function duration(value: unknown, estimate = false) {
    if (!number(value)) return '—';
    const minutes = estimate ? Math.ceil(value / 60) : Math.floor(value / 60);
    return value === 0
      ? '0m'
      : minutes < 1
        ? '<1m'
        : (minutes >= 60 ? Math.floor(minutes / 60) + 'h ' : '') + (minutes % 60) + 'm';
  }
  function elapsedClock(value: unknown) {
    if (!number(value)) return '—';
    const seconds = Math.floor(value);
    const minutes = Math.floor(seconds / 60);
    const pad = (part: number) => String(part).padStart(2, '0');
    return (
      (minutes >= 60 ? Math.floor(minutes / 60) + ':' : '') +
      pad(minutes % 60) +
      ':' +
      pad(seconds % 60)
    );
  }
  function coolingTracker() {
    let samples: { at: number; temperature: number }[] = [];
    return (value: Heater = {}, at: number) => {
      if (value.target !== 0 || !number(value.temperature)) {
        samples = [];
        return false;
      }
      const previous = samples.at(-1);
      // Missing status breaks the observation window; silence is not stability.
      if (previous && (at - previous.at > 10000 || at < previous.at)) samples = [];
      samples.push({ at, temperature: value.temperature });
      while (samples.length > 1 && samples[1].at <= at - 60000) samples.shift();
      const temperatures = samples.map((sample) => sample.temperature);
      return (
        at - samples[0].at >= 60000 && Math.max(...temperatures) - Math.min(...temperatures) <= 1
      );
    };
  }
  const trackBedCooling = coolingTracker();
  const trackHotendCooling = coolingTracker();
  let settled = $state.raw({ bed: false, hotend: false });
  $effect(() => {
    settled = {
      bed: trackBedCooling(stats?.bed, receivedAt),
      hotend: trackHotendCooling(stats?.hotend, receivedAt),
    };
  });
  function temperature(value: Heater = {}, settled = false) {
    const current = number(value.temperature) ? Math.round(value.temperature) : null;
    const target = number(value.target) ? Math.round(value.target) : null;
    const direction =
      !(value.target === 0 && settled) &&
      number(value.temperature) &&
      number(value.target) &&
      Math.abs(value.temperature - value.target) > 2
        ? value.target > value.temperature
          ? 'heating'
          : 'cooling'
        : null;
    return {
      current,
      target,
      direction,
      description:
        value.target === 0 ? (direction ? 'Heater off · cooling' : 'Heater off') : undefined,
      visible: current !== null || (target !== null && target > 0),
    };
  }
  const preparing = $derived(stats?.state === 'printing' && stats.phase === 'preparing');
  const printState = $derived(preparing ? 'preparing' : (stats?.state ?? 'unavailable'));
  const printing = $derived(printState === 'printing');
  const activePrint = $derived(stats?.state === 'printing' || stats?.state === 'paused');
  const showEstimate = $derived(
    printing && stats?.phase === 'printing' && number(stats?.remaining),
  );
  const showPlan = $derived(
    (printing || preparing) && !showEstimate && number(stats?.planned_total),
  );
  const preparationFinish = $derived(
    preparing && number(stats?.planned_total) && stats.planned_total > 0,
  );
  const showFinish = $derived(showEstimate || preparationFinish);
  const standby = $derived(printState === 'standby');
  const missing = $derived(printState === 'unavailable');
  const terminal = $derived(['complete', 'cancelled', 'error'].includes(printState));
  const heating = $derived(
    [stats?.bed, stats?.hotend].some(
      (value) =>
        value &&
        number(value.temperature) &&
        number(value.target) &&
        value.target - value.temperature > 2,
    ),
  );
  const message = $derived(
    missing
      ? 'Waiting for status'
      : standby
        ? 'No print running'
        : preparing
          ? heating
            ? 'Heating up'
            : 'Preparing to print'
          : '',
  );
  const progress = $derived(number(stats?.progress) ? Math.min(100, stats.progress * 100) : null);
  const indeterminate = $derived(preparing || (activePrint && !progress));
  const color = $derived(
    typeof stats?.color === 'string' && /^#[0-9a-f]{6}$/i.test(stats.color)
      ? stats.color.toUpperCase()
      : null,
  );
  const showTool = $derived(
    !terminal && !standby && Number.isInteger(stats?.tool) && number(stats?.tool) && stats.tool < 4,
  );
  const showMaterial = $derived(
    !standby && printState !== 'error' && Boolean(showTool || stats?.material || color),
  );
  const bedTemperature = $derived(temperature(stats?.bed, settled.bed));
  const hotendTemperature = $derived(temperature(stats?.hotend, settled.hotend));
  const finish = $derived(
    preparationFinish && number(stats?.planned_total)
      ? new Date(receivedAt + stats.planned_total * 1000)
      : showEstimate && number(stats?.remaining)
        ? new Date(receivedAt + stats.remaining * 1000)
        : null,
  );
  const finishTime = $derived(
    finish && Number.isFinite(finish.getTime())
      ? (finish.toDateString() === new Date(receivedAt).toDateString()
          ? ''
          : finish.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ', ') +
          finish.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
      : '—',
  );
</script>

{#snippet heaterTemperature(value: ReturnType<typeof temperature>)}
  {#if value.current !== null}
    <span
      title={value.description}
      aria-label={value.description ? value.current + ' °C · ' + value.description : undefined}
    >
      {#if value.direction}<span class="temperature-arrow {value.direction}" aria-hidden="true"
          >{value.direction === 'heating' ? '↑' : '↓'}</span
        >{/if}{value.current} °C<span class="temperature-target"
        >{#if value.direction === 'heating'}<sup
            class="heating"
            aria-label={'Target ' + value.target + ' °C'}>{value.target} °C</sup
          >{:else if value.direction === 'cooling' && value.target !== 0}<sub
            class="cooling"
            aria-label={'Target ' + value.target + ' °C'}>{value.target} °C</sub
          >{/if}</span
      >
    </span>
  {:else if value.visible}Target {value.target} °C{/if}
{/snippet}

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users can reveal the statistics surface.) -->
<section
  id="stats"
  aria-labelledby="stats-title"
  tabindex="0"
  hidden={!stats}
  data-state={printState}
>
  <h2 id="stats-title">Print statistics</h2>
  <div class="stats-gauge">
    <PrintState id="stats-state" {stats} />
    <div class="stats-primary">
      <span id="stats-message" hidden={!message}>{message}</span>
      <strong id="progress-value" hidden={standby || preparing || missing || progress === null}
        >{progress === null ? '—' : Math.floor(progress) + '%'}</strong
      >
    </div>
  </div>
  <div id="journey-stat" class="stats-journey" hidden={standby || missing}>
    <div class="journey-header">
      <div id="remaining-stat" hidden={!showEstimate}>
        <strong id="remaining-value">
          {#if number(stats?.remaining)}
            <small class="approximation">≈</small> {duration(stats.remaining, true)}
          {:else}—{/if}
        </strong><span>left</span>
      </div>
      <div id="plan-stat" hidden={!showPlan}>
        <strong id="plan-value">
          {#if number(stats?.planned_total)}
            <small class="approximation">≈</small> {duration(stats.planned_total, true)}
          {:else}—{/if}
        </strong>
        <span id="plan-caption" hidden={!preparing}>+ preparation</span>
      </div>
      <div
        id="layers-stat"
        aria-label="Layers"
        hidden={standby ||
          preparing ||
          missing ||
          !number(stats?.current_layer) ||
          !number(stats?.total_layer)}
      >
        <span id="layers-value">{layer(stats?.current_layer)} / {layer(stats?.total_layer)}</span>
      </div>
    </div>
    <div
      id="progress-bar"
      hidden={!activePrint && progress === null}
      data-indeterminate={indeterminate || undefined}
      data-shimmer={activePrint || undefined}
      data-heating={(preparing && heating) || undefined}
      role="progressbar"
      aria-label={preparing
        ? 'Preparing print'
        : indeterminate
          ? 'Print activity'
          : 'Print progress'}
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={indeterminate || progress === null ? undefined : Math.floor(progress)}
      aria-valuetext={preparing
        ? 'Preparation in progress'
        : stats?.state === 'paused'
          ? 'Print paused'
          : progress === null
            ? 'Print in progress'
            : Math.floor(progress) + '% complete'}
    >
      <span id="progress-fill" style:width={(indeterminate ? 100 : (progress ?? 0)) + '%'}></span>
    </div>
    <dl class="stats-times" hidden={!number(stats?.elapsed) && !showFinish}>
      <div hidden={!number(stats?.elapsed)}>
        <dt>Elapsed</dt>
        <dd id="elapsed-value" title="Printer-reported elapsed time, to the second">
          {elapsedClock(stats?.elapsed)}
        </dd>
      </div>
      <div id="finish-stat" hidden={!showFinish}>
        <dt>Est. finish</dt>
        <dd
          id="finish-value"
          title={preparationFinish
            ? 'Slicer plan from this update; preparation adds time.'
            : undefined}
        >
          {preparationFinish ? 'after ' : ''}{finishTime}
        </dd>
      </div>
    </dl>
  </div>
  <div
    id="hardware-stat"
    class="stats-details"
    hidden={missing || (!showMaterial && !bedTemperature.visible && !hotendTemperature.visible)}
  >
    <div id="material-stat" class="material" hidden={!showMaterial}>
      <span id="tool-stat" hidden={!showTool}
        ><span id="tool-value" title="Toolhead"
          >{Number.isInteger(stats?.tool) && number(stats?.tool) && stats.tool < 4
            ? 'T' + stats.tool
            : '—'}</span
        ></span
      ><span id="material-value" hidden={!stats?.material}>{stats?.material ?? ''}</span><span
        id="material-swatch"
        aria-hidden="true"
        hidden={!color}
        style:background-color={color || 'transparent'}
      ></span>
    </div>
    <dl class="temperatures">
      <div hidden={!bedTemperature.visible}>
        <dt id="bed-label">Bed</dt>
        <dd id="bed-value">{@render heaterTemperature(bedTemperature)}</dd>
      </div>
      <div hidden={!hotendTemperature.visible}>
        <dt id="hotend-label">Hotend</dt>
        <dd id="hotend-value">{@render heaterTemperature(hotendTemperature)}</dd>
      </div>
    </dl>
  </div>
</section>

<style>
  #stats {
    grid-column: 1;
    display: grid;
    grid-template-columns: 146px minmax(0, 1fr) 185px;
    gap: 18px;
    width: 720px;
    max-width: 100%;
    padding: 16px;
    border: 1px solid transparent;
    border-radius: 14px;
    background: transparent;
    pointer-events: auto;
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
    text-shadow: 0 1px 4px #0009;
  }
  #stats:hover,
  #stats:focus-visible {
    border-color: #ffffff26;
    background: #111820e8;
    box-shadow: 0 8px 32px #0003;
    backdrop-filter: blur(18px);
  }
  #stats:focus-visible {
    outline: 2px solid #7abaff;
    outline-offset: 3px;
  }
  .stats-gauge {
    display: grid;
    grid-template-rows: 16px 66px;
    align-content: start;
    gap: 6px;
  }
  .stats-primary {
    display: grid;
    grid-row: 2;
    align-items: center;
    min-width: 0;
  }
  .stats-primary > * {
    grid-area: 1 / 1;
  }
  #stats:is([data-state='standby'], [data-state='unavailable']) .stats-gauge {
    grid-template-rows: 16px auto;
  }
  #stats-title {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  #progress-value {
    font-size: 56px;
    line-height: 1;
    font-weight: 550;
    letter-spacing: -2px;
  }
  #layers-stat {
    grid-area: 1 / 2;
    justify-self: end;
    align-self: end;
    white-space: nowrap;
    color: #c0cbd5;
    font-size: 10px;
  }
  .stats-journey {
    display: grid;
    grid-template-rows: 32px 4px 32px;
    align-content: start;
    gap: 8px;
    border-left: 1px solid #ffffff28;
    padding-left: 18px;
  }
  .journey-header {
    grid-area: 1 / 1;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: baseline;
    gap: 4px 8px;
  }
  #remaining-stat,
  #plan-stat {
    grid-area: 1 / 1;
    align-self: end;
  }
  #plan-stat {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0 6px;
  }
  #remaining-stat {
    display: flex;
    align-items: baseline;
    gap: 7px;
  }
  #remaining-value,
  #plan-value {
    display: inline-flex;
    align-items: center;
    gap: 0.2em;
  }
  #remaining-value {
    font-size: 22px;
    font-weight: 550;
    letter-spacing: -0.6px;
  }
  .approximation {
    color: #96a4b3;
    font-size: 0.6em;
    font-weight: 400;
    letter-spacing: 0;
  }
  #remaining-stat span {
    color: #c0cbd5;
    font-size: 10px;
  }
  #plan-value {
    font-size: 22px;
    font-weight: 550;
  }
  #plan-caption {
    color: #c0cbd5;
    font-size: 11px;
    font-weight: 400;
  }
  #progress-bar {
    grid-area: 2 / 1;
    height: 4px;
    overflow: hidden;
    border-radius: 99px;
    background: #ffffff32;
  }
  #progress-fill {
    display: block;
    position: relative;
    overflow: hidden;
    width: 0;
    height: 100%;
    border-radius: inherit;
    background: #7abaff;
  }
  #progress-bar[data-indeterminate] #progress-fill {
    background: #a0cbff38;
  }
  #progress-bar[data-heating] {
    --progress-highlight: #ffbb77;
  }
  #progress-bar[data-heating] #progress-fill {
    background: #ffaf6538;
  }
  #progress-bar[data-shimmer] #progress-fill::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(
      90deg,
      transparent,
      var(--progress-highlight, #a0cbff),
      transparent
    );
    transform: translateX(-100%);
    animation: progress-shimmer 2.4s ease-in-out infinite;
  }
  #stats[data-state='printing'] #progress-fill::after {
    --progress-highlight: #e0f2ff;
    opacity: 1;
    animation-duration: 3.2s;
  }
  #stats[data-state='paused'] #progress-fill::after {
    --progress-highlight: #fff4d9;
    opacity: 1;
    animation-duration: 3.2s;
  }
  @keyframes progress-shimmer {
    to {
      transform: translateX(100%);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    #progress-bar[data-shimmer] #progress-fill::after {
      animation: none;
      transform: none;
    }
  }
  #stats[data-state='complete'] #progress-fill {
    background: #72dec2;
  }
  #stats[data-state='cancelled'] #progress-fill {
    background: #96a4b3;
  }
  #stats[data-state='paused'] #progress-fill {
    background: #f1c675;
  }
  #stats[data-state='error'] #progress-fill {
    background: #ff9999;
  }
  .stats-times {
    grid-area: 3 / 1;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin: 0;
  }
  .stats-times > div:first-child {
    grid-area: 1 / 1;
  }
  .stats-times > div:last-child {
    grid-area: 1 / 2;
    text-align: right;
  }
  #stats dt {
    margin-bottom: 4px;
    color: #c0cbd5;
    font-size: 10px;
  }
  #stats dd {
    margin: 0;
    color: #edf3f9;
    font-size: 12px;
    font-weight: 500;
  }
  #stats #finish-value {
    overflow-wrap: anywhere;
  }
  .stats-details {
    display: grid;
    grid-template-rows: 30px 40px;
    align-content: center;
    gap: 10px;
    border-left: 1px solid #ffffff28;
    padding-left: 18px;
  }
  .material {
    grid-area: 1 / 1;
    align-self: center;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }
  #tool-value {
    display: inline-block;
    padding: 3px 6px;
    border: 1px solid #ffffff60;
    border-bottom-width: 2px;
    border-radius: 5px;
    background: #ffffff0d;
    font-size: 11px;
    font-weight: 600;
  }
  #material-value {
    overflow-wrap: anywhere;
  }
  #material-swatch {
    flex: 0 0 10px;
    width: 10px;
    height: 10px;
    border: 1px solid #ffffff80;
    border-radius: 3px;
    background: transparent;
  }
  .temperatures {
    grid-area: 2 / 1;
    display: grid;
    grid-template-rows: 16px 16px;
    gap: 7px;
    margin: 0;
  }
  .temperatures > div:first-child {
    grid-area: 1 / 1;
  }
  .temperatures > div:last-child {
    grid-area: 2 / 1;
  }
  .temperatures > div {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
  }
  #stats .temperatures dt {
    margin: 0;
  }
  #stats .temperatures dd {
    white-space: nowrap;
  }
  .heating {
    color: #ffbb77;
  }
  .cooling {
    color: #7abaff;
  }
  .temperature-arrow {
    margin-right: 3px;
  }
  .temperature-target {
    display: inline-block;
    width: 5ch;
    margin-left: 5px;
  }
  .temperatures sup,
  .temperatures sub {
    font-size: 0.75em;
    line-height: 0;
  }
  #stats[data-state='standby'] {
    width: 400px;
    grid-template-columns: max-content minmax(0, 1fr);
  }
  #stats[data-state='standby'] #stats-message {
    font-size: 18px;
    white-space: nowrap;
  }
  #stats[data-state='standby'] .stats-details {
    grid-template-rows: auto;
  }
  #stats[data-state='standby'] .temperatures {
    grid-area: auto;
  }
  #stats[data-state='unavailable'] {
    width: 360px;
    grid-template-columns: 1fr;
  }
  #stats-message {
    font-size: 16px;
    font-weight: 500;
  }
  #stats[data-state='preparing'] #stats-message {
    font-size: 18px;
  }
  @media (max-width: 650px) {
    #stats {
      width: 480px;
      grid-template-columns: 120px minmax(0, 1fr);
      gap: 12px;
    }
    #progress-value {
      font-size: 46px;
    }
    .stats-journey {
      grid-template-rows: 48px 4px 48px;
      padding-left: 12px;
    }
    .stats-details {
      grid-column: 1 / -1;
      border-left: 0;
      border-top: 1px solid #ffffff28;
      padding: 10px 0 0;
      grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
      grid-template-rows: 32px;
      align-items: center;
    }
    .temperatures {
      grid-area: 1 / 2;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: 32px;
      gap: 12px;
    }
    .temperatures > div:last-child {
      grid-area: 1 / 2;
    }
    .temperatures > div {
      display: block;
    }
    #stats .temperatures dt {
      margin-bottom: 4px;
    }
    #stats[data-state='standby'] .stats-details {
      grid-column: auto;
      border-top: 0;
      border-left: 1px solid #ffffff28;
      padding: 0 0 0 12px;
      grid-template-columns: 1fr;
    }
    #stats[data-state='standby'] .temperatures {
      grid-template-columns: 1fr;
      grid-template-rows: 32px 32px;
    }
    #stats[data-state='standby'] .temperatures > div:last-child {
      grid-area: 2 / 1;
    }
  }
  @media (max-width: 420px) {
    .stats-details {
      grid-template-columns: 1fr;
      grid-template-rows: 30px 32px;
    }
    .temperatures {
      grid-area: 2 / 1;
    }
  }
</style>
