import type { HostMessage, OrcaBridge, PrintStats, StatusDelivery } from './types';

type StreamState =
  | 'live' // Fresh images are arriving; show FPS with a blue dot.
  | 'waiting' // Connecting or interrupted for at most five seconds; show an orange counter.
  | 'error'; // Interrupted for more than five seconds; show the reason with a red outline.

function statusGuidance(category: string | undefined): string {
  switch (category) {
    case 'timeout': // The live status operation exceeded its deadline.
      return 'Status timed out. Check the printer connection.';
    case 'access_denied': // The printer denied authentication or LAN access.
      return 'The printer denied access. Check its connection settings.';
    case 'invalid_response': // The response cannot supply valid printer readings.
      return 'Invalid status response. Check the printer address.';
    default: // Unreachable, unknown and missing categories use safe generic guidance.
      return 'Status unavailable. Check the printer address and connection.';
  }
}

export class Camera {
  playing = $state(false);
  supported = $state(false);
  targetUnavailable = $state(false);
  target = $state('Selected printer');
  printer = $state('Connecting');
  warning = $state('Set the printer address in plugin Config.');
  imageVisible = $state(false);
  fps = $state(0);
  now = $state(Date.now());
  issueAt = $state<number | null>(null);
  issue = $state('');
  notice = $state('');
  lightError = $state('');
  statusError = $state('');
  private noticeAt: number | null = null;
  seconds = $derived(this.issueAt === null ? 0 : Math.floor((this.now - this.issueAt) / 1000));
  streamState: StreamState = $derived(
    this.issueAt === null ? 'live' : this.seconds > 5 ? 'error' : 'waiting',
  );
  status = $derived(
    this.issueAt === null
      ? this.fps.toFixed(1) + ' FPS'
      : this.seconds > 5
        ? this.issue + ' · ' + this.seconds + 's'
        : (this.imageVisible ? '' : 'Connecting · ') + this.seconds + 's',
  );
  light = $state<boolean | null>(null);
  lightDisabled = $state(true);
  private lightAt: number | null = null;
  stats = $state<PrintStats | null>(null);
  statsAt = $state(0);
  lastSuccessfulStatusAt = $state<number | null>(null);
  private active = $state(false);
  statusDelivery: StatusDelivery | null = $derived(
    !this.active || this.targetUnavailable
      ? null
      : this.stats && this.stats.state !== 'unavailable'
        ? 'fresh'
        : this.stats?.state === 'unavailable' ||
            this.statusError ||
            this.lastSuccessfulStatusAt !== null
          ? 'retrying'
          : 'waiting',
  );
  private pendingNotice = '';
  private generation = -1;
  private lastFrame = 0;
  private frameURL: string | null = null;

  constructor(
    private bridge: OrcaBridge,
    private image: HTMLImageElement,
  ) {}

  private setStats(stats: PrintStats | null = null, unavailable = false) {
    this.stats = stats ?? (unavailable ? { state: 'unavailable' } : null);
    this.statsAt = this.stats && this.stats.state !== 'unavailable' ? Date.now() : 0;
    if (this.stats && this.stats.state !== 'unavailable')
      this.lastSuccessfulStatusAt = this.statsAt;
  }

  private resetStatus() {
    this.setStats();
    this.lastSuccessfulStatusAt = null;
    this.statusError = '';
    this.printer = 'Connecting';
    this.light = null;
    this.lightAt = null;
    this.lightDisabled = true;
    this.lightError = this.notice = this.pendingNotice = '';
    this.noticeAt = null;
  }

  private releaseFrame() {
    this.image.onload = this.image.onerror = null;
    if (this.frameURL !== null) URL.revokeObjectURL(this.frameURL);
    this.frameURL = null;
  }

  private clearImage() {
    this.releaseFrame();
    this.imageVisible = false;
    this.image.removeAttribute('src');
  }

  stop = () => {
    this.playing = false;
    this.clearImage();
    this.issueAt = null;
    this.issue = '';
    this.fps = 0;
  };

  play = () => {
    if (document.hidden || !this.supported) return;
    this.playing = true;
    this.notice = '';
    this.noticeAt = null;
    this.pendingNotice = '';
    this.lastFrame = Date.now();
    this.issueAt = this.now = Date.now();
    this.issue = 'Camera not responding';
    this.tick();
  };

  setLight = (enabled: boolean) => {
    if (this.active && !document.hidden && !this.lightDisabled && this.light !== null) {
      this.lightError = '';
      this.bridge.postMessage({ type: 'light', enabled, generation: this.generation });
    }
  };

  private interrupt(message: string) {
    this.issueAt ??= Date.now();
    this.now = Date.now();
    this.issue = message.replace(/\.$/, '');
  }

  tick = () => {
    this.now = Date.now();
    if (this.noticeAt !== null && this.now - this.noticeAt > 5000) this.notice = this.pendingNotice;
    if (this.stats && this.stats.state !== 'unavailable' && this.now - this.statsAt >= 10000)
      this.setStats(null, true);
    if (this.lightAt !== null && this.now - this.lightAt >= 10000) {
      this.light = null;
      this.lightDisabled = true;
      this.lightAt = null;
    }
    if (document.hidden) {
      if (this.active) this.hide();
      return;
    }
    this.active = true;
    if (this.playing && this.imageVisible && this.now - this.lastFrame > 1000) {
      this.issueAt ??= this.lastFrame;
      if (!this.issue) this.issue = 'Camera not updating';
    }
    this.bridge.postMessage({
      type: 'activity',
      active: true,
      playing: this.playing,
      ...(this.generation >= 0 ? { generation: this.generation } : {}),
    });
  };

  private hide = () => {
    this.stop();
    this.resetStatus();
    this.active = false;
    this.lightDisabled = true;
    this.bridge.postMessage({
      type: 'activity',
      active: false,
      ...(this.generation >= 0 ? { generation: this.generation } : {}),
    });
    this.generation = -1;
  };

  private receive = (data: HostMessage) => {
    if (!this.active || document.hidden) return;
    if (data.type === 'selection_changed') {
      this.generation = -1;
      this.resetStatus();
      this.stop();
      this.notice = this.lightError = '';
      this.noticeAt = null;
      this.supported = false;
      this.lightDisabled = true;
      this.tick();
      return;
    }
    if (data.type === 'target' && data.generation !== undefined && data.target !== undefined) {
      if (this.generation >= 0 && data.generation < this.generation) return;
      const changed = this.generation !== data.generation || this.target !== data.target;
      if (changed) this.resetStatus();
      this.supported = true;
      if (this.targetUnavailable) {
        this.warning = '';
        this.notice = '';
        this.noticeAt = null;
      }
      this.targetUnavailable = false;
      if (this.generation !== data.generation) {
        this.clearImage();
        this.lastFrame = this.now = Date.now();
        this.issueAt = this.playing ? this.lastFrame : null;
        this.issue = this.playing ? 'Camera not responding' : '';
        this.fps = 0;
      }
      this.generation = data.generation;
      this.target = data.target;
      return;
    }
    if (data.generation !== undefined && data.generation !== this.generation) return;
    if (data.error) {
      this.setStats(null, true);
      if (typeof data.supported === 'boolean') {
        this.resetStatus();
        this.lightError = this.notice = '';
        this.noticeAt = null;
        this.targetUnavailable = true;
        this.generation = -1;
        this.stop();
        this.supported = false;
        this.target = 'Printer address unavailable';
        this.printer = 'Unavailable';
        this.warning = data.error;
      }
      this.lightDisabled = true;
      if (!this.targetUnavailable) {
        if (this.playing) this.interrupt(data.error);
        else {
          this.noticeAt ??= Date.now();
          this.pendingNotice = data.error;
        }
      }
      return;
    }
    if ('light_error' in data) this.lightError = data.light_error ?? '';
    if (data.printer) this.printer = data.printer;
    if ('light' in data) {
      this.light = data.light ?? null;
      this.lightDisabled = this.light === null;
      this.lightAt = this.light !== null ? Date.now() : null;
      if (this.light !== null) {
        this.notice = '';
        this.noticeAt = null;
      }
    }
    if ('stats' in data || 'status_error' in data) {
      const failed = 'status_error' in data || !data.stats || data.stats.state === 'unavailable';
      this.setStats(failed ? null : data.stats, true);
      this.statusError = failed ? statusGuidance(data.status_error) : '';
      if (failed) {
        this.light = null;
        this.lightDisabled = true;
        this.lightAt = null;
      }
    }
    if (data.failed && data.camera) {
      if (this.playing) this.interrupt(data.camera);
      else {
        this.noticeAt ??= Date.now();
        this.pendingNotice = data.camera.replace(/\.$/, '');
      }
    }
    if (!this.playing) return;
    if (data.image) {
      const { generation: frameGeneration, fps = 0, frozen } = data;
      const acknowledge = () => {
        if (this.active && this.playing && this.generation === frameGeneration)
          this.bridge.postMessage({ type: 'frame', generation: frameGeneration });
      };
      this.releaseFrame();
      let url: string;
      try {
        const prefix = /^data:(image\/[a-z0-9.+-]+);base64,/i.exec(data.image);
        if (!prefix) throw new Error('Invalid camera image');
        const bytes = Uint8Array.from(atob(data.image.slice(prefix[0].length)), (c) =>
          c.charCodeAt(0),
        );
        url = URL.createObjectURL(new Blob([bytes], { type: prefix[1] }));
      } catch {
        this.clearImage();
        this.interrupt('Could not display camera image');
        acknowledge();
        return;
      }
      this.frameURL = url;
      // Revocable URLs keep WebKit from retaining each frame's encoded data URL.
      this.image.onload = () => {
        if (this.frameURL !== url) return;
        if (this.active && this.playing && this.generation === frameGeneration) {
          this.lastFrame = Date.now();
          this.imageVisible = true;
          this.now = Date.now();
          this.fps = fps;
          if (frozen) this.interrupt('Camera not updating');
          else {
            this.issueAt = null;
            this.issue = '';
          }
        }
        this.releaseFrame();
        acknowledge();
      };
      this.image.onerror = () => {
        if (this.frameURL !== url) return;
        this.clearImage();
        this.interrupt('Could not display camera image');
        acknowledge();
      };
      this.image.src = url;
    }
  };

  start() {
    this.bridge.onMessage(this.receive);
    const error = (event: ErrorEvent) => {
      this.notice = 'Player error: ' + event.message;
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
