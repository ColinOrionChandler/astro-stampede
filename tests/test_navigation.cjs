const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = new Map();
  const listeners = {};
  const intervals = [];
  function element() {
    return {value: '', checked: false, open: false, dataset: {},
      addEventListener() {}, setAttribute() {}, replaceChildren() {},
      classList: {toggle() {}, add() {}, remove() {}}};
  }
  const context = vm.createContext({
    document: {getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
      querySelector() { return null; }},
    window: {addEventListener(name, fn) { listeners[name] = fn; },
      clearInterval() {}, setInterval(fn) { intervals.push(fn); return intervals.length; }},
    console,
  });
  const points = fs.readFileSync(require.resolve('../src/astro_stampede/review_static/points.js'), 'utf8');
  const source = fs.readFileSync(require.resolve('../src/astro_stampede/review_static/app.js'), 'utf8');
  vm.runInContext(points + '\n' + source.replace('boot().catch((error) => toast(error.message));', ''), context);
  vm.runInContext(`
    renderObjects = () => {};
    renderCurrent = () => {};
    resetReveal = () => {};
    toast = () => {};
    api = async () => [{image_id: 'scored', score_status: 'scored'}, {image_id: 'unscored'}];
    wireEvents();
  `, context);
  return {context, listeners, intervals, run: code => vm.runInContext(code, context)};
}

test('object navigation follows the loaded order, includes completed objects, and wraps both ways', async () => {
  const {run} = setup();
  run("state.objects = [{object_id: 'b', scored_count: 4}, {object_id: 'a'}, {object_id: 'c'}]");
  await run('moveObject(-1)');
  assert.equal(run('state.selectedObject.object_id'), 'c');
  assert.equal(run('state.index'), 1);
  await run('moveObject(1)');
  assert.equal(run('state.selectedObject.object_id'), 'b');
  await run('moveObject(1)');
  assert.equal(run('state.selectedObject.object_id'), 'a');
  run('state.objects = [state.selectedObject]');
  const token = run('state.imageRequestToken');
  await run('moveObject(1)');
  assert.equal(run('state.imageRequestToken'), token);
  run('state.objects = []; state.selectedObject = null;');
  await run('moveObject(-1)');
  assert.equal(run('state.selectedObject'), null);
});

test('navigation retains blink preference and only the newest object load can win', async () => {
  const {run, context} = setup();
  const pending = [];
  context.fetchImages = () => new Promise(resolve => pending.push(resolve));
  run(`fetchObjectImages = fetchImages; state.blinkEnabled = true; state.playing = true;
    state.objects = [{object_id: 'a'}, {object_id: 'b'}];`);
  const first = run('moveObject(1)');
  const second = run('moveObject(1)');
  assert.equal(run('state.objectLoading'), true);
  pending[1]([{image_id: 'b-image', score_status: 'scored'}]);
  await second;
  pending[0]([{image_id: 'a-image'}]);
  await first;
  assert.equal(run('state.images[0].image_id'), 'b-image');
  assert.equal(run('state.selectedObject.object_id'), 'b');
  assert.equal(run('state.objectLoading'), false);
  assert.equal(run('state.blinkEnabled'), true);
  assert.equal(run('state.playing'), false);
  assert.equal(run('state.index'), 0);
});

test('shortcuts ignore typing, dialogs, modifiers, composition, and repeats; arrows remain image navigation', async () => {
  const {run, context, listeners} = setup();
  const moves = [];
  context.objectMove = delta => { moves.push(['object', delta]); return Promise.resolve(); };
  context.imageMove = delta => moves.push(['image', delta]);
  run('moveObject = objectMove; move = imageMove;');
  function key(key, extra = {}) {
    listeners.keydown({key, preventDefault() {}, target: {matches: () => false}, ...extra});
  }
  key(','); key('.'); key('ArrowLeft'); key('ArrowRight');
  assert.deepEqual(moves, [['object', -1], ['object', 1], ['image', -1], ['image', 1]]);
  for (const extra of [{ctrlKey: true}, {metaKey: true}, {altKey: true}, {shiftKey: true},
    {repeat: true}, {isComposing: true}, {target: {matches: () => true}},
    {target: {matches: () => false, isContentEditable: true}}]) key('.', extra);
  run("$('settingsDialog').open = true");
  key(',');
  assert.equal(moves.length, 4);
});

test('playback and blink hold the selected frame through a click and resume after release', () => {
  const {run, intervals} = setup();
  run(`state.images = [{image_id: 'one', pairs: [{image_id: 'two'}]}, {image_id: 'three'}];
    $('rateInput').value = '2'; $('loopInput').checked = true;
    setImageSrc = () => {}; state.blinkEnabled = true;
    syncBlink(currentImage()); startPlayback(); pointReview.gesture = {};`);
  intervals[0](); intervals[1]();
  assert.equal(run('state.blinkOn'), false);
  assert.equal(run('state.index'), 0);
  run('pointReview.gesture = null');
  intervals[0]();
  assert.equal(run('state.blinkOn'), true);
  intervals[1]();
  assert.equal(run('state.index'), 1);
});

test('export waits for point writes and does not export after a save failure', async () => {
  const {run} = setup();
  run(`flushPendingSaves = async () => {}; pointReview.flush = async () => { throw new Error('offline'); };
    api = async () => { throw new Error('export must not run'); };`);
  await assert.rejects(run('exportLabels()'), /offline/);
});


test('source revisions distinguish browser cache entries for primary and comparison frames', () => {
  const {run} = setup();
  run("state.summary = {image_cache_token: 'epoch'}");
  assert.match(run("primaryImageUrl({image_id: 'one', point_target: {source_revision: 'v1'}})"), /source_revision=v1/);
  assert.notEqual(run("primaryImageUrl({image_id: 'one', point_target: {source_revision: 'v1'}})"),
    run("primaryImageUrl({image_id: 'one', point_target: {source_revision: 'v2'}})"));
  assert.match(run("blinkSource({comparison: {url: '/api/comparisons/two', point_target: {source_revision: 'v2'}}})"), /source_revision=v2/);
});
