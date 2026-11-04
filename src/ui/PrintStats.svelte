<script lang="ts">
  import type { Heater, PrintStats } from './types';
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
  function temperature(value: Heater = {}, showTarget = true) {
    const degrees = (item: unknown) => (number(item) ? Math.round(item) : '—');
    const target =
      showTarget &&
      number(value.target) &&
      value.target > 0 &&
      (!number(value.temperature) || Math.abs(value.temperature - value.target) > 2);
    return degrees(value.temperature) + (target ? ' / ' + degrees(value.target) : '') + ' °C';
  }
  const states: Record<string, string> = {
    printing: 'Printing', // Active print, with progress and estimates.
    preparing: 'Preparing', // Before the first model layer, including early purge.
    paused: 'Paused', // Interrupted print, without a finish estimate.
    complete: 'Complete', // Finished print, with cooling temperatures.
    error: 'Error', // Failed job.
    cancelled: 'Cancelled', // Job stopped by the user.
    standby: 'Standby', // No job running.
    unavailable: 'Status unavailable', // Status missing or older than ten seconds.
  };
  const preparing = $derived(stats?.state === 'printing' && stats.phase === 'preparing');
  const state = $derived(preparing ? 'preparing' : (stats?.state ?? 'unavailable'));
  const printing = $derived(state === 'printing');
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
  const standby = $derived(state === 'standby');
  const missing = $derived(state === 'unavailable');
  const terminal = $derived(['complete', 'cancelled', 'error'].includes(state));
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
          : printing && stats?.phase === 'finishing'
            ? 'Finishing; ETA unknown'
            : printing && stats?.phase !== 'printing'
              ? 'Print active; phase unknown'
              : '',
  );
  const progress = $derived(number(stats?.progress) ? Math.min(100, stats.progress * 100) : null);
  const color = $derived(
    typeof stats?.color === 'string' && /^#[0-9a-f]{6}$/i.test(stats.color)
      ? stats.color.toUpperCase()
      : null,
  );
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

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users can reveal the statistics surface.) -->
<section id="stats" aria-labelledby="stats-title" tabindex="0" hidden={!stats} data-state={state}>
  <h2 id="stats-title">Print statistics</h2>
  <div class="stats-gauge">
    <span id="stats-state">{states[state] ?? 'Unknown'}</span>
    <span id="stats-message" hidden={!message}>{message}</span>
    <strong id="progress-value" hidden={standby || preparing || missing}
      >{progress === null ? '—' : Math.floor(progress) + '%'}</strong
    >
    <div id="layers-stat" class="layers" hidden={standby || preparing || missing}>
      Layers<span id="layers-value"
        >{layer(stats?.current_layer)} / {layer(stats?.total_layer)}</span
      >
    </div>
  </div>
  <div id="journey-stat" class="stats-journey" hidden={standby || missing}>
    <div id="remaining-stat" hidden={!showEstimate}>
      <strong id="remaining-value"
        >{number(stats?.remaining) ? '≈ ' + duration(stats.remaining, true) : '—'}</strong
      ><span>left</span>
    </div>
    <div id="plan-stat" hidden={!showPlan}>
      <strong id="plan-value"
        >{number(stats?.planned_total) ? '≈ ' + duration(stats.planned_total, true) : '—'}{preparing
          ? ' print plan + preparation'
          : ''}</strong
      >
      <span hidden={preparing}>Slicer plan at normal speed</span>
    </div>
    <div id="finish-unknown" hidden={!(printing || preparing) || showFinish}>Finish unknown</div>
    <div
      id="progress-bar"
      data-indeterminate={preparing || undefined}
      role="progressbar"
      aria-label={preparing ? 'Preparing print' : 'Print progress'}
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow={preparing || progress === null ? undefined : Math.floor(progress)}
      aria-valuetext={preparing
        ? 'Preparation in progress'
        : progress === null
          ? 'Progress unavailable'
          : Math.floor(progress) + '% complete'}
    >
      <span id="progress-fill" style:width={(preparing ? 100 : (progress ?? 0)) + '%'}></span>
    </div>
    <dl class="stats-times">
      <div>
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
  <div id="hardware-stat" class="stats-details" hidden={missing}>
    <div id="material-stat" class="material" hidden={standby || state === 'error'}>
      <span id="tool-stat" hidden={terminal || standby}
        ><span id="tool-value" title="Toolhead"
          >{Number.isInteger(stats?.tool) && number(stats?.tool) && stats.tool < 4
            ? 'T' + stats.tool
            : '—'}</span
        ></span
      ><span id="material-value">{stats?.material || 'Unknown material'}</span><span
        id="material-swatch"
        aria-hidden="true"
        hidden={!color}
        style:background-color={color || 'transparent'}
      ></span>
    </div>
    <dl class="temperatures">
      <div>
        <dt id="bed-label">Bed</dt>
        <dd id="bed-value">{temperature(stats?.bed, !terminal && !standby)}</dd>
      </div>
      <div>
        <dt id="hotend-label">Hotend</dt>
        <dd id="hotend-value">{temperature(stats?.hotend, !terminal && !standby)}</dd>
      </div>
    </dl>
  </div>
</section>

<style>
  #stats {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: 100px minmax(0, 1fr) 185px;
    gap: 18px;
    width: 666px;
    max-width: 100%;
    padding: 16px;
    border: 1px solid transparent;
    border-radius: 14px;
    background: transparent;
    pointer-events: auto;
    font-variant-numeric: tabular-nums;
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
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 8px;
  }
  #stats-title {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  #stats-state {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: #d5dee7;
    font-size: 11px;
    font-weight: 500;
  }
  #stats-state::before {
    content: '';
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #96a4b3;
  }
  #stats[data-state='printing'] #stats-state::before {
    background: #7abaff;
  }
  #stats[data-state='preparing'] #stats-state::before {
    background: #a0cbff;
  }
  #stats[data-state='complete'] #stats-state::before {
    background: #72dec2;
  }
  #stats[data-state='paused'] #stats-state::before {
    background: #f1c675;
  }
  #stats[data-state='error'] #stats-state::before {
    background: #ff9999;
  }
  #progress-value {
    font-size: 42px;
    font-weight: 550;
    letter-spacing: -2px;
    line-height: 1;
  }
  .layers {
    color: #c0cbd5;
    font-size: 10px;
  }
  #layers-value {
    margin-left: 4px;
  }
  .stats-journey {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 10px;
    border-left: 1px solid #ffffff28;
    padding-left: 18px;
  }
  #remaining-stat {
    display: flex;
    align-items: baseline;
    gap: 7px;
  }
  #remaining-value {
    font-size: 22px;
    font-weight: 550;
    letter-spacing: -0.6px;
  }
  #remaining-stat span {
    color: #c0cbd5;
    font-size: 10px;
  }
  #plan-value {
    font-size: 16px;
    font-weight: 550;
    line-height: 1.4;
  }
  #plan-stat span {
    display: block;
    margin-top: 3px;
  }
  #plan-stat span,
  #finish-unknown {
    color: #c0cbd5;
    font-size: 10px;
    line-height: 1.4;
  }
  #progress-bar {
    height: 4px;
    overflow: hidden;
    border-radius: 99px;
    background: #ffffff32;
  }
  #progress-fill {
    display: block;
    width: 0;
    height: 100%;
    border-radius: inherit;
    background: #7abaff;
  }
  #progress-bar[data-indeterminate] #progress-fill {
    position: relative;
    background: #a0cbff38;
  }
  #progress-bar[data-indeterminate] #progress-fill::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, transparent, #a0cbff, transparent);
    transform: translateX(-100%);
    animation: preparation-shimmer 2.4s ease-in-out infinite;
  }
  @keyframes preparation-shimmer {
    to {
      transform: translateX(100%);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    #progress-bar[data-indeterminate] #progress-fill::after {
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
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin: 0;
  }
  #stats dt {
    margin-bottom: 4px;
    color: #c0cbd5;
    font-size: 10px;
    line-height: 1.2;
  }
  #stats dd {
    margin: 0;
    color: #edf3f9;
    font-size: 12px;
    font-weight: 500;
    line-height: 1.3;
  }
  #stats #finish-value {
    overflow-wrap: anywhere;
  }
  .stats-details {
    display: grid;
    align-content: center;
    gap: 10px;
    border-left: 1px solid #ffffff28;
    padding-left: 18px;
  }
  .material {
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
    line-height: 1;
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
    display: grid;
    gap: 7px;
    margin: 0;
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
  #stats:is(
    [data-state='complete'],
    [data-state='paused'],
    [data-state='cancelled'],
    [data-state='error']
  ) {
    width: 540px;
    grid-template-columns: 100px minmax(0, 1fr) 180px;
  }
  #stats[data-state='standby'] {
    width: 320px;
    grid-template-columns: 1fr 1fr;
  }
  #stats[data-state='preparing'] {
    width: 720px;
    grid-template-columns: 110px minmax(0, 1fr) 185px;
  }
  #stats[data-state='unavailable'] {
    width: 360px;
    grid-template-columns: 1fr;
  }
  #stats-message {
    font-size: 16px;
    font-weight: 500;
    line-height: 1.4;
  }
  #stats[data-state='preparing'] #stats-message {
    font-size: 22px;
  }
  @media (max-width: 650px) {
    #stats,
    #stats:is(
      [data-state='complete'],
      [data-state='paused'],
      [data-state='cancelled'],
      [data-state='error'],
      [data-state='preparing']
    ) {
      width: 480px;
      grid-template-columns: 90px minmax(0, 1fr);
      gap: 12px;
    }
    #progress-value {
      font-size: 36px;
    }
    .stats-journey {
      padding-left: 12px;
    }
    .stats-details {
      grid-column: 1 / -1;
      border-left: 0;
      border-top: 1px solid #ffffff28;
      padding: 10px 0 0;
      grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
      align-items: center;
    }
    .temperatures {
      grid-template-columns: 1fr 1fr;
      gap: 12px;
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
    }
  }
  @media (max-width: 420px) {
    .stats-details {
      grid-template-columns: 1fr;
    }
  }
</style>
