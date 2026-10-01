const state = {
  objects: [],
  batches: [],
  currentBatch: null,
  summary: null,
  filteredObjectCount: null,
  selectedObject: null,
  images: [],
  index: 0,
  playing: false,
  direction: 1,
  timer: null,
  blinkTimer: null,
  blinkEnabled: false,
  blinkImageId: null,
  blinkOn: false,
  selectedTags: new Set(),
  pendingScoreSaves: new Map(),
  flushTimer: null,
  flushing: false,
  lastDbWriteAt: 0,
  revealEnabled: false,
  revealScores: new Map(),
  revealObjectId: null,
  revealRequestToken: 0,
  revealLoading: false,
  imageRequestToken: 0,
  comparisonDefaultsApplied: false,
};

const SAVE_FLUSH_INTERVAL_MS = 5000;
let tags = [
  { value: "trail", label: "trail" },
  { value: "field guide", label: "field guide", shortcut: "f" },
  { value: "off-center", label: "off-center", shortcut: "o" },
  {
    value: "binary",
    label: "binary",
    shortcut: "B",
    ariaShortcut: "Shift+B",
  },
  { value: "crowded", label: "crowded", shortcut: "c" },
  { value: "artifact", label: "artifact", shortcut: "a" },
  { value: "psf/focus", label: "PSF/focus", shortcut: "p" },
  { value: "galaxy", label: "galaxy", shortcut: "g" },
  { value: "diffraction", label: "diffraction", shortcut: "d" },
  { value: "blobs", label: "blobs", shortcut: "b" },
  { value: "saturated", label: "saturated" },
  { value: "uncertain", label: "uncertain" },
];
const $ = (id) => document.getElementById(id);

function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.classList.add("show");
  window.clearTimeout(node._timer);
  node._timer = window.setTimeout(() => node.classList.remove("show"), 2600);
}

function setSaveStatus(message, stateName = "") {
  const node = $("saveStatus");
  node.textContent = message;
  node.className = `save-status ${stateName}`.trim();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

function queryFromFilters() {
  const params = new URLSearchParams();
  const pairs = [
    ["q", $("searchInput").value],
    ["product", $("productSelect").value],
    ["class", $("classInput").value],
    ["min_pct_peri", $("minPctPeriInput").value],
    ["max_delta_mag", $("maxDeltaMagInput").value],
    ["max_tj", $("maxTjInput").value],
    ["state", $("stateSelect").value],
    ["score_state", $("scoreStateSelect").value],
    ["preset", $("presetSelect").value],
    ["min_images", $("minImagesInput").value],
    ["batch", state.currentBatch],
  ];
  for (const [key, value] of pairs) {
    if (value) params.set(key, value);
  }
  params.set("include_missing_delta_mag", $("includeMissingDeltaMagInput").checked ? "1" : "0");
  params.set("include_missing_tj", $("includeMissingTjInput").checked ? "1" : "0");
  if ($("unscoredFirstInput").checked) params.set("unscored_first", "1");
  if ($("reverseSortInput").checked) params.set("reverse_sort", "1");
  if ($("revealMissingDetailsInput").checked) params.set("reveal_missing_details", "1");
  params.set("limit", "300");
  return params.toString();
}

async function loadSummary() {
  state.summary = await api("/api/summary");
  if (state.summary.tags) tags = state.summary.tags.map(value => ({value, label: value}));
  buildScoreButtons();
  buildTagButtons();
  configureLayout();
  renderSummary();
}

function configureLayout() {
  const activeAsteroids = state.summary?.layout === "active-asteroids";
  document.body.classList.toggle("manifest-layout", state.summary?.layout === "manifest");
  document.body.classList.toggle("active-asteroids-layout", activeAsteroids);
  $("batchPanel").hidden = !activeAsteroids;
  $("brandTitle").textContent = activeAsteroids ? "Active Asteroids Review" : "Astro Stampede";
  document.title = activeAsteroids ? "Active Asteroids Review" : "Astro Stampede";
  if (activeAsteroids) $("minImagesInput").value = "1";
  if (state.summary?.comparison_mode) {
    $("brandTitle").textContent = "Astro Stampede Comparisons";
    document.title = "Astro Stampede Comparisons";
    if (!state.comparisonDefaultsApplied) {
      $("minImagesInput").value = "1";
      $("scaleSelect").value = "fit";
      state.comparisonDefaultsApplied = true;
    }
  }
  $("revealBtn").hidden = !state.summary?.reveal_available;
  $("revealOnlyControl").hidden = !state.summary?.reveal_available;
  $("revealContextControl").hidden = !state.summary?.reveal_available;
  $("revealMissingDetailsControl").hidden = !state.summary?.reveal_available;
  $("brandTitle").textContent = state.summary?.title || "Astro Stampede";
  document.title = state.summary?.title || "Astro Stampede";
  if (state.summary?.layout === "manifest") $("minImagesInput").value = "1";
  renderReveal();
}

function renderSummary() {
  const summary = state.summary;
  if (!summary) return;
  const lines = [
    `${summary.objects.toLocaleString()} objects`,
    `${summary.images.toLocaleString()} PNGs`,
  ];
  if (state.filteredObjectCount !== null) {
    lines.push(`${state.filteredObjectCount.toLocaleString()} after filters`);
    if (state.filteredObjectCount > state.objects.length) {
      lines.push(`Showing first ${state.objects.length.toLocaleString()}`);
    }
  }
  lines.push(
    `${summary.scored.toLocaleString()} scored`,
    `${summary.active_objects.toLocaleString()} active`,
  );
  if (summary.reveal_available) {
    lines.push(
      `${summary.reveal_matched_images.toLocaleString()} reveal-scored`,
      `${summary.reveal_without_notes_or_tags.toLocaleString()} without note/features`,
    );
  }
  lines.push(`Reviewer: ${summary.reviewer}`);
  $("summary").innerHTML = [
    ...lines,
  ].join("<br>");
}

function renderBatches() {
  const select = $("batchSelect");
  select.replaceChildren();
  for (const batch of state.batches) {
    const option = document.createElement("option");
    option.value = String(batch.batch_number);
    option.textContent = `${batch.label} · ${batch.completed_objects}/${batch.objects} objects · ${batch.scored_images}/${batch.images} images`;
    option.selected = Number(batch.batch_number) === Number(state.currentBatch);
    select.append(option);
  }
  const current = state.batches.find(
    (batch) => Number(batch.batch_number) === Number(state.currentBatch),
  );
  $("batchProgress").textContent = current
    ? `${current.completed_objects}/${current.objects} objects complete; ${current.scored_images}/${current.images} images classified`
    : "";
}

async function loadBatches() {
  if (state.summary?.layout !== "active-asteroids") {
    state.batches = [];
    state.currentBatch = null;
    return;
  }
  state.batches = await api("/api/batches");
  if (
    state.currentBatch === null
    || !state.batches.some((batch) => Number(batch.batch_number) === Number(state.currentBatch))
  ) {
    const firstIncomplete = state.batches.find((batch) => !batch.complete);
    state.currentBatch = Number(
      (firstIncomplete || state.batches[0] || { batch_number: 1 }).batch_number,
    );
  }
  renderBatches();
}

async function loadObjects(options = {}) {
  const query = queryFromFilters();
  const [objects, count] = await Promise.all([
    api(`/api/objects?${query}`),
    api(`/api/objects/count?${query}`),
  ]);
  state.objects = objects;
  state.filteredObjectCount = count.objects;
  renderSummary();
  renderObjects();
  if (options.selectIncomplete && state.objects.length) {
    const next = state.objects.find(
      (object) => Number(object.scored_count || 0) < Number(object.image_count || 0),
    ) || state.objects[0];
    await selectObject(next);
  }
}

function renderObjects() {
  const list = $("objectList");
  list.replaceChildren();
  for (const object of state.objects) {
    const row = document.createElement("button");
    row.className = "object-row";
    if (state.selectedObject?.object_id === object.object_id) row.classList.add("selected");
    row.addEventListener("click", () => selectObject(object));

    const label = document.createElement("div");
    label.innerHTML = `
      <div class="object-name">${escapeHtml(object.object_name)}</div>
      <div class="object-detail">${escapeHtml(object.product)} / ${escapeHtml(object.class_name)}</div>
      ${object.queue_rank ? `<div class="object-detail">Queue #${object.queue_rank}</div>` : ""}
      <div class="object-detail">${object.scored_count || 0}/${object.image_count || 0} scored</div>
    `;
    const badge = document.createElement("div");
    const active = object.reviewer_active_flag === 1 || object.active_flag === 1;
    badge.className = `badge${active ? " active" : ""}`;
    badge.textContent = active ? "active" : `${object.image_count || 0} img`;
    row.append(label, badge);
    list.append(row);
  }
  renderActiveButton();
}

function renderActiveButton() {
  const button = $("activeBtn");
  const active = state.selectedObject?.reviewer_active_flag === 1;
  button.textContent = active ? "Unmark active" : "Active";
  button.title = active ? "Unmark object active" : "Mark object active";
  button.setAttribute("aria-pressed", active ? "true" : "false");
}

function renderReveal() {
  const button = $("revealBtn");
  const indicator = $("revealIndicator");
  const available = Boolean(state.summary?.reveal_available);
  button.hidden = !available;
  button.disabled = !state.selectedObject;
  button.textContent = state.revealEnabled ? "Hide reveal" : "Reveal";
  button.title = state.revealEnabled
    ? "Hide AI scores for the current object"
    : "Reveal AI scores for the current object";
  button.setAttribute("aria-pressed", state.revealEnabled ? "true" : "false");
  button.classList.toggle("revealed", state.revealEnabled);

  if (!available || !state.revealEnabled) {
    indicator.hidden = true;
    indicator.textContent = "";
    return;
  }

  indicator.hidden = false;
  if (state.revealLoading) {
    indicator.textContent = "Reveal on · Loading AI scores…";
    return;
  }
  const image = currentImage();
  const score = image ? state.revealScores.get(image.image_id) : null;
  indicator.textContent = score
    ? `Reveal on · R3 ${score.model_score_r3_percent}% · Operational ${score.model_score_operational_percent}%`
    : "Reveal on · No model score for this image";
}

function resetReveal() {
  state.revealRequestToken += 1;
  state.revealEnabled = false;
  state.revealScores = new Map();
  state.revealObjectId = null;
  state.revealLoading = false;
  renderReveal();
}

async function toggleReveal() {
  if (!state.summary?.reveal_available || !state.selectedObject) return;
  if (state.revealEnabled) {
    resetReveal();
    return;
  }

  const objectId = state.selectedObject.object_id;
  const requestToken = state.revealRequestToken + 1;
  state.revealRequestToken = requestToken;
  state.revealEnabled = true;
  state.revealObjectId = objectId;
  state.revealScores = new Map();
  state.revealLoading = true;
  renderReveal();

  try {
    const payload = await api(`/api/objects/${encodeURIComponent(objectId)}/reveal`);
    if (
      state.revealRequestToken !== requestToken
      || !state.revealEnabled
      || state.revealObjectId !== objectId
      || state.selectedObject?.object_id !== objectId
    ) return;
    state.revealScores = new Map(
      (payload.images || []).map((score) => [score.image_id, score]),
    );
    state.revealLoading = false;
    renderReveal();
  } catch (error) {
    if (state.revealRequestToken === requestToken) resetReveal();
    throw error;
  }
}

function objectImagesPath(objectId) {
  const suffix = $("revealContextInput").checked
    ? "?reveal_context=1"
    : ($("revealOnlyInput").checked ? "?reveal_only=1" : "");
  return `/api/objects/${encodeURIComponent(objectId)}/images${suffix}`;
}

async function fetchObjectImages(objectId) {
  const images = await api(objectImagesPath(objectId));
  applyPendingScores(images);
  return images;
}

async function applyRevealOnlyFilter() {
  if (!state.selectedObject) return;
  stopPlayback();
  pauseBlink();
  const objectId = state.selectedObject.object_id;
  const requestToken = state.imageRequestToken + 1;
  state.imageRequestToken = requestToken;
  const previousImageId = currentImage()?.image_id;
  const images = await fetchObjectImages(objectId);
  if (
    state.imageRequestToken !== requestToken
    || state.selectedObject?.object_id !== objectId
  ) return;
  state.images = images;
  const preservedIndex = previousImageId
    ? state.images.findIndex((image) => image.image_id === previousImageId)
    : -1;
  state.index = preservedIndex >= 0 ? preservedIndex : firstUnscoredIndex(state.images);
  state.direction = 1;
  renderCurrent();
}

async function selectObject(object) {
  stopPlayback();
  pauseBlink();
  if (state.selectedObject?.object_id !== object.object_id) resetReveal();
  const requestToken = state.imageRequestToken + 1;
  state.imageRequestToken = requestToken;
  state.selectedObject = object;
  const images = await fetchObjectImages(object.object_id);
  if (
    state.imageRequestToken !== requestToken
    || state.selectedObject?.object_id !== object.object_id
  ) return;
  state.images = images;
  state.index = firstUnscoredIndex(state.images);
  state.direction = 1;
  state.selectedTags.clear();
  $("commentInput").value = "";
  renderObjects();
  renderCurrent();
}

function firstUnscoredIndex(images) {
  const found = images.findIndex((image) => !image.score_status);
  return found >= 0 ? found : 0;
}

function currentImage() {
  return state.images[state.index] || null;
}

function filenameOnly(value) {
  return String(value || "").split(/[\\/]/).pop();
}

function blinkFilename(image) {
  if (image?.comparison) return image.comparison.filename;
  const pair = image?.pairs?.[0];
  return pair?.filename || pair?.relative_path;
}

function applySavedPayload(image, payload, result = {}) {
  Object.assign(image, {
    score: payload.score,
    score_status: payload.status,
    score_tags: payload.tags,
    score_comment: payload.comment,
    scored_at: result.scored_at || image.scored_at || new Date().toISOString(),
  });
}

function applyClearedPayload(image) {
  Object.assign(image, {
    score: null,
    score_status: null,
    score_tags: "",
    score_comment: "",
    scored_at: null,
  });
}

function applyPendingScores(images) {
  for (const image of images) {
    const pending = state.pendingScoreSaves.get(image.image_id);
    if (pending) applySavedPayload(image, pending);
  }
}

function renderCurrent() {
  const object = state.selectedObject;
  const image = currentImage();
  $("objectTitle").textContent = object ? object.object_name : "Choose an object";
  $("objectSubtitle").textContent = object
    ? `${object.product} / ${object.class_name}`
    : "Select an object from the queue to review its PNG cutouts.";
  $("imageCounter").textContent = state.images.length ? `${state.index + 1} / ${state.images.length}` : "0 / 0";
  $("blinkBtn").disabled = !state.blinkEnabled && !blinkSource(image);
  renderComparison(image);

  if (!image) {
    $("mainImage").removeAttribute("src");
    renderTrailOverlays();
    $("primaryFilename").textContent = "";
    $("metadata").replaceChildren();
    syncReviewControls(null);
    renderReveal();
    return;
  }
  syncBlink(image);
  const primarySrc = `/api/images/${encodeURIComponent(image.image_id)}`;
  setImageSrc(state.blinkEnabled && state.blinkOn ? blinkSource(image) : primarySrc);
  $("primaryFilename").textContent = filenameOnly(
    state.blinkEnabled && state.blinkOn ? blinkFilename(image) : image.filename,
  );
  if (image.comparison) {
    $("primaryCaption").textContent = state.blinkOn
      ? "Comparison · scores apply to original"
      : "Original · scoring this image";
  }
  renderMetadata(image);
  syncReviewControls(image);
  renderReveal();
}

function blinkSource(image) {
  if (image?.comparison) return image.comparison.url || null;
  const pair = image?.pairs?.[0];
  return pair ? `/api/images/${encodeURIComponent(pair.image_id)}` : null;
}

function renderComparison(image) {
  $("comparisonTrailOverlay").hidden = true;
  const comparison = image?.comparison;
  const visible = Boolean(comparison) && !state.blinkEnabled;
  $("comparisonPanel").hidden = !visible;
  $("imageStage").classList.toggle("comparing", visible);
  $("primaryCaption").hidden = !comparison;
  $("primaryCaption").textContent = "Original · scoring this image";
  const img = $("comparisonImage");
  img.hidden = !comparison?.url;
  $("comparisonFilename").textContent = comparison?.url
    ? filenameOnly(comparison.filename)
    : "";
  if (!comparison?.url) {
    img.removeAttribute("src");
    $("comparisonCaption").textContent = "Comparison";
    $("comparisonStatus").textContent = comparison?.status || "";
    return;
  }
  $("comparisonCaption").textContent = [
    "Comparison", comparison.band && `${comparison.band} band`, comparison.visit,
  ].filter(Boolean).join(" · ");
  $("comparisonCaption").title = comparison.filename;
  $("comparisonStatus").textContent = "";
  img.onload = applyDisplay;
  img.onerror = () => {
    $("comparisonTrailOverlay").hidden = true;
    if (currentImage()?.comparison?.url !== comparison.url) return;
    stopBlink();
    $("comparisonPanel").hidden = false;
    $("comparisonStatus").textContent = "Comparison image could not be loaded. Rescan to refresh.";
    $("blinkBtn").disabled = true;
    setImageSrc(`/api/images/${encodeURIComponent(image.image_id)}`);
  };
  if (!img.src.endsWith(comparison.url)) img.src = comparison.url;
}

function setImageSrc(src) {
  const img = $("mainImage");
  $("mainTrailOverlay").hidden = true;
  if (img.src.endsWith(src)) {
    applyDisplay();
    return;
  }
  img.onload = applyDisplay;
  img.onerror = renderTrailOverlays;
  img.src = src;
  renderTrailOverlays();
}

function renderTrailOverlays() {
  const image = currentImage();
  const enabled = $("trailOverlayInput").checked;
  const displayed = displayedTrailImage(image, state.blinkEnabled && state.blinkOn);
  const primaryStatus = drawTrailBar($("mainTrailOverlay"), $("mainImage"), displayed, enabled);
  const comparisonStatus = drawTrailBar($("comparisonTrailOverlay"), $("comparisonImage"), image?.comparison,
    enabled && !$("comparisonPanel").hidden);
  $("trailOverlayStatus").textContent = !image ? "" : $("comparisonPanel").hidden
    ? primaryStatus : `Original: ${primaryStatus}. Comparison: ${comparisonStatus}.`;
}

function applyDisplay() {
  const primary = $("mainImage");
  const comparison = $("comparisonImage");
  const width = primary.naturalWidth + (
    currentImage()?.comparison?.url ? comparison.naturalWidth : 0
  );
  const scale = $("scaleSelect").value === "fit"
    ? Math.min(1, Math.max(1, $("imageStage").clientWidth - 66) / Math.max(1, width))
    : Number($("scaleSelect").value);
  const invert = $("invertInput").checked ? "invert(1)" : "invert(0)";
  const brightness = `brightness(${$("brightnessInput").value}%)`;
  const contrast = `contrast(${$("contrastInput").value}%)`;
  for (const img of [primary, comparison]) {
    if (img.naturalWidth && img.naturalHeight) {
      img.style.width = `${Math.round(img.naturalWidth * scale)}px`;
      img.style.height = `${Math.round(img.naturalHeight * scale)}px`;
      img.closest(".image-panel").style.width = img.style.width;
    }
    img.style.filter = `${invert} ${brightness} ${contrast}`;
  }
  renderTrailOverlays();
}

function renderMetadata(image) {
  $("metadata").hidden = state.summary?.show_metadata === false;
  const fields = [
    ["Score", image.score_status ? `${image.score_status}${image.score !== null ? ` ${image.score}` : ""}` : "unscored"],
    ["Product", image.product],
    ["Class", image.class_name],
    ["Object", image.object_name],
    ["Time", image.timestamp],
    ["Visit", image.visit],
    ["Detector", image.detector],
    ["Filter", image.filter_token],
    ["Product detail", image.annotation],
    ["Delta mag", image.delta_mag_token],
    ["q", image.q_token],
    ["TJ", image.tisserand_token],
    ["Size", `${image.width} x ${image.height}`],
    ["Path", image.relative_path],
    ["Pairs", image.pairs?.length ? image.pairs.map((p) => p.product).join(", ") : ""],
  ];
  const dl = $("metadata");
  dl.replaceChildren();
  for (const [key, value] of fields) {
    if (value === null || value === undefined || value === "") continue;
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value;
    dl.append(dt, dd);
  }
}

function parseTags(value) {
  return String(value || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function scoreLabel(image) {
  if (!image || !image.score_status) return "Unscored";
  if (image.score_status === "scored") return `Saved score: ${image.score}`;
  if (image.score_status === "bad") return "Marked bad/artifact";
  if (image.score_status === "skipped") return "Skipped";
  return image.score_status;
}

function syncReviewControls(image) {
  state.selectedTags = new Set(parseTags(image?.score_tags));
  $("commentInput").value = image?.score_comment || "";

  const current = $("currentScore");
  current.textContent = scoreLabel(image);
  current.className = "current-score";
  if (image?.score_status) current.classList.add(image.score_status);

  for (const button of $("scoreGrid").querySelectorAll("button")) {
    button.classList.toggle(
      "selected",
      image?.score_status === "scored" && button.dataset.score === String(image.score),
    );
  }
  for (const button of $("tagGrid").querySelectorAll("button")) {
    button.classList.toggle("selected", state.selectedTags.has(button.dataset.tag));
  }
}

function move(delta) {
  if (!state.images.length) return;
  state.index += delta;
  if (state.index >= state.images.length || state.index < 0) {
    if ($("pingPongInput").checked) {
      state.direction *= -1;
      state.index = Math.max(0, Math.min(state.images.length - 1, state.index + 2 * state.direction));
    } else if ($("loopInput").checked) {
      state.index = (state.index + state.images.length) % state.images.length;
    } else {
      state.index = Math.max(0, Math.min(state.images.length - 1, state.index));
      stopPlayback();
    }
  }
  renderCurrent();
}

function startPlayback() {
  if (!state.images.length) return;
  stopPlayback();
  state.playing = true;
  $("playBtn").textContent = "Pause";
  const tick = () => move(state.direction);
  state.timer = window.setInterval(tick, 1000 / Number($("rateInput").value));
}

function stopPlayback() {
  state.playing = false;
  $("playBtn").textContent = "Play";
  if (state.timer) window.clearInterval(state.timer);
  state.timer = null;
}

function togglePlayback() {
  state.playing ? stopPlayback() : startPlayback();
}

function pauseBlink() {
  if (state.blinkTimer) window.clearInterval(state.blinkTimer);
  state.blinkTimer = null;
  state.blinkImageId = null;
  state.blinkOn = false;
}

function stopBlink() {
  pauseBlink();
  state.blinkEnabled = false;
  $("blinkBtn").textContent = "Blink pair";
  $("blinkBtn").setAttribute("aria-pressed", "false");
}

function syncBlink(image) {
  const button = $("blinkBtn");
  button.textContent = state.blinkEnabled
    ? (image?.comparison ? "Side by side" : "Stop blink")
    : "Blink pair";
  button.setAttribute("aria-pressed", String(state.blinkEnabled));
  if (!state.blinkEnabled) return;

  const comparisonSrc = blinkSource(image);
  if (!comparisonSrc) {
    if (state.blinkTimer) window.clearInterval(state.blinkTimer);
    state.blinkTimer = null;
    state.blinkImageId = null;
    state.blinkOn = false;
    return;
  }
  if (state.blinkImageId === image.image_id && state.blinkTimer) return;

  if (state.blinkTimer) window.clearInterval(state.blinkTimer);
  state.blinkOn = false;
  state.blinkImageId = image.image_id;
  const primarySrc = `/api/images/${encodeURIComponent(image.image_id)}`;
  state.blinkTimer = window.setInterval(() => {
    if (currentImage()?.image_id !== image.image_id) return;
    state.blinkOn = !state.blinkOn;
    setImageSrc(state.blinkOn ? comparisonSrc : primarySrc);
    if (image.comparison) {
      $("primaryCaption").textContent = state.blinkOn
        ? "Comparison · scores apply to original"
        : "Original · scoring this image";
    }
    $("primaryFilename").textContent = filenameOnly(
      state.blinkOn ? blinkFilename(image) : image.filename,
    );
  }, 400);
}

function toggleBlink() {
  const image = currentImage();
  if (state.blinkEnabled) {
    stopBlink();
    renderCurrent();
    return;
  }
  if (!blinkSource(image)) return;
  stopPlayback();
  state.blinkEnabled = true;
  renderCurrent();
}

async function toggleActive() {
  if (!state.selectedObject) return;
  const active = state.selectedObject.reviewer_active_flag !== 1;
  await api("/api/object-review", {
    method: "POST",
    body: JSON.stringify({
      object_id: state.selectedObject.object_id,
      active_flag: active,
      status: active ? "active" : "reviewed",
    }),
  });
  state.selectedObject.reviewer_active_flag = active ? 1 : 0;
  state.selectedObject.active_flag = active ? 1 : 0;
  state.selectedObject.reviewer_status = active ? "active" : "reviewed";
  toast(active ? "Object marked active" : "Object unmarked active");
  if (state.summary) {
    state.summary.active_objects = Math.max(
      0,
      (state.summary.active_objects || 0) + (active ? 1 : -1),
    );
    renderSummary();
  }
  renderObjects();
}

function selectedTagsText() {
  return Array.from(state.selectedTags).join(",");
}

async function saveCurrentReviewEdits(options = {}) {
  const image = currentImage();
  if (!image?.score_status) return false;
  const payload = {
    image_id: image.image_id,
    score: image.score_status === "scored" ? image.score : null,
    status: image.score_status,
    tags: selectedTagsText(),
    comment: $("commentInput").value,
  };
  applySavedPayload(image, payload);
  queueScoreSave(payload);
  renderMetadata(image);
  if (options.toast !== false) toast(options.message || "Queued tag update");
  return true;
}

async function scoreCurrent(score, status = "scored") {
  const image = currentImage();
  if (!image) return;
  if (score !== null && (score < (state.summary?.score_min ?? 0) || score > (state.summary?.score_max ?? 9))) return;
  const wasReviewed = Boolean(image.score_status);
  const payload = {
    image_id: image.image_id,
    score,
    status,
    tags: selectedTagsText(),
    comment: $("commentInput").value,
  };
  applySavedPayload(image, payload);
  queueScoreSave(payload);
  syncReviewControls(image);
  renderMetadata(image);
  toast(payload.score === null ? payload.status : `Score ${payload.score}`);
  if (!wasReviewed && state.selectedObject) {
    state.selectedObject.scored_count = (state.selectedObject.scored_count || 0) + 1;
    renderObjects();
  }
  await advanceAfterClassification();
}

async function advanceAfterClassification() {
  const nextImageIndex = state.images.findIndex(
    (image, index) => index > state.index && !image.score_status,
  );
  if (nextImageIndex >= 0) {
    state.index = nextImageIndex;
    renderCurrent();
    return;
  }
  const wrappedImageIndex = state.images.findIndex((image) => !image.score_status);
  if (wrappedImageIndex >= 0) {
    state.index = wrappedImageIndex;
    renderCurrent();
    return;
  }

  if ($("loopInput").checked) {
    move(state.direction);
    return;
  }

  const selectedIndex = state.objects.findIndex(
    (object) => object.object_id === state.selectedObject?.object_id,
  );
  const orderedCandidates = [
    ...state.objects.slice(selectedIndex + 1),
    ...state.objects.slice(0, Math.max(0, selectedIndex)),
  ];
  const nextObject = orderedCandidates.find(
    (object) => Number(object.scored_count || 0) < Number(object.image_count || 0),
  );
  if (nextObject) {
    await selectObject(nextObject);
    return;
  }
  if (state.summary?.layout !== "active-asteroids") {
    return;
  }

  await flushPendingSaves();
  await loadBatches();
  const nextBatch = state.batches.find(
    (batch) => Number(batch.batch_number) > Number(state.currentBatch) && !batch.complete,
  );
  if (!nextBatch) {
    toast("All Active Asteroids batches are complete");
    return;
  }
  state.currentBatch = Number(nextBatch.batch_number);
  renderBatches();
  state.selectedObject = null;
  state.images = [];
  state.index = 0;
  await loadObjects({ selectIncomplete: true });
  toast(`Continuing with ${nextBatch.label}`);
}

async function undoScore() {
  await flushPendingSaves();
  const result = await api("/api/undo-score", { method: "POST", body: "{}" });
  if (!result.undone) {
    toast("Nothing to undo");
    return;
  }
  toast("Last score removed");
  if (state.selectedObject?.object_id === result.object_id) {
    const previousImageId = currentImage()?.image_id;
    state.images = await fetchObjectImages(result.object_id);
    const index = state.images.findIndex((image) => image.image_id === result.image_id);
    const preservedIndex = previousImageId
      ? state.images.findIndex((image) => image.image_id === previousImageId)
      : -1;
    state.index = index >= 0
      ? index
      : (preservedIndex >= 0 ? preservedIndex : firstUnscoredIndex(state.images));
    renderCurrent();
  }
  await loadSummary();
  await loadBatches();
  await loadObjects();
}

async function clearCurrentScore() {
  const image = currentImage();
  if (!image) return;
  const wasReviewed = Boolean(image.score_status || state.pendingScoreSaves.has(image.image_id));
  state.pendingScoreSaves.delete(image.image_id);
  setSaveStatus(
    state.pendingScoreSaves.size ? `${state.pendingScoreSaves.size} queued` : "Saved",
    state.pendingScoreSaves.size ? "queued" : "saved",
  );
  await api("/api/image-score/clear", {
    method: "POST",
    body: JSON.stringify({ image_id: image.image_id }),
  });
  applyClearedPayload(image);
  state.selectedTags.clear();
  $("commentInput").value = "";
  if (wasReviewed && state.selectedObject) {
    state.selectedObject.scored_count = Math.max(0, (state.selectedObject.scored_count || 0) - 1);
  }
  syncReviewControls(image);
  renderMetadata(image);
  renderObjects();
  await loadSummary();
  await loadBatches();
  await loadObjects();
  toast("Cleared classification");
}

async function rescan() {
  await flushPendingSaves();
  toast("Rescanning PNG tree...");
  const result = await api("/api/rescan", { method: "POST", body: "{}" });
  toast(
    state.summary?.layout === "active-asteroids"
      ? `Indexed ${result.records} PNGs in ${result.elapsed_seconds}s`
      : `Indexed ${result.records} source PNGs as ${result.review_products} review products in ${result.elapsed_seconds}s; ${result.duplicate_groups} duplicate groups, ${result.duplicates_suppressed} suppressed; TJ filled ${result.tisserand_filled || 0}`,
  );
  await loadSummary();
  await loadBatches();
  await loadObjects();
}

async function exportLabels() {
  await flushPendingSaves();
  const result = await api("/api/export", { method: "POST", body: "{}" });
  toast(`Exported ${result.classification_parquet}`);
}

async function shutdownReviewTool() {
  if (!window.confirm("Save the classification Parquet and stop the review tool?")) return;
  stopPlayback();
  stopBlink();
  while (state.flushing) {
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
  await flushPendingSaves();
  const result = await api("/api/shutdown", { method: "POST", body: "{}" });
  const reviewName = state.summary?.layout === "active-asteroids"
    ? "Active Asteroids Review"
    : "Astro Stampede";
  document.body.innerHTML = `
    <main class="shutdown-message">
      <h1>${reviewName} stopped</h1>
      <p>Classifications were saved to:</p>
      <code>${escapeHtml(result.parquet)}</code>
      <p>You can close this tab.</p>
    </main>
  `;
}

function queueScoreSave(payload) {
  state.pendingScoreSaves.set(payload.image_id, payload);
  setSaveStatus(`${state.pendingScoreSaves.size} queued`, "queued");
  const elapsed = Date.now() - state.lastDbWriteAt;
  if (elapsed >= SAVE_FLUSH_INTERVAL_MS) {
    flushPendingSaves().catch((error) => toast(error.message));
  } else {
    scheduleSaveFlush(SAVE_FLUSH_INTERVAL_MS - elapsed);
  }
}

function scheduleSaveFlush(delayMs = SAVE_FLUSH_INTERVAL_MS) {
  if (state.flushTimer) return;
  state.flushTimer = window.setTimeout(() => {
    state.flushTimer = null;
    flushPendingSaves().catch((error) => toast(error.message));
  }, delayMs);
}

async function flushPendingSaves(options = {}) {
  if (state.flushing || state.pendingScoreSaves.size === 0) return;
  state.flushing = true;
  setSaveStatus(`Saving ${state.pendingScoreSaves.size}...`, "saving");
  if (state.flushTimer) {
    window.clearTimeout(state.flushTimer);
    state.flushTimer = null;
  }
  const entries = Array.from(state.pendingScoreSaves.entries());
  try {
    const payload = { scores: entries.map(([, scorePayload]) => scorePayload) };
    const result = await saveScoreBatch(payload, options);
    state.lastDbWriteAt = Date.now();
    for (const [imageId, scorePayload] of entries) {
      if (state.pendingScoreSaves.get(imageId) === scorePayload) {
        state.pendingScoreSaves.delete(imageId);
      }
    }
    for (const saved of result.results || []) {
      const image = state.images.find((candidate) => candidate.image_id === saved.image_id);
      if (image) image.scored_at = saved.scored_at;
    }
    if (!options.keepalive) {
      await loadSummary();
      await loadBatches();
      await loadObjects();
    }
    setSaveStatus(
      state.pendingScoreSaves.size ? `${state.pendingScoreSaves.size} queued` : "Saved",
      state.pendingScoreSaves.size ? "queued" : "saved",
    );
  } catch (error) {
    setSaveStatus("Save failed", "error");
    throw error;
  } finally {
    state.flushing = false;
    if (state.pendingScoreSaves.size) scheduleSaveFlush();
  }
}

async function saveScoreBatch(payload, options = {}) {
  try {
    return await api("/api/image-scores", {
      method: "POST",
      body: JSON.stringify(payload),
      keepalive: Boolean(options.keepalive),
    });
  } catch (error) {
    if (options.keepalive) throw error;
    const results = [];
    for (const scorePayload of payload.scores) {
      results.push(
        await api("/api/image-score", {
          method: "POST",
          body: JSON.stringify(scorePayload),
        }),
      );
    }
    toast("Used single-save fallback; restart astro-stampede soon");
    return { results };
  }
}

function buildScoreButtons() {
  const grid = $("scoreGrid");
  const image = currentImage();
  grid.replaceChildren();
  for (let score = (state.summary?.score_min ?? 0); score <= (state.summary?.score_max ?? 9); score += 1) {
    const button = document.createElement("button");
    button.dataset.score = String(score);
    button.classList.toggle("selected", image?.score_status === "scored" && image.score === score);
    button.textContent = String(score);
    button.title = `Score ${score}`;
    button.addEventListener("click", () => scoreCurrent(score));
    grid.append(button);
  }
}

function buildTagButtons() {
  const grid = $("tagGrid");
  grid.replaceChildren();
  for (const tag of tags) {
    const button = document.createElement("button");
    button.className = "tag";
    button.dataset.tag = tag.value;
    button.classList.toggle("selected", state.selectedTags.has(tag.value));
    button.textContent = tag.label;
    if (tag.shortcut) {
      button.title = `${tag.label} (shortcut: ${tag.shortcut})`;
      button.setAttribute("aria-keyshortcuts", tag.ariaShortcut || tag.shortcut);
    }
    button.addEventListener(
      "click",
      () => toggleTag(tag.value).catch((error) => toast(error.message)),
    );
    grid.append(button);
  }
}

async function toggleTag(tag) {
  const button = $("tagGrid").querySelector(`[data-tag="${CSS.escape(tag)}"]`);
  if (state.selectedTags.has(tag)) {
    state.selectedTags.delete(tag);
    button?.classList.remove("selected");
  } else {
    state.selectedTags.add(tag);
    button?.classList.add("selected");
  }
  await saveCurrentReviewEdits();
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function wireEvents() {
  const filterIds = [
    "searchInput",
    "productSelect",
    "classInput",
    "minPctPeriInput",
    "maxDeltaMagInput",
    "includeMissingDeltaMagInput",
    "maxTjInput",
    "includeMissingTjInput",
    "stateSelect",
    "scoreStateSelect",
    "revealMissingDetailsInput",
    "presetSelect",
    "minImagesInput",
    "unscoredFirstInput",
    "reverseSortInput",
  ];
  for (const id of filterIds) {
    $(id).addEventListener("input", () => loadObjects().catch((error) => toast(error.message)));
  }
  $("batchSelect").addEventListener("change", async () => {
    state.currentBatch = Number($("batchSelect").value);
    state.selectedObject = null;
    resetReveal();
    state.images = [];
    state.index = 0;
    renderBatches();
    await loadObjects({ selectIncomplete: true });
  });
  $("prevBtn").addEventListener("click", () => move(-1));
  $("nextBtn").addEventListener("click", () => move(1));
  $("playBtn").addEventListener("click", togglePlayback);
  $("activeBtn").addEventListener("click", () => toggleActive().catch((error) => toast(error.message)));
  $("revealBtn").addEventListener("click", () => toggleReveal().catch((error) => toast(error.message)));
  $("revealOnlyInput").addEventListener("change", () => {
    applyRevealOnlyFilter().catch((error) => toast(error.message));
  });
  $("revealContextInput").addEventListener("change", () => {
    applyRevealOnlyFilter().catch((error) => toast(error.message));
  });
  $("blinkBtn").addEventListener("click", toggleBlink);
  $("skipBtn").addEventListener("click", () => scoreCurrent(null, "skipped"));
  $("clearBtn").addEventListener("click", () => clearCurrentScore().catch((error) => toast(error.message)));
  $("undoBtn").addEventListener("click", () => undoScore().catch((error) => toast(error.message)));
  $("rescanBtn").addEventListener("click", () => rescan().catch((error) => toast(error.message)));
  $("exportBtn").addEventListener("click", () => exportLabels().catch((error) => toast(error.message)));
  $("shutdownBtn").addEventListener("click", () => shutdownReviewTool().catch((error) => toast(error.message)));
  $("commentInput").addEventListener("input", () => {
    saveCurrentReviewEdits({ toast: false }).catch((error) => toast(error.message));
  });
  $("commentInput").addEventListener("change", () => {
    saveCurrentReviewEdits({ toast: false })
      .then(() => flushPendingSaves())
      .catch((error) => toast(error.message));
  });
  $("scaleSelect").addEventListener("change", applyDisplay);
  $("trailOverlayInput").addEventListener("change", renderTrailOverlays);
  window.addEventListener("resize", applyDisplay);
  $("invertInput").addEventListener("change", applyDisplay);
  $("brightnessInput").addEventListener("input", applyDisplay);
  $("contrastInput").addEventListener("input", applyDisplay);
  $("rateInput").addEventListener("input", () => {
    $("rateOutput").textContent = `${$("rateInput").value} fps`;
    if (state.playing) startPlayback();
  });
  window.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (/^[0-9]$/.test(event.key)) {
      event.preventDefault();
      scoreCurrent(Number(event.key)).catch((error) => toast(error.message));
    } else if (event.key === " ") {
      event.preventDefault();
      togglePlayback();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      move(1);
    } else if (event.key.toLowerCase() === "a") {
      event.preventDefault();
      toggleTag("artifact").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "s") {
      event.preventDefault();
      scoreCurrent(null, "skipped").catch((error) => toast(error.message));
    } else if (event.key === "B") {
      event.preventDefault();
      toggleTag("binary").catch((error) => toast(error.message));
    } else if (event.key === "b") {
      event.preventDefault();
      toggleTag("blobs").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "c") {
      event.preventDefault();
      toggleTag("crowded").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "o") {
      event.preventDefault();
      toggleTag("off-center").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "f") {
      event.preventDefault();
      toggleTag("field guide").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "p") {
      event.preventDefault();
      toggleTag("psf/focus").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "g") {
      event.preventDefault();
      toggleTag("galaxy").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "d") {
      event.preventDefault();
      toggleTag("diffraction").catch((error) => toast(error.message));
    } else if (event.key.toLowerCase() === "z") {
      event.preventDefault();
      clearCurrentScore().catch((error) => toast(error.message));
    } else if (event.key === "Backspace") {
      event.preventDefault();
      undoScore().catch((error) => toast(error.message));
    }
  });
  window.addEventListener("pagehide", () => {
    flushPendingSaves({ keepalive: true }).catch(console.error);
  });
}

async function boot() {
  buildScoreButtons();
  buildTagButtons();
  wireEvents();
  await loadSummary();
  await loadBatches();
  await loadObjects({ selectIncomplete: state.summary?.layout === "active-asteroids" });
}

boot().catch((error) => toast(error.message));
