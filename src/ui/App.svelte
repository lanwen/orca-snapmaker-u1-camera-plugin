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
