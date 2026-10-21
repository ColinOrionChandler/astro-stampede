/* Pixel geometry and point persistence are independent of activity scoring. */
function imagePixel(clientX, clientY, rect, width, height) {
  if (!(rect.width > 0 && rect.height > 0 && width > 0 && height > 0)) return null;
  const dx = clientX - rect.left;
  const dy = clientY - rect.top;
  if (!Number.isFinite(dx) || !Number.isFinite(dy)
      || dx < 0 || dy < 0 || dx >= rect.width || dy >= rect.height) return null;
  return {
    x: Math.min(width - 1, Math.floor(dx * width / rect.width)),
    y: Math.min(height - 1, Math.floor(dy * height / rect.height)),
  };
}

class PointController {
  constructor({request, isTransitioning = () => false, onDiscard = () => {}}) {
    this.request = request;
    this.isTransitioning = isTransitioning;
    this.onDiscard = onDiscard;
    this.panels = new Map();
    this.values = new Map();
    this.queue = [];
    this.running = null;
    this.error = null;
    this.gesture = null;
  }

  get holding() { return Boolean(this.gesture); }

  attach(img, marker, readout, clear) {
    this.panels.set(img, {marker, readout, clear, frame: null, loaded: false});
    img.draggable = false;
    img.addEventListener('pointerdown', event => this.beginGesture(img, event));
    img.addEventListener('pointermove', event => {
      if (this.gesture?.pointerId === event.pointerId
          && Math.hypot(event.clientX - this.gesture.clientX, event.clientY - this.gesture.clientY) > 5) {
        this.gesture.dragged = true;
      }
    });
    img.addEventListener('pointerup', event => this.endGesture(img, event));
    for (const type of ['pointercancel', 'lostpointercapture']) {
      img.addEventListener(type, () => this.cancelGesture());
    }
    clear.addEventListener('click', () => {
      const panel = this.panels.get(img);
      if (panel.loaded && !this.isTransitioning()) this.enqueue(panel.frame.point_target, null);
    });
  }

  bindStatus(status, retry, discard) {
    this.status = status;
    this.retry = retry;
    this.discard = discard;
    retry.addEventListener('click', () => this.flush().catch(() => {}));
    discard.addEventListener('click', () => {
      if (this.running) return;
      // Revert each target to the state before its first unsaved operation.
      const seen = new Set();
      for (const op of this.queue) {
        if (!seen.has(op.target.target_id)) this.values.set(op.target.target_id, op.previous);
        seen.add(op.target.target_id);
      }
      this.queue = [];
      this.error = null;
      this.render();
      this.onDiscard([...seen]);
    });
    this.render();
  }

  beginFrame(img, frame, src) {
    const panel = this.panels.get(img);
    if (!panel) return;
    const same = panel.src === src
      && panel.frame?.point_target?.target_id === frame?.point_target?.target_id
      && panel.frame?.point_target?.source_revision === frame?.point_target?.source_revision;
    if (!same) {
      if (this.gesture?.img === img) this.cancelGesture();
      panel.loaded = false;
    }
    panel.frame = frame;
    panel.src = src;
    this.render();
  }

  loaded(img, src) {
    const panel = this.panels.get(img);
    if (!panel || panel.src !== src) return;
    const target = panel.frame?.point_target;
    panel.loaded = Boolean(target && img.complete && img.getAttribute('src') === src
      && img.naturalWidth === target.width && img.naturalHeight === target.height);
    this.render();
  }

  invalidate(img) {
    const panel = this.panels.get(img);
    if (panel) panel.loaded = false;
    if (this.gesture?.img === img) this.cancelGesture();
    this.render();
  }

  beginGesture(img, event) {
    const panel = this.panels.get(img);
    if (!panel?.loaded || this.isTransitioning() || this.gesture || event.button !== 0
        || event.isPrimary === false || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
    const target = {...panel.frame.point_target};
    const pixel = imagePixel(event.clientX, event.clientY, img.getBoundingClientRect(), target.width, target.height);
    if (!pixel) return;
    event.preventDefault();
    this.gesture = {img, target, pixel, pointerId: event.pointerId,
      clientX: event.clientX, clientY: event.clientY, dragged: false};
    img.setPointerCapture(event.pointerId);
  }

  endGesture(img, event) {
    const gesture = this.gesture;
    if (!gesture || gesture.img !== img || gesture.pointerId !== event.pointerId) return;
    const panel = this.panels.get(img);
    const moved = Math.hypot(event.clientX - gesture.clientX, event.clientY - gesture.clientY) > 5;
    const inside = imagePixel(event.clientX, event.clientY, img.getBoundingClientRect(),
      gesture.target.width, gesture.target.height);
    this.cancelGesture();
    if (!gesture.dragged && !moved && inside && panel.loaded && !this.isTransitioning()
        && panel.frame.point_target.target_id === gesture.target.target_id
        && panel.frame.point_target.source_revision === gesture.target.source_revision) {
      this.enqueue(gesture.target, gesture.pixel);
    }
  }

  cancelGesture() {
    const gesture = this.gesture;
    this.gesture = null;
    if (gesture?.img.hasPointerCapture(gesture.pointerId)) gesture.img.releasePointerCapture(gesture.pointerId);
  }

  pointFor(frame) {
    const id = frame?.point_target?.target_id;
    return this.values.has(id) ? this.values.get(id) : frame?.point;
  }

  enqueue(target, pixel) {
    const frame = [...this.panels.values()].find(panel => panel.frame?.point_target?.target_id === target.target_id)?.frame;
    const previous = this.pointFor(frame) || null;
    const point = pixel ? {...target, ...pixel} : null;
    this.values.set(target.target_id, point);
    this.queue.push({target: {...target}, point, previous});
    this.render();
    if (!this.error) this.flush().catch(() => {});
  }

  flush(options = {}) {
    if (this.running) return this.running;
    if (!this.queue.length) return Promise.resolve();
    this.error = null;
    this.running = this.drain(options).finally(() => {
      this.running = null;
      this.render();
    });
    this.render();
    return this.running;
  }

  async drain(options) {
    try {
      while (this.queue.length) {
        const op = this.queue[0];
        const result = await this.request(op.point ? '/api/image-point' : '/api/image-point/clear', {
          method: 'POST', keepalive: Boolean(options.keepalive),
          body: JSON.stringify({target_id: op.target.target_id, source_revision: op.target.source_revision,
            ...(op.point ? {x: op.point.x, y: op.point.y} : {})}),
        });
        this.queue.shift();
        if (!this.queue.some(other => other.target.target_id === op.target.target_id)) {
          this.values.set(op.target.target_id, result.point);
        }
        this.render();
      }
    } catch (error) {
      this.error = error;
      throw error;
    }
  }

  render() {
    for (const panel of this.panels.values()) {
      const target = panel.frame?.point_target;
      const point = this.pointFor(panel.frame);
      const stale = point && target && point.source_revision !== target.source_revision;
      panel.marker.hidden = !panel.loaded || !point || stale || this.isTransitioning();
      if (!panel.marker.hidden) {
        panel.marker.style.left = `${100 * (point.x + 0.5) / target.width}%`;
        panel.marker.style.top = `${100 * (point.y + 0.5) / target.height}%`;
      }
      panel.readout.textContent = point
        ? `x = ${point.x}, y = ${point.y}${stale ? ' · Stale: source image changed' : ''}`
        : 'No point';
      panel.clear.disabled = !panel.loaded || !point || this.isTransitioning();
    }
    if (this.status) {
      this.status.textContent = this.error ? `Point save failed: ${this.error.message}`
        : this.queue.length ? `Saving ${this.queue.length} point change(s)…` : 'Points saved';
      this.status.className = `save-status ${this.error ? 'error' : this.queue.length ? 'queued' : 'saved'}`;
      this.retry.hidden = !this.error;
      this.discard.hidden = !this.error;
    }
  }
}

if (typeof module !== 'undefined') module.exports = {imagePixel, PointController};
