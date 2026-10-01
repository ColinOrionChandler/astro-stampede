/* Geometry is in original PNG pixels, following the RCC batch scale bar. */
function trailBarGeometry(pixels, width, height) {
  if (!Number.isFinite(pixels)) return { reason: "No expected trail length" };
  if (pixels < 3) return { reason: "Expected trail is below 3 px" };
  const right = width - 24;
  const bottom = height - 24;
  const left = right - pixels + 1;
  if (left < 24 || bottom - 7 < 0 || bottom + 7 >= height) {
    return { reason: "Expected trail does not fit this image" };
  }
  return { left, right, bottom, pixels };
}

function displayedTrailImage(image, blinking) {
  return blinking ? (image?.comparison || image?.pairs?.[0] || null) : image;
}

function drawTrailBar(canvas, img, image, enabled) {
  canvas.hidden = true;
  if (!enabled) return "Hidden";
  if (!img.getAttribute("src") || !img.complete || !img.naturalWidth) return "Image not loaded";
  const bar = trailBarGeometry(image?.expected_trail_pixels, img.naturalWidth, img.naturalHeight);
  if (bar.reason) return bar.reason;
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d");
  const leftCap = bar.left + 1;
  const rightCap = bar.right - 1;
  // Integer rectangles reproduce the batch's inclusive pixel endpoints.
  // Fractional lengths retain their scale rather than rounding or clipping.
  for (const [width, color] of [[5, "#000000"], [3, "#00FF00"]]) {
    ctx.fillStyle = color;
    const half = (width - 1) / 2;
    ctx.fillRect(leftCap, bar.bottom - half, rightCap - leftCap + 1, width);
    for (const x of [leftCap, rightCap]) {
      ctx.fillRect(x - half, bar.bottom - 7, width, 15);
    }
  }
  canvas.setAttribute("aria-label", `Expected trail length: ${bar.pixels} pixels`);
  canvas.hidden = false;
  return `${bar.pixels} px expected trail`;
}

// The same geometry and image selection are exercised by the Node tests.
if (typeof module !== "undefined") module.exports = { trailBarGeometry, displayedTrailImage, drawTrailBar };
