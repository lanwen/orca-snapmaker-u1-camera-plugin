<script lang="ts">
  import FanIcon from './FanIcon.svelte';
  import type { Fan } from './types';
  let { label, value }: { label: string; value?: Fan } = $props();
  const number = (value: unknown): value is number =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const speed = $derived(
    number(value?.speed) && value.speed <= 1 ? Math.round(value.speed * 100) : null,
  );
  const rpm = $derived(
    number(value?.rpm) && Math.round(value.rpm) > 0 ? Math.round(value.rpm) : null,
  );
  let tooltipTop = $state(0);
  let tooltipLeft = $state(0);
  function positionTooltip(event: Event) {
    if (rpm === null) return;
    const fan = event.currentTarget as HTMLElement;
    const panel = fan.closest('#stats')!;
    const bounds = fan.getBoundingClientRect();
    const panelBounds = panel.getBoundingClientRect();
    const islands = panel.querySelector('.fan-islands')?.getBoundingClientRect();
    const tooltip = fan.querySelector('.fan-tooltip')!.getBoundingClientRect();
    const beside = islands && islands.left - tooltip.width - 8 >= 8;
    const left = beside ? islands.left - tooltip.width - 8 : panelBounds.left + 16;
    const top = beside
      ? islands.top + (islands.height - tooltip.height) / 2
      : (islands ?? panelBounds).top - tooltip.height - 8;
    tooltipTop = top - bounds.top;
    tooltipLeft = left - bounds.left;
  }
</script>

{#if speed !== null || rpm !== null}
  <!-- svelte-ignore a11y_no_noninteractive_tabindex (Keyboard users can reveal this fan's RPM.) -->
  <div
    class="fan-speed"
    role="group"
    onpointerenter={positionTooltip}
    onfocus={positionTooltip}
    aria-label={label + ' fan'}
    aria-describedby={rpm !== null ? 'fan-rpm-' + label : undefined}
    tabindex={rpm !== null ? 0 : undefined}
  >
    <dt>{label}</dt>
    <dd>
      {#if speed !== null}{speed} <small class="fan-unit">%</small>{/if}
      {#if rpm !== null}<span
          class="fan-tooltip"
          style:top={tooltipTop + 'px'}
          style:left={tooltipLeft + 'px'}
          id={'fan-rpm-' + label}
          role="tooltip"
          ><FanIcon size={12} /><span class="fan-details"
            ><strong>{label} fan</strong><span>{rpm} rpm</span></span
          ></span
        >{/if}
    </dd>
  </div>
{/if}

<style>
  .fan-speed {
    position: relative;
    text-align: left;
  }
  dt {
    margin: 0;
    color: #c0cbd5;
    font-size: 8px;
    line-height: 10px;
    text-transform: uppercase;
  }
  dd {
    margin: 0;
    color: #edf3f9;
    font-size: 10px;
    line-height: 12px;
    font-weight: 500;
  }
  .fan-unit {
    color: #a7b5c2;
    font-size: 8px;
  }
  .fan-tooltip {
    display: flex;
    align-items: center;
    gap: 6px;
    position: absolute;
    left: 0;
    z-index: 6;
    width: max-content;
    padding: 7px 6px;
    border: 1px solid #ffffff38;
    border-radius: 10px;
    background: #111820fa;
    color: #a7b5c2;
    font-size: 11px;
    line-height: 1.5;
    visibility: hidden;
  }
  .fan-details {
    display: grid;
    font-size: 10px;
    line-height: 12px;
  }
  .fan-details strong {
    color: #edf3f9;
    font-size: 8px;
    line-height: 10px;
    font-weight: 500;
    text-transform: uppercase;
  }
  .fan-speed:hover .fan-tooltip,
  :global(#stats:not(:has(.fan-speed:hover))) .fan-speed:focus .fan-tooltip {
    visibility: visible;
  }
  .fan-speed:focus-visible {
    outline: 1px solid #7abaff;
    outline-offset: 2px;
  }
</style>
