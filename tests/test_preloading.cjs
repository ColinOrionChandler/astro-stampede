const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const requests = [];
  const node = {addEventListener() {}};
  const context = vm.createContext({
    document: {getElementById() {return node;}},
    window: {addEventListener() {}},
    Image: class {set src(value) {this.url = value; requests.push(this);}},
  });
  const source = fs.readFileSync(require.resolve('../astro_stampede/review_static/app.js'), 'utf8');
  vm.runInContext(source.replace('boot().catch((error) => toast(error.message));', ''), context);
  vm.runInContext(`state.summary = {image_cache_token: 'session-0'};
    state.images = Array.from({length: 1000}, (_, i) => ({image_id: String(i), width: 500, height: 500,
      comparison: {url: '/api/comparisons/' + i}}));`, context);
  return {context, requests, run: s => vm.runInContext(s, context)};
}

test('preloading is bounded, serial, shared with display URLs, and skips obsolete queue', () => {
  const {requests, run} = setup();
  run('preloadNearbyImages()');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, run('primaryImageUrl(currentImage())'));
  assert.equal(requests[0].fetchPriority, 'low');
  run('state.index = 500; preloadNearbyImages()');
  requests[0].onload();
  assert.equal(requests[1].url, run('primaryImageUrl(currentImage())'));
  for (let i = 1; i < 11; i++) requests[i].onload();
  assert.equal(requests.length, 11);
  assert.equal(run('imagePreloads.size'), 10);
  run('preloadNearbyImages()');
  assert.equal(requests.length, 11);
});

test('cache revision changes URLs and failed preloads can be retried', () => {
  const {requests, run} = setup();
  run('state.images = state.images.slice(0, 1); preloadNearbyImages()');
  requests[0].onerror();
  requests[1].onload();
  run('preloadNearbyImages()');
  assert.equal(requests.length, 3);
  requests[2].onload();
  const prior = run('primaryImageUrl(currentImage())');
  run("state.summary.image_cache_token = 'session-1'; preloadNearbyImages()");
  assert.notEqual(requests[3].url, prior);
  assert.equal(run('imagePreloads.size'), 1);
});
