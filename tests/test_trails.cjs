const test = require('node:test');
const assert = require('node:assert/strict');
const { trailBarGeometry, displayedTrailImage, drawTrailBar } = require('../astro_stampede/review_static/trails.js');

test('batch geometry preserves exact source-pixel length and margins', () => {
  for (const pixels of [3, 5, 8.5, 235, 553]) {
    const bar = trailBarGeometry(pixels, 600, 600);
    assert.equal(bar.right, 576);
    assert.equal(bar.bottom, 576);
    assert.equal(bar.right - bar.left + 1, pixels);
  }
  assert.ok(trailBarGeometry(554, 600, 600).reason);
  assert.ok(trailBarGeometry(235, 160, 160).reason);
  for (const pixels of [null, undefined, NaN, Infinity, '5', -1, 0, 2]) {
    assert.ok(trailBarGeometry(pixels, 600, 600).reason);
  }
});

test('blink uses the displayed image, never the original length as a fallback', () => {
  const primary = { expected_trail_pixels: 24, comparison: { expected_trail_pixels: 8 } };
  assert.equal(displayedTrailImage(primary, false).expected_trail_pixels, 24);
  assert.equal(displayedTrailImage(primary, true).expected_trail_pixels, 8);
  primary.comparison = {};
  assert.equal(displayedTrailImage(primary, true).expected_trail_pixels, undefined);
  delete primary.comparison;
  primary.pairs = [{ expected_trail_pixels: 12 }];
  assert.equal(displayedTrailImage(primary, true).expected_trail_pixels, 12);
});

test('hidden, loading, missing and oversized images clear an earlier overlay', () => {
  const canvas = { hidden: false, getContext() { throw Error('must not draw'); } };
  const image = { getAttribute: () => '/image', complete: true, naturalWidth: 160, naturalHeight: 160 };
  for (const [enabled, complete, pixels] of [[false, true, 24], [true, false, 24], [true, true, null], [true, true, 235]]) {
    canvas.hidden = false;
    image.complete = complete;
    drawTrailBar(canvas, image, { expected_trail_pixels: pixels }, enabled);
    assert.equal(canvas.hidden, true);
  }
});
