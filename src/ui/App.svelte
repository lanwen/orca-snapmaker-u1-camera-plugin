<script lang="ts">
  import { onMount } from 'svelte';
  import { Camera } from './camera.svelte';
  import PrintStats from './PrintStats.svelte';
  import PrintState from './PrintState.svelte';
  import LightControl from './LightControl.svelte';

  let image: HTMLImageElement;
  let camera = $state<Camera>();
  const timerStart = $derived(
    camera?.statusDelivery === 'retrying'
      ? camera.statusInterruptedAt
      : (camera?.pauseObservedAt ?? null),
  );
  const stateAge = $derived(
    timerStart === null ? null : Math.max(0, Math.floor(((camera?.now ?? 0) - timerStart) / 1000)),
  );
  const timerMeaning = $derived(
    camera?.statusDelivery === 'retrying'
      ? 'Time since printer status became unavailable'
      : 'Time since the printer pause was observed',
  );
  const deliveryAnnouncement = $derived(
    camera?.statusDelivery === 'retrying'
      ? 'Printer status unavailable. Waiting for a new reading.'
      : camera?.pauseObservedAt != null
        ? 'Printer pause observed. Timer shows time since it was observed.'
        : camera?.statusDelivery === 'fresh'
          ? 'Printer status current.'
          : 'Waiting for printer status.',
  );
  function age(seconds: number) {
    return seconds < 60
      ? seconds + 's'
      : seconds < 3600
        ? Math.floor(seconds / 60) + 'm'
        : Math.floor(seconds / 3600) + 'h';
  }
  const warning = $derived.by(() => {
    const message = camera?.warning ?? '';
    const boundary = message.indexOf('. ');
    return {
      title: boundary < 0 ? message : message.slice(0, boundary + 1),
      recommendation: boundary < 0 ? '' : message.slice(boundary + 2),
    };
  });
  onMount(() => {
    camera = new Camera(window.orca, image);
    return camera.start();
  });
</script>

<main class="viewer" aria-label="Snapmaker U1 camera">
  <img bind:this={image} id="image" alt="Snapmaker U1 camera" hidden={!camera?.imageVisible} />
  <header>
    <div>
      <div class="printer-title">
        <h1>Snapmaker U1</h1>
        <span
          id="status-delivery"
          data-state={camera?.statusDelivery}
          class:sr-only={camera?.statusDelivery !== 'waiting'}
          hidden={!camera?.statusDelivery}
        >
          <span id="status-delivery-label" class="sr-only" role="status" aria-atomic="true"
            >{deliveryAnnouncement}</span
          >
          <span
            id="status-delivery-value"
            aria-hidden="true"
            hidden={camera?.statusDelivery !== 'waiting'}>Waiting</span
          >
        </span>
      </div>
      <div class="printer-meta">
        <span id="target" title={camera?.target}>{camera?.target ?? 'Selected printer'}</span>
        <PrintState
          id="printer"
          stats={camera?.stats ?? null}
          fallback={camera?.printer ?? 'Connecting'}
        />
        <span
          id="status-age"
          aria-live="off"
          title={timerMeaning}
          aria-label={stateAge === null ? undefined : timerMeaning + ': ' + age(stateAge)}
          hidden={stateAge === null}>{stateAge === null ? '' : '…' + age(stateAge)}</span
        >
      </div>
    </div>
    <div class="light-area" data-error={Boolean(camera?.lightError) || undefined}>
      <LightControl
        value={camera?.light ?? null}
        disabled={camera?.lightDisabled ?? true}
        onchange={(enabled) => camera?.setLight(enabled)}
      />
      <div id="light-error" role="status" hidden={!camera?.lightError}>{camera?.lightError}</div>
    </div>
  </header>
  <button
    id="play"
    type="button"
    aria-label="Play camera"
    disabled={!camera?.supported}
    hidden={camera?.playing || camera?.targetUnavailable}
    onclick={() => camera?.play()}
    ><span class="play-circle"
      ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3v18l16-9z" /></svg></span
    ><span id="play-label" class="play-label">Play camera</span></button
  >
  <div id="warning" role="status" hidden={!camera?.targetUnavailable}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 21h20zM12 9v5m0 3v.01" /></svg>
    <div id="warning-label">
      {#if camera?.warning}
        <strong id="warning-title">{warning.title}</strong>
        <span id="warning-recommendation" hidden={!warning.recommendation}>
          {#each warning.recommendation.split(/"([a-z][a-z0-9_]*)"/g) as part, index}
            {#if index % 2}<code>{part}</code>{:else}{part}{/if}
          {/each}
        </span>
      {/if}
    </div>
  </div>
  <div
    id="empty"
    hidden={!camera?.playing || camera?.imageVisible}
    data-retrying={camera?.streamState === 'error' || undefined}
  >
    <div class="connection-pending">
      <svg viewBox="0 0 56 40" fill="none" stroke-linecap="round" aria-hidden="true">
        <path
          class="viewfinder"
          d="M15 4H8a4 4 0 0 0-4 4v6M41 4h7a4 4 0 0 1 4 4v6M52 26v6a4 4 0 0 1-4 4h-7M15 36H8a4 4 0 0 1-4-4v-6"
        />
        <circle class="connection-lens" cx="28" cy="20" r="8" />
        <g class="connection-sweep">
          <circle cx="28" cy="20" r="8" stroke-dasharray="12 39" />
        </g>
      </svg>
      <span role="status">
        {camera?.streamState === 'error' ? 'Retrying connection…' : 'Connecting to camera…'}
      </span>
    </div>
  </div>
  <footer>
    <PrintStats
      stats={camera?.stats ?? null}
      receivedAt={camera?.statsAt ?? 0}
      statusError={camera?.statusError ?? ''}
    />
    <div id="notice" role="status" hidden={!camera?.notice}>{camera?.notice}</div>
    <div class="stream-controls" hidden={!camera?.playing}>
      <div
        id="camera"
        role="status"
        data-state={camera?.streamState}
        title={camera?.imageVisible && camera?.issueAt !== null
          ? 'Last image retained'
          : 'Camera stream'}
      >
        <span
          ><span class="sr-only"
            >{camera?.streamState === 'live'
              ? 'Live: '
              : camera?.streamState === 'error'
                ? 'Stream error: '
                : 'Waiting: '}</span
          >{camera?.status ?? ''}</span
        >
        <span class="stream-dot" aria-hidden="true"></span>
      </div>
      <button
        id="stop"
        type="button"
        aria-label="Stop camera"
        onclick={() => {
          camera?.stop();
          camera?.tick();
        }}
        ><svg viewBox="0 0 12 12" aria-hidden="true"><rect width="12" height="12" rx="2" /></svg
        >Stop</button
      >
    </div>
  </footer>
</main>

<style>
  .viewer {
    position: fixed;
    inset: var(--viewer-top, 0px) 0 0;
    isolation: isolate;
    background: #0b0e12;
  }
  #image {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
  header {
    position: absolute;
    inset: 0 0 auto;
    padding: 26px 30px 50px;
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 24px;
    background: linear-gradient(#0009, transparent);
    z-index: 3;
    pointer-events: none;
  }
  h1 {
    font-size: 19px;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.3px;
  }
  .printer-title {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 5px;
  }
  #status-delivery {
    flex: 0 0 auto;
    color: #a7b5c2;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  header > div:first-child {
    min-width: 0;
  }
  #target {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    color: #ffffffa0;
  }
  .printer-meta {
    --print-state-font-size: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  #target {
    margin-right: 6px;
  }
  .printer-meta :global(#printer),
  #status-age {
    flex: 0 0 auto;
  }
  #status-age {
    color: #a7b5c2;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  #play,
  #warning {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 18px;
    background: radial-gradient(ellipse at center, #26313c80, transparent 70%);
    z-index: 2;
  }
  #warning {
    color: #f3c568;
    padding: 30px;
    text-align: center;
    pointer-events: none;
  }
  #warning-label {
    display: grid;
    gap: 10px;
    max-width: 480px;
    overflow-wrap: anywhere;
    line-height: 1.5;
  }
  #warning-title {
    font-size: 18px;
    font-weight: 550;
  }
  #warning-recommendation {
    color: #d5dee7;
    font-size: 14px;
  }
  #warning-recommendation code {
    padding: 1px 5px;
    border: 1px solid #ffffff38;
    border-radius: 4px;
    background: #ffffff0d;
    color: #edf3f9;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 0.95em;
  }
  #warning svg {
    width: 64px;
    height: 64px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .play-circle {
    width: 88px;
    height: 88px;
    border: 1px solid #ffffff4d;
    border-radius: 50%;
    display: grid;
    place-items: center;
    background: #ffffff10;
    transition:
      background 0.15s,
      transform 0.15s;
  }
  #play:hover .play-circle {
    background: #ffffff20;
    transform: scale(1.04);
  }
  #play svg {
    width: 30px;
    height: 30px;
    margin-left: 5px;
    fill: currentColor;
  }
  .play-label {
    font-size: 15px;
    color: #e0e5ec;
    max-width: 480px;
    padding: 0 24px;
  }
  #empty {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    pointer-events: none;
  }
  .connection-pending {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
    width: 220px;
    color: #c0cbd5;
    font-size: 13px;
    font-weight: 500;
    line-height: 20px;
  }
  .connection-pending svg {
    width: 56px;
    height: 40px;
    overflow: visible;
    stroke-width: 1.5;
  }
  .viewfinder {
    stroke: #748493;
  }
  .connection-lens {
    stroke: #ffbb7738;
  }
  .connection-sweep {
    stroke: #ffbb77;
    filter: drop-shadow(0 0 4px #ffbb7750);
    transform-origin: 28px 20px;
    animation: connection-search 2.4s linear infinite;
  }
  #empty[data-retrying] .connection-sweep {
    animation-duration: 4s;
  }
  @keyframes connection-search {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .connection-sweep {
      animation: none;
    }
  }
  footer {
    position: absolute;
    inset: auto 0 0;
    padding: 36px 30px 24px;
    display: grid;
    grid-template-columns: minmax(0, 895px) minmax(0, 1fr);
    align-items: end;
    gap: 12px 20px;
    background: linear-gradient(transparent, #0009);
    z-index: 3;
    pointer-events: none;
  }
  .light-area {
    display: grid;
    justify-items: end;
    gap: 8px;
    max-width: 240px;
  }
  .light-area[data-error] {
    padding: 10px;
    border: 1px solid #ff9999;
    border-radius: 20px;
    pointer-events: auto;
  }
  .light-area[data-error]:hover {
    background: #17202be8;
  }
  #light-error,
  #notice {
    color: #ffb5b5;
    font-size: 12px;
    line-height: 1.5;
  }
  #notice {
    grid-column: 1;
  }
  .stream-controls {
    grid-column: 2;
    grid-row: 1;
    display: flex;
    flex-direction: column;
    align-items: end;
    gap: 8px;
    max-width: min(300px, 100%);
    justify-self: end;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  #camera {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 6px 10px;
    border: 1px solid transparent;
    border-radius: 14px;
    background: transparent;
    pointer-events: auto;
    font-variant-numeric: tabular-nums;
    font-size: 12px;
    color: #d7dee7;
    line-height: 1.5;
  }
  #camera:hover {
    background: #17202be8;
    border-color: #ffffff40;
  }
  #camera[data-state='error'] {
    border-color: #ff9999;
  }
  .stream-dot {
    flex: 0 0 6px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #7abaff;
  }
  #camera[data-state='waiting'] .stream-dot {
    background: #ffbb77;
  }
  #camera[data-state='error'] .stream-dot {
    background: #ff9999;
  }
  #stop {
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 10px 16px;
    border: 1px solid #ffffff40;
    border-radius: 999px;
    background: #17202bd9;
    opacity: 0;
    transition: opacity 0.15s;
  }
  #stop svg {
    width: 12px;
    height: 12px;
    fill: currentColor;
  }
  .viewer:hover #stop,
  .viewer:focus-within #stop {
    opacity: 1;
  }
  @media (hover: none) {
    #stop {
      opacity: 1;
    }
  }
  @media (max-width: 1080px) {
    footer {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    footer :global(#stats) {
      grid-column: 1 / -1;
    }
    .stream-controls {
      grid-row: 2;
    }
  }
  @media (max-width: 550px) {
    header {
      padding: 18px;
    }
    footer {
      padding: 36px 18px 18px;
    }
    #target {
      max-width: 180px;
    }
  }
</style>
