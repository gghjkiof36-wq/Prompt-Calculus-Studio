import {HOST_NAME} from './core.mjs';

export class BridgeError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

export class NativeClient {
  constructor(runtime, {timeout = 20000, idle = 45000} = {}) {
    this.runtime = runtime;
    this.timeout = timeout;
    this.idle = idle;
    this.port = null;
    this.pending = new Map();
    this.idleTimer = null;
  }
  connect() {
    if (this.port) return this.port;
    let port;
    try { port = this.runtime.connectNative(HOST_NAME); }
    catch { throw new BridgeError('host_unavailable', '無法連接 PCS。'); }
    this.port = port;
    port.onMessage.addListener(message => {
      if (!message || typeof message.request_id !== 'string') return;
      const pending = this.pending.get(message.request_id);
      if (!pending) return;
      this.pending.delete(message.request_id);
      clearTimeout(pending.timer);
      if (message.ok === true && message.result && typeof message.result === 'object') pending.resolve(message.result);
      else pending.reject(new BridgeError(message.error?.code || 'invalid_response', message.error?.message || 'PCS 回應失敗。'));
      this.scheduleIdle();
    });
    port.onDisconnect.addListener(() => {
      // Read lastError even if no request is outstanding; Chrome otherwise logs it.
      const message = this.runtime.lastError?.message || '';
      if (this.port !== port) return;
      this.port = null;
      clearTimeout(this.idleTimer);
      const code = /not found|not registered|forbidden|specified native messaging host/i.test(message) ? 'host_unavailable' : 'disconnected';
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new BridgeError(code, '與 PCS 的連線已中斷。'));
      }
      this.pending.clear();
    });
    return port;
  }
  scheduleIdle() {
    clearTimeout(this.idleTimer);
    if (this.pending.size) return;
    this.idleTimer = setTimeout(() => this.close(), this.idle);
    this.idleTimer.unref?.();
  }
  request(command, fields = {}) {
    clearTimeout(this.idleTimer);
    const request_id = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      let port;
      try { port = this.connect(); }
      catch (error) { reject(error); return; }
      const timer = setTimeout(() => {
        this.pending.delete(request_id);
        reject(new BridgeError('timeout', 'PCS 暫時沒有回應。'));
        this.scheduleIdle();
      }, this.timeout);
      this.pending.set(request_id, {resolve, reject, timer});
      try { port.postMessage({...fields, command, request_id}); }
      catch {
        clearTimeout(timer);
        this.pending.delete(request_id);
        reject(new BridgeError('disconnected', '與 PCS 的連線已中斷。'));
        this.scheduleIdle();
      }
    });
  }
  close() {
    clearTimeout(this.idleTimer);
    const port = this.port;
    this.port = null;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new BridgeError('disconnected', '與 PCS 的連線已中斷。'));
    }
    this.pending.clear();
    port?.disconnect();
  }
}
