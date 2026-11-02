import type { HostMessage, OrcaBridge, PrintStats } from './types';

export class Camera {
  playing = $state(false);
  supported = $state(false);
  targetUnavailable = $state(false);
  target = $state('Selected printer');
  printer = $state('Connecting');
  warning = $state('Set the printer address in plugin Config.');
  status = $state('');
  light = $state<boolean | null>(null);
  lightDisabled = $state(true);
  imageVisible = $state(false);
  stats = $state<PrintStats | null>(null);
  statsAt = $state(0);
  private active = false;
  private generation = -1;
  private lastFrame = 0;

  constructor(
    private bridge: OrcaBridge,
    private image: HTMLImageElement,
  ) {}

  private setStats(stats: PrintStats | null = null, unavailable = false) {
    this.stats = stats ?? (unavailable ? { state: 'unavailable' } : null);
    this.statsAt = this.stats && this.stats.state !== 'unavailable' ? Date.now() : 0;
  }

  private clearImage() {
    this.imageVisible = false;
    this.image.removeAttribute('src');
  }

  stop = () => {
    this.playing = false;
    this.clearImage();
    this.status = '';
  };

  play = () => {
    if (document.hidden || !this.supported) return;
    this.playing = true;
    this.lastFrame = Date.now();
    this.status = 'Connecting…';
    this.tick();
  };

  toggleLight = () => {
    if (this.active && !document.hidden && this.light !== null)
      this.bridge.postMessage({ type: 'light', enabled: !this.light });
  };

  tick = () => {
    if (this.statsAt && Date.now() - this.statsAt > 10000) this.setStats(null, true);
    if (document.hidden) {
      if (this.active) this.hide();
      return;
    }
    this.active = true;
    if (this.playing && Date.now() - this.lastFrame > 6000) {
      this.clearImage();
      this.status = 'Waiting for camera — no recent image';
    }
    this.bridge.postMessage({ type: 'activity', active: true, playing: this.playing });
  };

  private hide = () => {
    this.stop();
    this.active = false;
    this.lightDisabled = true;
    this.bridge.postMessage({ type: 'activity', active: false });
  };

  private receive = (data: HostMessage) => {
    if (!this.active) return;
    if (data.type === 'selection_changed') {
      this.setStats();
      this.stop();
      this.supported = false;
      this.lightDisabled = true;
      this.tick();
      return;
    }
    if (data.type === 'target' && data.generation !== undefined && data.target !== undefined) {
      this.supported = true;
      this.targetUnavailable = false;
      if (this.generation !== data.generation) {
        this.clearImage();
        this.lastFrame = Date.now();
      }
      if (this.target !== data.target) {
        this.setStats();
        this.printer = 'Connecting';
        this.light = null;
        this.lightDisabled = true;
      }
      this.generation = data.generation;
      this.target = data.target;
      return;
    }
    if (data.generation !== undefined && data.generation !== this.generation) return;
    if (data.error) {
      this.setStats();
      if (typeof data.supported === 'boolean') {
        this.targetUnavailable = true;
        this.stop();
        this.supported = false;
        this.target = 'Printer address unavailable';
        this.printer = 'Unavailable';
        this.warning = data.error;
      }
      this.clearImage();
      this.lightDisabled = true;
      this.status = data.error;
      return;
    }
    if (data.printer) this.printer = data.printer;
    if ('stats' in data) this.setStats(data.stats, true);
    if ('light' in data) {
      this.light = data.light ?? null;
      this.lightDisabled = this.light === null;
    }
    if (data.camera && (this.playing || data.failed)) this.status = data.camera;
    if (data.failed) this.clearImage();
    if (!this.playing) return;
    if (data.image) {
      const frameGeneration = data.generation;
      const acknowledge = () => {
        if (this.active && this.playing && this.generation === frameGeneration)
          this.bridge.postMessage({ type: 'frame', generation: frameGeneration });
      };
      // Assign the source only after handlers, so cached frames are acknowledged too.
      this.image.onload = () => {
        if (this.active && this.playing && this.generation === frameGeneration) {
          this.lastFrame = Date.now();
          this.imageVisible = true;
        }
        acknowledge();
      };
      this.image.onerror = () => {
        this.clearImage();
        this.status = 'Could not display camera image';
        acknowledge();
      };
      this.image.src = data.image;
    }
  };

  start() {
    this.bridge.onMessage(this.receive);
    const error = (event: ErrorEvent) => {
      this.status = 'Player error: ' + event.message;
    };
    document.addEventListener('visibilitychange', this.tick);
    window.addEventListener('pageshow', this.tick);
    window.addEventListener('pagehide', this.hide);
    window.addEventListener('error', error);
    const timer = window.setInterval(this.tick, 1000);
    this.tick();
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', this.tick);
      window.removeEventListener('pageshow', this.tick);
      window.removeEventListener('pagehide', this.hide);
      window.removeEventListener('error', error);
      this.hide();
      this.image.onload = this.image.onerror = null;
    };
  }
}
