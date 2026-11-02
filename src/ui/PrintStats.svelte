<script lang="ts">
  import type { Heater, PrintStats } from './types';
  let { stats, receivedAt }: { stats: PrintStats | null; receivedAt: number } = $props();
  const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const layer = (value: unknown) => number(value) ? Math.floor(value).toLocaleString() : '—';
  function duration(value: unknown) {
    if (!number(value)) return '—';
    const minutes = Math.floor(value / 60);
    return value === 0 ? '0m' : minutes < 1 ? '<1m' : (minutes >= 60 ? Math.floor(minutes / 60) + 'h ' : '') + minutes % 60 + 'm';
  }
  function temperature(value: Heater = {}, showTarget = true) {
    const degrees = (item: unknown) => number(item) ? Math.round(item) : '—';
    const target = showTarget && number(value.target) && value.target > 0 &&
      (!number(value.temperature) || Math.abs(value.temperature - value.target) > 2);
    return degrees(value.temperature) + (target ? ' / ' + degrees(value.target) : '') + ' °C';
  }
  const states: Record<string, string> = {
    printing: 'Printing', // Active print, with progress and estimates.
    preparing: 'Preparing', // Job started before extrusion or the first layer.
    paused: 'Paused', // Interrupted print, without a finish estimate.
    complete: 'Complete', // Finished print, with cooling temperatures.
    error: 'Error', // Failed job.
    cancelled: 'Cancelled', // Job stopped by the user.
    standby: 'Standby', // No job running.
    unavailable: 'Status unavailable', // Status missing or older than ten seconds.
  };
  // No extrusion time or first layer yet: preparation, even if the file has started.
  const preparing = $derived(stats?.state === 'printing' && stats.printing_duration === 0 &&
    (!number(stats.current_layer) || stats.current_layer === 0));
  const state = $derived(preparing ? 'preparing' : stats?.state ?? 'unavailable');
  const printing = $derived(state === 'printing');
  const standby = $derived(state === 'standby');
  const missing = $derived(state === 'unavailable');
  const terminal = $derived(['complete', 'cancelled', 'error'].includes(state));
  const heating = $derived([stats?.bed, stats?.hotend].some(value => value && number(value.temperature) &&
    number(value.target) && value.target - value.temperature > 2));
  const message = $derived(missing ? 'Waiting for status' : standby ? 'No print running' :
    preparing ? (heating ? 'Heating up' : 'Preparing to print') : '');
  const progress = $derived(number(stats?.progress) ? Math.min(100, stats.progress * 100) : null);
  const color = $derived(typeof stats?.color === 'string' && /^#[0-9a-f]{6}$/i.test(stats.color) ? stats.color.toUpperCase() : null);
  const finish = $derived(stats?.state === 'printing' && number(stats.remaining) ? new Date(receivedAt + stats.remaining * 1000) : null);
  const finishTime = $derived(finish && Number.isFinite(finish.getTime()) ?
    (finish.toDateString() === new Date(receivedAt).toDateString() ? '' : finish.toLocaleDateString(undefined, {month:'short', day:'numeric'}) + ', ') +
    finish.toLocaleTimeString(undefined, {hour:'2-digit', minute:'2-digit'}) : '—');
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users can reveal the statistics surface.) -->
<section id="stats" aria-labelledby="stats-title" tabindex="0" hidden={!stats} data-state={state}>
<h2 id="stats-title">Print statistics</h2>
<div class="stats-gauge">
<span id="stats-state">{states[state] ?? 'Unknown'}</span>
<span id="stats-message" hidden={!missing && !standby && !preparing}>{message}</span>
<strong id="progress-value" hidden={standby || preparing || missing}>{progress === null ? '—' : Math.floor(progress) + '%'}</strong>
<div id="layers-stat" class="layers" hidden={standby || preparing || missing}>Layers<span id="layers-value">{layer(stats?.current_layer)} / {layer(stats?.total_layer)}</span></div>
</div>
<div id="journey-stat" class="stats-journey" hidden={standby || preparing || missing}>
<div id="remaining-stat" hidden={!printing}><strong id="remaining-value">{number(stats?.remaining) ? '≈ ' + duration(stats.remaining) : '—'}</strong><span>left</span></div>
<div id="progress-bar" role="progressbar" aria-label="Print progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress === null ? undefined : Math.floor(progress)} aria-valuetext={progress === null ? 'Progress unavailable' : Math.floor(progress) + '% complete'}><span id="progress-fill" style:width={(progress ?? 0) + '%'}></span></div>
<dl class="stats-times">
<div><dt>Elapsed</dt><dd id="elapsed-value">{duration(stats?.elapsed)}</dd></div>
<div id="finish-stat" hidden={!printing}><dt>Est. finish</dt><dd id="finish-value">{finishTime}</dd></div>
</dl>
</div>
<div id="hardware-stat" class="stats-details" hidden={missing}>
<div id="material-stat" class="material" hidden={standby || state === 'error'}><span id="tool-stat" hidden={terminal || standby}><span id="tool-value" title="Toolhead">{Number.isInteger(stats?.tool) && number(stats?.tool) && stats.tool < 4 ? 'T' + stats.tool : '—'}</span></span><span id="material-value">{stats?.material || 'Unknown material'}</span><span id="material-swatch" aria-hidden="true" hidden={!color} style:background-color={color || 'transparent'}></span></div>
<dl class="temperatures">
<div><dt id="bed-label">Bed</dt><dd id="bed-value">{temperature(stats?.bed, !terminal && !standby)}</dd></div>
<div><dt id="hotend-label">Hotend</dt><dd id="hotend-value">{temperature(stats?.hotend, !terminal && !standby)}</dd></div>
</dl>
</div>
</section>
