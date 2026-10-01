const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element() {
  const classes = new Set();
  return {
    textContent: '', value: '', checked: false, dataset: {}, children: [],
    classList: {
      add: (name) => classes.add(name),
      toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
      contains: (name) => classes.has(name),
    },
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
    querySelectorAll() { return this.children; },
    setAttribute() {},
    addEventListener() {},
  };
}

for (const score of [0, 7]) {
  test(`saved score ${score} and tags survive the background save refresh`, async () => {
    const nodes = new Map();
    const get = (id) => {
      if (!nodes.has(id)) nodes.set(id, element());
      return nodes.get(id);
    };
    const context = vm.createContext({
      document: { getElementById: get, createElement: element, body: element() },
      window: { clearTimeout() {} },
    });
    const source = fs.readFileSync(require.resolve('../astro_stampede/review_static/app.js'), 'utf8');
    vm.runInContext(source.replace('boot().catch((error) => toast(error.message));', ''), context);
    vm.runInContext(`
      api = async () => ({objects: 1, images: 1, scored: 1, active_objects: 0,
        tags: ['trail', 'artifact'], score_min: 0, score_max: 9});
      loadObjects = async () => {};
      saveScoreBatch = async () => ({results: []});
      state.images = [{image_id: 'primary', score: ${score}, score_status: 'scored',
        score_tags: 'trail', score_comment: 'saved comment'}];
      state.pendingScoreSaves.set('primary', {image_id: 'primary', score: ${score}});
      syncReviewControls(currentImage());
      // Draft edits must also survive a save completing in the background.
      state.selectedTags.add('artifact');
      $('commentInput').value = 'unfinished draft';
    `, context);
    await vm.runInContext('flushPendingSaves()', context);
    assert.ok(get('scoreGrid').children[score].classList.contains('selected'));
    assert.ok(get('tagGrid').children.every(button => button.classList.contains('selected')));
    assert.equal(get('currentScore').textContent, `Saved score: ${score}`);
    assert.equal(get('commentInput').value, 'unfinished draft');
    assert.equal(vm.runInContext('state.pendingScoreSaves.size', context), 0);
    assert.equal(vm.runInContext('state.index', context), 0);
  });
}

for (const paired of [false, true]) {
  test(`single-image blink scoring refreshes without navigation (paired=${paired})`, async () => {
    const nodes = new Map();
    const get = (id) => {
      if (!nodes.has(id)) nodes.set(id, element());
      return nodes.get(id);
    };
    for (let score = 0; score <= 9; score++) {
      const button = element();
      button.dataset.score = String(score);
      get('scoreGrid').append(button);
    }
    let tick;
    const context = vm.createContext({
      document: { getElementById: get, createElement: element },
      window: { setInterval(fn) { tick = fn; return 1; }, clearInterval() {} },
    });
    const source = fs.readFileSync(require.resolve('../astro_stampede/review_static/app.js'), 'utf8');
    vm.runInContext(source.replace('boot().catch((error) => toast(error.message));', ''), context);
    vm.runInContext(`
      queueScoreSave = (payload) => state.pendingScoreSaves.set(payload.image_id, payload);
      toast = () => {};
      setImageSrc = () => {};
      state.images = [{image_id: 'primary', filename: 'primary.png', score: null}];
      state.blinkEnabled = true;
    `, context);
    if (paired) vm.runInContext("state.images[0].comparison = {url: '/comparison.png', filename: 'comparison.png'}", context);
    vm.runInContext('syncBlink(currentImage())', context);
    if (tick) tick();
    for (const score of [0, 7]) {
      await vm.runInContext(`scoreCurrent(${score})`, context);
      if (tick) tick();
      assert.equal(get('currentScore').textContent, `Saved score: ${score}`);
      assert.ok(get('scoreGrid').children[score].classList.contains('selected'));
      assert.equal(get('metadata').children[1].textContent, `scored ${score}`);
      assert.equal(vm.runInContext("state.pendingScoreSaves.get('primary').score", context), score);
      assert.equal(vm.runInContext('state.index', context), 0);
      assert.equal(vm.runInContext('state.blinkEnabled', context), true);
    }
  });
}
