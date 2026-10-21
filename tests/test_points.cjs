const test = require('node:test');
const assert = require('node:assert/strict');
const {imagePixel, PointController} = require('../src/astro_stampede/review_static/points.js');

function element() {
  return {style: {}, hidden: false, textContent: '', disabled: false, listeners: {},
    complete: true, naturalWidth: 100, naturalHeight: 80, src: '/one.png', captured: null,
    addEventListener(name, fn) { this.listeners[name] = fn; },
    getAttribute(name) { return this[name]; },
    getBoundingClientRect() { return {left: -20, top: 10, width: 200, height: 160}; },
    setPointerCapture(id) { this.captured = id; },
    hasPointerCapture(id) { return this.captured === id; },
    releasePointerCapture() { this.captured = null; },
  };
}

const target = {target_id: 'one', source_revision: 'v1', width: 100, height: 80};
const event = (extra = {}) => ({pointerId: 1, button: 0, clientX: 80, clientY: 90,
  preventDefault() {}, ...extra});

function setup(request = async (url, options) => ({point: url.endsWith('/clear') ? null : {...target, ...JSON.parse(options.body)}})) {
  let transitioning = false;
  const controller = new PointController({request, isTransitioning: () => transitioning});
  const [img, marker, readout, clear, status, retry, discard] = Array.from({length: 7}, element);
  controller.attach(img, marker, readout, clear);
  controller.bindStatus(status, retry, discard);
  controller.beginFrame(img, {point_target: target, point: null}, img.src);
  controller.loaded(img, img.src);
  return {controller, img, marker, readout, clear, status, retry, discard,
    transition(value) { transitioning = value; }};
}

test('whole pixel geometry includes corners, boundaries, Fit, zoom, scroll, and rejects padding', () => {
  for (const scale of [0.25, 0.5, 1, 2, 4]) {
    const rect = {left: -101, top: 37, width: 600 * scale, height: 200 * scale};
    assert.deepEqual(imagePixel(rect.left, rect.top, rect, 600, 200), {x: 0, y: 0});
    assert.deepEqual(imagePixel(rect.left + 31 * scale, rect.top + 17.9 * scale, rect, 600, 200), {x: 31, y: 17});
    assert.deepEqual(imagePixel(rect.left + rect.width - 0.001, rect.top + rect.height - 0.001,
      rect, 600, 200), {x: 599, y: 199});
    assert.equal(imagePixel(rect.left + rect.width, rect.top, rect, 600, 200), null);
    assert.equal(imagePixel(rect.left - 0.01, rect.top, rect, 600, 200), null);
    assert.equal(imagePixel(rect.left, rect.top + rect.height, rect, 600, 200), null);
  }
  assert.equal(imagePixel(0, 0, {left: 0, top: 0, width: 0, height: 1}, 1, 1), null);
});

test('a click captures the displayed frame and renders the source pixel center', async () => {
  const ui = setup();
  ui.img.listeners.pointerdown(event());
  assert.equal(ui.controller.holding, true);
  ui.img.listeners.pointerup(event());
  assert.equal(ui.controller.holding, false);
  await ui.controller.flush();
  assert.equal(ui.marker.hidden, false);
  assert.equal(ui.marker.style.left, '50.5%');
  assert.equal(ui.marker.style.top, '50.625%');
  assert.match(ui.readout.textContent, /x = 50, y = 40/);
  ui.clear.listeners.click();
  await ui.controller.flush();
  assert.equal(ui.marker.hidden, true);
  assert.equal(ui.readout.textContent, 'No point');
});

test('drag, cancel, unloaded image, transitions, and frame changes never record a point', () => {
  const ui = setup();
  for (const cancel of [
    () => ui.img.listeners.pointermove(event({clientX: 120})),
    () => ui.img.listeners.pointercancel(event()),
    () => ui.controller.invalidate(ui.img),
    () => ui.transition(true),
    () => ui.controller.beginFrame(ui.img, {point_target: {...target, target_id: 'two'}}, '/two.png'),
  ]) {
    ui.transition(false);
    ui.controller.beginFrame(ui.img, {point_target: target}, '/one.png');
    ui.controller.loaded(ui.img, '/one.png');
    ui.img.listeners.pointerdown(event());
    cancel();
    ui.img.listeners.pointerup(event());
    assert.equal(ui.controller.queue.length, 0);
    assert.equal(ui.controller.values.size, 0);
  }
  ui.controller.invalidate(ui.img);
  ui.img.listeners.pointerdown(event());
  assert.equal(ui.controller.holding, false);
});

test('independent comparison frames restore points and suppress stale or loading markers', async () => {
  const ui = setup();
  ui.controller.enqueue(target, {x: 20, y: 30});
  await ui.controller.flush();
  const other = {target_id: 'comparison:two', source_revision: 'v2', width: 50, height: 25};
  ui.img.src = '/two.png';
  ui.img.naturalWidth = 50;
  ui.img.naturalHeight = 25;
  ui.controller.beginFrame(ui.img, {point_target: other, point: {...other, x: 3, y: 4}}, '/two.png');
  assert.equal(ui.marker.hidden, true);
  ui.controller.loaded(ui.img, '/two.png');
  assert.match(ui.readout.textContent, /x = 3, y = 4/);
  assert.equal(ui.marker.style.left, '7%');
  ui.img.src = '/one.png';
  ui.img.naturalWidth = 100;
  ui.img.naturalHeight = 80;
  ui.controller.beginFrame(ui.img, {point_target: target}, '/one.png');
  ui.controller.loaded(ui.img, '/one.png');
  assert.match(ui.readout.textContent, /x = 20, y = 30/);
  ui.controller.beginFrame(ui.img, {point_target: {...target, source_revision: 'new'}}, '/one.png');
  ui.controller.loaded(ui.img, '/one.png');
  assert.equal(ui.marker.hidden, true);
  assert.match(ui.readout.textContent, /Stale/);
});

test('ordered saves preserve a newer marker across older responses and navigation', async () => {
  const requests = [];
  const ui = setup((url, options) => new Promise((resolve) => requests.push({url, options, resolve})));
  ui.controller.enqueue(target, {x: 1, y: 2});
  const saving = ui.controller.flush();
  ui.controller.enqueue(target, {x: 5, y: 6});
  assert.equal(requests.length, 1);
  requests[0].resolve({point: {...target, x: 1, y: 2}});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(ui.readout.textContent, /x = 5, y = 6/);
  assert.equal(requests.length, 2);
  ui.controller.beginFrame(ui.img, null, null);
  requests[1].resolve({point: {...target, x: 5, y: 6}});
  await saving;
  ui.controller.beginFrame(ui.img, {point_target: target}, '/one.png');
  ui.controller.loaded(ui.img, '/one.png');
  assert.match(ui.readout.textContent, /x = 5, y = 6/);
  assert.equal(ui.controller.queue.length, 0);
});

test('failed saves stay queued, Retry drains them, and discard restores the saved point', async () => {
  let fail = true;
  const ui = setup(async (_url, options) => {
    if (fail) throw new Error('offline');
    return {point: {...target, ...JSON.parse(options.body)}};
  });
  ui.controller.enqueue(target, {x: 1, y: 2});
  await assert.rejects(ui.controller.flush(), /offline/);
  assert.equal(ui.controller.queue.length, 1);
  assert.equal(ui.retry.hidden, false);
  assert.match(ui.status.textContent, /failed.*offline/);
  fail = false;
  await ui.controller.flush();
  assert.equal(ui.controller.queue.length, 0);
  assert.equal(ui.retry.hidden, true);
  fail = true;
  ui.controller.enqueue(target, {x: 3, y: 4});
  await assert.rejects(ui.controller.flush(), /offline/);
  ui.controller.enqueue(target, null);
  ui.discard.listeners.click();
  assert.match(ui.readout.textContent, /x = 1, y = 2/);
  assert.equal(ui.controller.queue.length, 0);
});
