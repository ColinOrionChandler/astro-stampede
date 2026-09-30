from pathlib import Path


STATIC_DIR = Path(__file__).parents[1] / "astro_stampede" / "review_static"


def _function_source(app_source: str, name: str, next_name: str) -> str:
    start = app_source.index(f"async function {name}()")
    end = app_source.index(f"function {next_name}()", start)
    return app_source[start:end]


def _keydown_source(app_source: str) -> str:
    start = app_source.index('window.addEventListener("keydown"')
    end = app_source.index('window.addEventListener("pagehide"', start)
    return app_source[start:end]


def _shortcut_branch(source: str, condition: str, next_condition: str) -> str:
    start = source.index(condition)
    end = source.index(next_condition, start)
    return source[start:end]


def test_active_button_toggles_current_reviewer_flag() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    index_source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    toggle_source = _function_source(app_source, "toggleActive", "selectedTagsText")

    assert "state.selectedObject.reviewer_active_flag !== 1" in toggle_source
    assert "active_flag: active" in toggle_source
    assert 'status: active ? "active" : "reviewed"' in toggle_source
    assert "state.selectedObject.reviewer_active_flag = active ? 1 : 0" in toggle_source
    assert 'toggleActive().catch((error) => toast(error.message))' in app_source
    assert 'aria-pressed="false"' in index_source


def test_a_shortcut_toggles_artifact_tag() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    shortcut_start = app_source.index('event.key.toLowerCase() === "a"')
    shortcut_end = app_source.index('event.key.toLowerCase() === "s"', shortcut_start)
    shortcut_source = app_source[shortcut_start:shortcut_end]

    assert 'toggleTag("artifact")' in shortcut_source
    assert "toggleActive" not in shortcut_source


def test_new_artifact_tags_have_labels_and_shortcut_metadata() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for value, label, shortcut in (
        ("psf/focus", "PSF/focus", "p"),
        ("galaxy", "galaxy", "g"),
        ("diffraction", "diffraction", "d"),
        ("blobs", "blobs", "b"),
    ):
        definition = f'{{ value: "{value}", label: "{label}", shortcut: "{shortcut}" }}'
        assert definition in app_source

    assert "button.dataset.tag = tag.value" in app_source
    assert "button.textContent = tag.label" in app_source
    assert 'button.title = `${tag.label} (shortcut: ${tag.shortcut})`' in app_source
    assert 'button.setAttribute("aria-keyshortcuts", tag.ariaShortcut || tag.shortcut)' in app_source


def test_new_artifact_tag_shortcuts_and_binary_case_are_distinct() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    shortcut_source = _keydown_source(app_source)

    binary_branch = _shortcut_branch(
        shortcut_source,
        'event.key === "B"',
        'event.key === "b"',
    )
    blobs_branch = _shortcut_branch(
        shortcut_source,
        'event.key === "b"',
        'event.key.toLowerCase() === "c"',
    )
    assert 'toggleTag("binary")' in binary_branch
    assert 'toggleTag("blobs")' in blobs_branch

    for key, tag, next_key in (
        ("p", "psf/focus", "g"),
        ("g", "galaxy", "d"),
        ("d", "diffraction", "z"),
    ):
        branch = _shortcut_branch(
            shortcut_source,
            f'event.key.toLowerCase() === "{key}"',
            f'event.key.toLowerCase() === "{next_key}"',
        )
        assert f'toggleTag("{tag}")' in branch


def test_bad_action_is_hidden_but_legacy_status_still_renders() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    index_source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    styles_source = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    shortcut_source = _keydown_source(app_source)

    assert 'id="badBtn"' not in index_source
    assert '$("badBtn")' not in app_source
    assert 'event.key.toLowerCase() === "x"' not in shortcut_source
    assert 'image.score_status === "bad"' in app_source
    assert ".current-score.bad" in styles_source


def test_reveal_control_is_accessible_and_hidden_without_data() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    index_source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    styles_source = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'id="revealBtn"' in index_source
    assert 'aria-pressed="false"' in index_source
    assert 'id="revealIndicator"' in index_source
    assert 'role="status"' in index_source
    assert 'aria-live="polite"' in index_source
    assert '$("revealBtn").hidden = !state.summary?.reveal_available' in app_source
    assert 'button.setAttribute("aria-pressed", state.revealEnabled ? "true" : "false")' in app_source
    assert ".reveal-command.revealed" in styles_source
    assert ".reveal-indicator[hidden]" in styles_source


def test_reveal_fetches_on_demand_and_guards_stale_object_responses() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = app_source.index("async function toggleReveal()")
    end = app_source.index("async function selectObject(object)", start)
    toggle_source = app_source[start:end]

    assert 'api(`/api/objects/${encodeURIComponent(objectId)}/reveal`)' in toggle_source
    assert "state.revealRequestToken !== requestToken" in toggle_source
    assert "state.revealObjectId !== objectId" in toggle_source
    assert "state.selectedObject?.object_id !== objectId" in toggle_source
    assert "state.revealScores = new Map(" in toggle_source
    assert '$("revealBtn").addEventListener("click"' in app_source


def test_reveal_resets_on_object_change_and_updates_each_image() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    select_start = app_source.index("async function selectObject(object)")
    select_end = app_source.index("function firstUnscoredIndex", select_start)
    select_source = app_source[select_start:select_end]
    render_start = app_source.index("function renderReveal()")
    render_end = app_source.index("function resetReveal()", render_start)
    render_source = app_source[render_start:render_end]

    assert "state.selectedObject?.object_id !== object.object_id" in select_source
    assert "resetReveal()" in select_source
    assert "state.revealScores.get(image.image_id)" in render_source
    assert "Reveal on · R3 ${score.model_score_r3_percent}%" in render_source
    assert "Operational ${score.model_score_operational_percent}%" in render_source
    assert "Reveal on · No model score for this image" in render_source


def test_rcc_rescan_reports_product_deduplication_counts() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "${result.records} source PNGs" in app_source
    assert "${result.review_products} review products" in app_source
    assert "${result.duplicate_groups} duplicate groups" in app_source
    assert "${result.duplicates_suppressed} suppressed" in app_source


def test_reveal_only_checkbox_is_hidden_by_default_and_fetches_on_demand() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    index_source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    styles_source = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'id="revealOnlyControl" hidden' in index_source
    assert 'id="revealOnlyInput" type="checkbox"' in index_source
    assert "Show reveal-scored only" in index_source
    assert 'id="revealOnlyInput" type="checkbox" checked' not in index_source
    assert '$("revealOnlyControl").hidden = !state.summary?.reveal_available' in app_source
    assert '.checkline[hidden]' in styles_source
    assert '$("revealOnlyInput").checked ? "?reveal_only=1" : ""' in app_source
    assert '$("revealOnlyInput").addEventListener("change"' in app_source
    assert '$("revealOnlyInput").checked =' not in app_source


def test_reveal_only_filter_preserves_image_falls_back_and_stays_independent() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    start = app_source.index("async function applyRevealOnlyFilter()")
    end = app_source.index("async function selectObject(object)", start)
    filter_source = app_source[start:end]

    assert "const previousImageId = currentImage()?.image_id" in filter_source
    assert "image.image_id === previousImageId" in filter_source
    assert "preservedIndex >= 0 ? preservedIndex : firstUnscoredIndex(state.images)" in filter_source
    assert "state.imageRequestToken !== requestToken" in filter_source
    assert "state.selectedObject?.object_id !== objectId" in filter_source
    assert "resetReveal" not in filter_source
    assert "revealScores" not in filter_source
    assert 'state.images.length ? `${state.index + 1} / ${state.images.length}` : "0 / 0"' in app_source
    assert "return found >= 0 ? found : 0" in app_source
    assert "state.images = await api(" not in app_source


def test_reveal_summary_shows_matched_and_missing_detail_counts() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "summary.reveal_matched_images.toLocaleString()" in app_source
    assert "summary.reveal_without_notes_or_tags.toLocaleString()" in app_source
    assert "reveal-scored" in app_source
    assert "without note/features" in app_source


def test_missing_reveal_details_filter_controls_the_object_queue() -> None:
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    index_source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="revealMissingDetailsControl" hidden' in index_source
    assert 'id="revealMissingDetailsInput" type="checkbox"' in index_source
    assert "Has reveal cutouts missing details" in index_source
    assert 'id="revealMissingDetailsInput" type="checkbox" checked' not in index_source
    assert (
        '$("revealMissingDetailsControl").hidden = '
        "!state.summary?.reveal_available"
    ) in app_source
    assert (
        'if ($("revealMissingDetailsInput").checked) '
        'params.set("reveal_missing_details", "1")'
    ) in app_source
    assert '"revealMissingDetailsInput"' in app_source
    assert '$("revealMissingDetailsInput").checked =' not in app_source


def test_reveal_context_checkbox_and_request():
    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    index_source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="revealContextControl" hidden' in index_source
    assert 'id="revealContextInput" type="checkbox" />' in index_source
    assert '$("revealContextControl").hidden = !state.summary?.reveal_available' in app_source
    assert '?reveal_context=1' in app_source
    assert '$("revealContextInput").addEventListener("change"' in app_source
