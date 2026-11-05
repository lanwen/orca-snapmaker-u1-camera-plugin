<script lang="ts">
  import type { PrintStats } from './types';

  let {
    stats,
    fallback = '',
    id,
  }: { stats: PrintStats | null; fallback?: string; id: string } = $props();
  const labels: Record<string, string> = {
    printing: 'Printing', // Active model printing.
    preparing: 'Preparing', // Layer zero, including heating and purge.
    paused: 'Paused', // Interrupted print.
    complete: 'Complete', // Finished print.
    error: 'Error', // Failed print.
    cancelled: 'Cancelled', // Print stopped by the user.
    standby: 'Standby', // No print running.
    unavailable: 'Status unavailable', // Missing or expired printer status.
  };
  const state = $derived(
    stats?.state === 'printing' && stats.phase === 'preparing'
      ? 'preparing'
      : (stats?.state ?? fallback.toLowerCase()),
  );
  const label = $derived(
    stats?.state === 'printing' && stats.phase === 'finishing'
      ? 'Finishing'
      : (labels[state] ?? fallback.charAt(0).toUpperCase() + fallback.slice(1)),
  );
</script>

<span {id} data-state={state} hidden={!label}>{label}</span>

<style>
  span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: #d5dee7;
    font-size: var(--print-state-font-size, 11px);
    font-weight: 500;
    white-space: nowrap;
  }
  span::before {
    content: '';
    flex: 0 0 5px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #96a4b3;
  }
  [data-state='printing']::before {
    background: #7abaff;
  }
  [data-state='preparing']::before {
    background: #a0cbff;
  }
  [data-state='complete']::before {
    background: #72dec2;
  }
  [data-state='paused']::before {
    background: #f1c675;
  }
  [data-state='error']::before {
    background: #ff9999;
  }
</style>
