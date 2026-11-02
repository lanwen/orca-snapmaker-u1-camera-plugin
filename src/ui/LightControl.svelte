<script lang="ts">
  let {
    value,
    disabled,
    onchange,
  }: {
    value: boolean | null;
    disabled: boolean;
    onchange: (enabled: boolean) => void;
  } = $props();

  function toggle() {
    if (!disabled && value !== null) onchange(!value);
  }
</script>

<button
  id="light"
  type="button"
  role="switch"
  aria-label="Chamber light"
  aria-checked={value === true}
  disabled={disabled || value === null}
  onclick={toggle}
>
  <span>Light</span>
  <span class="track" aria-hidden="true"><span class="thumb"></span></span>
</button>

<style>
  button {
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    border: 1px solid #ffffff30;
    border-radius: 999px;
    background: #161c24b3;
    backdrop-filter: blur(12px);
    white-space: nowrap;
  }
  .track {
    display: inline-flex;
    align-items: center;
    width: 38px;
    height: 22px;
    padding: 3px;
    background: #ffffff35;
    border-radius: 99px;
    transition: background 0.15s;
  }
  .thumb {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: white;
    transition: transform 0.15s;
  }
  button[aria-checked='true'] .track {
    background: #249b7c;
  }
  button[aria-checked='true'] .thumb {
    transform: translateX(16px);
  }

  @media (max-width: 550px) {
    button {
      gap: 8px;
      padding: 8px 10px;
    }
  }
</style>
