/* Local path selection. Paths are only requested when Settings is opened. */
const settingsFields = [
  ['root', 'Image folder', 'directory'],
  ['manifest', 'Manifest CSV', 'file'],
  ['dataset', 'Dataset name', 'text'],
  ['output', 'Classification output folder', 'directory'],
  ['db', 'Label database', 'file'],
  ['index_db', 'Dataset index (use a new file for a different image folder)', 'file'],
  ['classification_parquet', 'Classification snapshot', 'file'],
  ['export_dir', 'CSV and audit export folder', 'directory'],
];
let activeSettings = null;
let browseTarget = null;
let browseKind = null;
let browsePath = null;
let applyingSettings = false;
let suggestedManifest = null;
let suggestedIndex = null;
let pendingSuggestion = Promise.resolve();
function suggestSettings(action) {
  pendingSuggestion = pendingSuggestion.catch(() => {}).then(action);
  pendingSuggestion.catch(error => { $('settingsStatus').textContent = error.message; });
}
const settingsPost = (path, payload = {}) => api(`/api/settings${path}`, {method: 'POST', body: JSON.stringify(payload)});

async function settingsDefaults(output, all = false) {
  const result = await settingsPost('/defaults', {
    root: $('setting-root').value,
    dataset: $('setting-dataset').value || undefined,
    output,
  });
  for (const key of (all ? ['manifest', 'dataset', 'output', 'db', 'index_db', 'classification_parquet', 'export_dir'] : ['db', 'index_db', 'classification_parquet', 'export_dir'])) {
    $(`setting-${key}`).value = result[key];
  }
  if (all) suggestedManifest = result.manifest;
  suggestedIndex = result.index_db;
  $('settingsStatus').textContent = 'Suggested paths filled in. Apply to use them; existing labels are not moved.';
}

async function settingsRootChanged() {
  if (!activeSettings.source_editable) return;
  const result = await settingsPost('/defaults', {
    root: $('setting-root').value, dataset: $('setting-dataset').value,
    output: $('setting-output').value || activeSettings.output,
  });
  if ($('setting-manifest').value === suggestedManifest) {
    $('setting-manifest').value = result.manifest;
    suggestedManifest = result.manifest;
  }
  if ($('setting-index_db').value === suggestedIndex) {
    $('setting-index_db').value = result.index_db;
    suggestedIndex = result.index_db;
  }
}


async function openSettings() {
  stopPlayback();
  stopBlink();
  activeSettings = await settingsPost('');
  suggestedManifest = `${activeSettings.values.root.replace(/\/$/, '')}/manifest.csv`;
  suggestedIndex = activeSettings.values.index_db;
  $('settingsFields').replaceChildren();
  $('settingsStatus').textContent = '';
  $('settingsCompatibility').hidden = activeSettings.source_editable;
  $('settingsDownload').hidden = !activeSettings.document;
  $('settingsDefaults').hidden = !activeSettings.source_editable;
  for (const [key, title, kind] of settingsFields) {
    const label = document.createElement('label');
    label.htmlFor = `setting-${key}`;
    label.textContent = title;
    const row = document.createElement('div');
    row.className = 'settings-path';
    const input = document.createElement('input');
    input.id = label.htmlFor;
    input.type = 'text';
    input.value = key === 'output' ? activeSettings.output : activeSettings.values[key] || '';
    input.required = key !== 'output' && (key !== 'manifest' || activeSettings.source_editable);
    input.disabled = !activeSettings.source_editable && ['root', 'manifest', 'dataset'].includes(key);
    if (key === 'output') {
      input.placeholder = 'Optional: choose a folder to set all four output paths below';
      input.addEventListener('change', () => { if (input.value.trim()) suggestSettings(() => settingsDefaults(input.value.trim())); });
    }
    if (key === 'root') input.addEventListener('change', () => suggestSettings(settingsRootChanged));
    row.append(input);
    if (kind !== 'text') {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Browse…';
      button.disabled = input.disabled;
      button.addEventListener('click', () => {
        browseTarget = input;
        browseKind = kind;
        $('folderDialog').showModal();
        browseFolder(input.value || activeSettings.values.root);
      });
      row.append(button);
    }
    $('settingsFields').append(label, row);
  }
  $('settingsDialog').showModal();
}

async function browseFolder(path) {
  $('folderStatus').textContent = 'Loading…';
  $('folderEntries').replaceChildren();
  $('folderSelect').disabled = true;
  try {
    const result = await settingsPost('/browse', {path});
    browsePath = result.path;
    $('folderPath').value = result.path;
    $('folderUp').onclick = () => browseFolder(result.parent);
    $('folderSelect').disabled = browseKind !== 'directory';
    for (const entry of result.entries) {
      if (!entry.directory && browseKind === 'directory') continue;
      const button = document.createElement('button');
      button.textContent = `${entry.directory ? 'Folder: ' : ''}${entry.name}`;
      button.onclick = () => entry.directory ? browseFolder(entry.path) : selectSettingsPath(entry.path);
      $('folderEntries').append(button);
    }
    $('folderStatus').textContent = 'Choose an existing path, or enter a new output path in Settings.';
  } catch (error) {
    $('folderStatus').textContent = error.message;
  }
}

function selectSettingsPath(path) {
  browseTarget.value = path;
  browseTarget.dispatchEvent(new Event('change'));
  $('folderDialog').close();
}

$('settingsBtn').addEventListener('click', () => openSettings().catch(error => toast(error.message)));
$('settingsDefaults').onclick = () => suggestSettings(() => settingsDefaults(undefined, true));
$('settingsClose').onclick = () => $('settingsDialog').close();
$('folderClose').onclick = () => $('folderDialog').close();
$('folderSelect').onclick = () => selectSettingsPath(browsePath);
$('folderForm').onsubmit = event => { event.preventDefault(); browseFolder($('folderPath').value); };
$('settingsDialog').addEventListener('cancel', event => { if (applyingSettings) event.preventDefault(); });
$('settingsDownload').onclick = () => {
  const blob = new Blob([JSON.stringify(activeSettings.document, null, 2) + '\n'], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'astro-stampede-settings.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  $('settingsStatus').textContent = 'Reopen with: astro-stampede --config /path/to/astro-stampede-settings.json';
};
$('settingsForm').onsubmit = async event => {
  event.preventDefault();
  if (applyingSettings) return;
  applyingSettings = true;
  try { await pendingSuggestion; } catch { applyingSettings = false; return; }
  const payload = {};
  for (const [key] of settingsFields) {
    if (key !== 'output') payload[key] = $(`setting-${key}`).value;
  }
  const controls = [...$('settingsForm').querySelectorAll('button, input')];
  const disabled = controls.map(node => node.disabled);
  controls.forEach(node => { node.disabled = true; });
  $('settingsStatus').textContent = 'Saving pending work and validating paths…';
  try {
    while (state.flushing) await new Promise(resolve => setTimeout(resolve, 50));
    await flushPendingSaves();
    await settingsPost('/apply', payload);
    window.location.reload();
  } catch (error) {
    $('settingsStatus').textContent = error.message;
    controls.forEach((node, index) => { node.disabled = disabled[index]; });
    applyingSettings = false;
  }
};
