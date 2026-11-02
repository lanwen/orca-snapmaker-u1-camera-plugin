<script lang="ts">
  import { onMount } from 'svelte';
  import { Camera } from './camera.svelte';
  import PrintStats from './PrintStats.svelte';
  import LightControl from './LightControl.svelte';

  let image: HTMLImageElement;
  let camera = $state<Camera>();
  onMount(() => {
    camera = new Camera(window.orca, image);
    return camera.start();
  });
</script>

<main class="viewer" aria-label="Snapmaker U1 camera">
  <img bind:this={image} id="image" alt="Snapmaker U1 camera" hidden={!camera?.imageVisible} />
  <header>
    <div>
      <h1>Snapmaker U1 <span id="printer">{camera?.printer ?? 'Connecting'}</span></h1>
      <div id="target">{camera?.target ?? 'Selected printer'}</div>
    </div>
    <LightControl
      value={camera?.light ?? null}
      disabled={camera?.lightDisabled ?? true}
      onchange={(enabled) => camera?.setLight(enabled)}
    />
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
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 21h20zM12 9v5m0 3v.01" /></svg
    ><span id="warning-label">{camera?.warning}</span>
  </div>
  <div id="empty" hidden={!camera?.playing || camera?.imageVisible}>Connecting to camera…</div>
  <footer>
    <PrintStats stats={camera?.stats ?? null} receivedAt={camera?.statsAt ?? 0} />
    <div id="camera" role="status">{camera?.status ?? ''}</div>
    <button
      id="stop"
      type="button"
      hidden={!camera?.playing}
      aria-label="Stop camera"
      onclick={() => {
        camera?.stop();
        camera?.tick();
      }}
      ><svg viewBox="0 0 12 12" aria-hidden="true"><rect width="12" height="12" rx="2" /></svg
      >Stop</button
    >
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
    margin: 0 0 5px;
    letter-spacing: -0.3px;
  }
  #target {
    font-size: 12px;
    color: #ffffffa0;
    overflow-wrap: anywhere;
  }
  #printer {
    margin-left: 10px;
    font-size: 12px;
    font-weight: 400;
    color: #ffffffb0;
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
    color: #ffffff80;
    pointer-events: none;
  }
  footer {
    position: absolute;
    inset: auto 0 0;
    padding: 36px 30px 24px;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: 12px 20px;
    background: linear-gradient(transparent, #0009);
    z-index: 3;
    pointer-events: none;
  }
  #camera {
    font-size: 12px;
    color: #d7dee7;
    line-height: 1.5;
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
