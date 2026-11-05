<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte';
  import type { PrintStats, Toolhead } from './types';
  let { stats, hotend, bed }: { stats: PrintStats | null; hotend: Snippet; bed: Snippet } =
    $props();
  let root: HTMLElement;
  let dismissed = $state<number | null>(null);
  let open = $state<number | null>(null);
  const tools = $derived(
    Array.from({ length: 4 }, (_, id): Toolhead =>
      stats?.state === 'unavailable'
        ? { state: 'unknown' }
        : (stats?.tools?.[id] ?? { state: 'unknown' }),
    ),
  );
  const showTabs = $derived(stats?.state !== 'standby');
  const connected = $derived(
    tools.filter((tool) => tool.state === 'connected').length === 1
      ? tools.findIndex((tool) => tool.state === 'connected')
      : -1,
  );
  const active = $derived(
    connected >= 0 ? connected : tools.findIndex((tool) => tool.state === 'changing'),
  );
  const color = (tool: Toolhead) =>
    typeof tool.color === 'string' && /^#[0-9a-f]{6}$/i.test(tool.color) ? tool.color : null;
  const material = (tool: Toolhead) =>
    [...new Set([tool.material, tool.material_subtype].filter(Boolean))].join(' ') ||
    'Not reported';
  const status = (tool: Toolhead) =>
    stats?.state === 'unavailable'
      ? 'Status unavailable'
      : {
          connected: 'Connected',
          changing: 'Changing toolhead',
          parked: 'Parked',
          unavailable: 'Toolhead unavailable',
          unknown: 'Attachment unknown',
        }[tool.state];
  const slicing = (tool: Toolhead) =>
    [
      tool.material_profile
        ? 'Sliced with: ' + tool.material_profile
        : 'Slicing profile not reported',
      stats?.layer_height
        ? 'Layer height: ' + stats.layer_height + ' mm'
        : 'Layer height not reported',
    ].join(' · ');
  let tooltipBottom = $state(0);
  let paths = $state<{ id: number; d: string; color: string; active: boolean }[]>([]);
  function measure() {
    if (!root) return;
    if (!showTabs) {
      paths = [];
      return;
    }
    const origin = root.getBoundingClientRect();
    const row = root.querySelector('.chosen');
    if (row)
      tooltipBottom =
        row.getBoundingClientRect().bottom -
        root.closest('#stats')!.getBoundingClientRect().top +
        8;
    const temp = root.querySelector('.current-temperature')!.getBoundingClientRect();
    paths = tools.map((tool, id) => {
      const tab = root.querySelector(`[data-tool="${id}"]`)!.getBoundingClientRect();
      const swatch = root.querySelector(`[data-swatch="${id}"]`)!.getBoundingClientRect();
      const y = tab.y + tab.height / 2 - origin.y;
      let d = `M ${swatch.x - origin.x - 4} ${y} H ${tab.right - origin.x + 4}`;
      if (id === active && root.querySelector('.current-temperature dd:not([hidden])')) {
        const nozzle = root.querySelector('.nozzle-size')!.getBoundingClientRect();
        const start = nozzle.x - origin.x - 4;
        const end = temp.right - origin.x + 4;
        const elbow = (start + end) / 2;
        d += ` M ${start} ${y} H ${elbow} V ${temp.y + temp.height / 2 - origin.y} H ${end}`;
      }
      return { id, d, color: color(tool) ?? '#a0aab4', active: id === connected };
    });
  }
  $effect(() => {
    void [stats, active];
    void tick().then(measure);
  });
  $effect(() => {
    void active;
    void stats?.state;
    open = null;
  });
  onMount(() => {
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    observer.observe(root.closest('#stats')!);
    observer.observe(root.querySelector('.current-temperature')!);
    return () => observer.disconnect();
  });
</script>

<div
  id="hardware-stat"
  hidden={!stats || stats.state === 'unavailable'}
  bind:this={root}
  role="group"
  aria-label="Toolheads and temperatures"
>
  <dl class="sheet">
    <div class="current-temperature">{@render hotend()}</div>
    <div class="bed-temperature">{@render bed()}</div>
  </dl>
  <div class="index-stack" hidden={!showTabs}>
    {#each tools as tool, id}
      {#snippet tabContents()}
        {#if id === active}<span class="nozzle-size"
            >{tool.nozzle_diameter ?? '—'}<small>mm</small></span
          >{/if}
        <b>{id + 1}</b><span
          class="head-state"
          style:color={id === connected ? color(tool) : undefined}
          aria-hidden="true"
          >{tool.state === 'changing'
            ? '↻'
            : id === connected
              ? '●'
              : tool.state === 'unavailable'
                ? '×'
                : tool.state === 'parked'
                  ? '○'
                  : '?'}</span
        >
      {/snippet}
      <div
        class="index-row"
        class:chosen={id === active}
        data-open={open === id}
        data-dismissed={dismissed === id}
      >
        {#if id === active}
          <button
            class="index-tab"
            data-tool={id}
            data-active={id === active}
            aria-label={'Toolhead ' + (id + 1) + ' · ' + status(tool) + ' · ' + material(tool)}
            aria-describedby={'tool-details-' + id}
            aria-expanded={open === id}
            onkeydown={(event) => {
              if (event.key === 'Escape') {
                open = null;
                dismissed = id;
              }
            }}
            onfocus={() => (dismissed = null)}
            onpointerenter={() => (dismissed = null)}
            onclick={() => {
              dismissed = open === id ? id : null;
              open = open === id ? null : id;
            }}
          >
            {@render tabContents()}
          </button>
        {:else}
          <span
            class="index-tab"
            data-tool={id}
            data-active="false"
            role="img"
            aria-label={'Toolhead ' + (id + 1) + ' · ' + status(tool) + ' · ' + material(tool)}
            >{@render tabContents()}</span
          >
        {/if}
        <div class="filament">
          <span
            class="swatch"
            class:unknown={!color(tool)}
            data-swatch={id}
            style:background-color={color(tool)}
            aria-label={color(tool) ? 'Filament color ' + color(tool) : 'Color not reported'}
          ></span>
          <span class="material-copy">
            <strong>{material(tool)}</strong>
            <small
              >{tool.state === 'unavailable'
                ? 'Toolhead unavailable'
                : id === active
                  ? 'Slice: ' + (tool.material_profile ?? 'Not reported')
                  : tool.filament_present === false
                    ? 'No filament detected'
                    : (tool.material_vendor ?? status(tool))}</small
            >
          </span>
        </div>
        {#if id === active}<span
            class="tool-tooltip"
            style:bottom={tooltipBottom + 'px'}
            id={'tool-details-' + id}
            role="tooltip"
          >
            Toolhead {id + 1} · {status(tool)} · {material(tool)}{tool.material_vendor
              ? ' · ' + tool.material_vendor
              : ''}<br />
            {tool.filament_present === false ? 'No filament detected · ' : ''}{color(tool)
              ? ''
              : 'Color not reported · '}
            Nozzle: {tool.nozzle_diameter ?? '—'} mm<br />{slicing(tool)}
          </span>{/if}
      </div>
    {/each}
  </div>
  {#if showTabs}<svg class="filament-traces" aria-hidden="true">
      {#each paths as path}
        <path class="trace-base" data-active={path.id === active} d={path.d} stroke={path.color} />
        {#if path.active}
          {#key connected}
            <path class="trace-draw" d={path.d} stroke={path.color} pathLength="1" />
            <path class="trace-shimmer" d={path.d} stroke={path.color} pathLength="1" />
          {/key}
        {/if}
      {/each}
    </svg>{/if}
</div>

<style>
  #hardware-stat {
    grid-column: 3;
    position: relative;
    display: grid;
    grid-template-columns: 160px minmax(0, 1fr);
    height: 88px;
    min-width: 0;
    border-left: 1px solid #ffffff28;
    padding-left: 18px;
    grid-template-rows: 88px;
  }
  .sheet {
    margin: 0;
    padding: 0 50px 4px 8px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }
  .current-temperature {
    display: flex;
    align-items: center;
    height: 22px;
    width: max-content;
    white-space: nowrap;
  }
  .bed-temperature {
    position: relative;
    width: max-content;
    white-space: nowrap;
  }
  .index-stack {
    display: grid;
    grid-template-rows: repeat(4, 22px);
    min-width: 0;
  }
  .index-row {
    position: relative;
    display: grid;
    grid-template-columns: 34px minmax(0, 1fr);
    grid-template-rows: 22px;
    gap: 18px;
    align-items: center;
    min-width: 0;
    min-height: 0;
    height: 22px;
  }
  .index-tab {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 3px;
    width: 34px;
    height: 22px;
    margin-left: -1px;
    padding: 0 4px;
    border: 1px solid transparent;
    border-radius: 0 5px 5px 0;
    background: transparent;
    color: #a7b5c2;
    font: inherit;
    cursor: default;
    box-shadow: none;
  }
  .index-tab::before {
    content: '';
    position: absolute;
    inset: -1px;
    border: 1px solid #465360;
    border-left-color: transparent;
    border-radius: inherit;
    pointer-events: none;
  }
  .index-tab[data-active='true'] {
    width: 56px;
    height: 28px;
    margin-left: -19px;
    z-index: 3;
    color: #fff;
  }
  .index-tab[data-active='true']::before {
    inset: -1px;
    border: 1px solid #c4d3e0;
    border-radius: 5px;
    mask-image:
      linear-gradient(to right, transparent 5px, #000 5px),
      linear-gradient(
        to bottom,
        #000 6px,
        transparent 6px,
        transparent calc(100% - 6px),
        #000 calc(100% - 6px)
      );
  }
  .index-tab:hover {
    background: transparent;
  }
  .index-tab:focus-visible {
    outline: 2px solid #7abaff;
    outline-offset: 2px;
  }
  .index-row:has(+ .chosen) .index-tab::before {
    clip-path: inset(0 0 3px 0);
  }
  .chosen + .index-row .index-tab::before {
    clip-path: inset(3px 0 0 0);
  }
  .index-tab b {
    font-size: 11px;
    font-weight: 500;
    line-height: 1;
  }
  .index-tab[data-active='true'] b {
    font-size: 22px;
  }
  .head-state {
    font-size: 7px;
  }
  .nozzle-size {
    display: inline-flex;
    flex-direction: column;
    align-items: center;
    gap: 1px;
    line-height: 1;
    font-size: 10px;
    white-space: nowrap;
  }
  .nozzle-size small {
    font-size: 8px;
    color: #c0cbd5;
  }
  .filament {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }
  .swatch {
    flex: 0 0 10px;
    width: 10px;
    height: 10px;
    border: 1px solid #ffffff70;
    border-radius: 2px;
    box-shadow:
      0 2px 4px #0005,
      inset 0 1px 1px #ffffff50;
  }
  .swatch.unknown {
    background: repeating-linear-gradient(135deg, transparent 0 3px, #a0aab4 3px 4px);
  }
  .material-copy {
    display: grid;
    min-width: 0;
  }
  .material-copy strong {
    font-size: 10px;
    font-weight: 500;
    line-height: 11px;
    color: #e0e9f0;
  }
  .material-copy small {
    font-size: 8px;
    line-height: 9px;
    color: #a9b8c5;
  }
  .material-copy > * {
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .index-tab:not([data-active='true']) + .filament,
  .trace-base[data-active='false'] {
    visibility: hidden;
  }
  :global(#stats:is(:hover, :focus-within)) .filament,
  :global(#stats:is(:hover, :focus-within)) .trace-base {
    visibility: visible;
  }
  .tool-tooltip {
    position: absolute;
    right: 0;
    bottom: calc(100% + 8px);
    z-index: 6;
    width: max-content;
    max-width: min(300px, calc(100vw - 68px));
    padding: 8px 10px;
    border: 1px solid #ffffff38;
    border-radius: 8px;
    background: #111820fa;
    color: #edf3f9;
    font-size: 11px;
    line-height: 1.5;
    visibility: hidden;
    transition: visibility 0s linear 250ms;
    cursor: text;
    user-select: text;
    overflow-wrap: anywhere;
  }
  .index-row:focus-within:not([data-dismissed='true']) .tool-tooltip,
  .index-row[data-open='true'] .tool-tooltip {
    visibility: visible;
    transition-delay: 0s;
  }
  .index-row[data-dismissed='true'] .tool-tooltip {
    transition: none;
  }
  @media (hover: hover) {
    .index-row:hover:not([data-dismissed='true']) .tool-tooltip {
      visibility: visible;
      transition-delay: 0s;
    }
  }
  .filament-traces {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    z-index: 4;
    overflow: visible;
  }
  path {
    fill: none;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
    filter: drop-shadow(0 1px 1px #0005) drop-shadow(0 0 2px #0003);
  }
  .trace-base {
    opacity: 0.45;
  }
  .trace-draw {
    stroke-width: 2.5;
    stroke-dasharray: 1;
    animation: trace-draw 0.9s ease-in-out both;
  }
  .trace-shimmer {
    stroke-width: 3;
    stroke-dasharray: 0.055 0.945;
    filter: brightness(1.6) drop-shadow(0 0 2px #ffffff40);
    animation: trace-flow 2.5s linear 0.9s infinite;
  }
  @keyframes trace-draw {
    from {
      stroke-dashoffset: 1;
    }
    to {
      stroke-dashoffset: 0;
    }
  }
  @keyframes trace-flow {
    from {
      stroke-dashoffset: 1;
      opacity: 0;
    }
    20%,
    85% {
      opacity: 0.9;
    }
    to {
      stroke-dashoffset: 0;
      opacity: 0;
    }
  }
  @media (max-width: 850px) {
    #hardware-stat {
      grid-column: 1 / -1;
      border-left: 0;
      border-top: 1px solid #ffffff28;
      padding: 12px 0 0;
      height: 101px;
    }
  }
  @media (hover: none) {
    .index-tab:not([data-active='true']) + .filament,
    .trace-base[data-active='false'] {
      visibility: visible;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .trace-draw,
    .trace-shimmer {
      animation: none;
    }
    .trace-shimmer {
      display: none;
    }
  }
</style>
