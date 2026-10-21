# Display and image loading

## Image loading performance

The browser preloads the current image, the next three images, the previous image,
and their comparison frames, with one background request at a time. It retains
at most ten preload images; primary frames larger than four megapixels are skipped.
Versioned PNG URLs allow browser caching for one hour. Restarting the server,
rescanning, or changing settings changes those URLs, so use **Rescan** after
replacing source pixels. Source PNGs are never rewritten or copied into a second
classification dataset.

Manifest and Active Asteroids image requests skip rebuilding effective scores;
score-dependent RCC representative selection retains its existing lookup.
Object lists, classification saves, and first uncached reads can still take time.
For sluggish systems, check Activity Monitor's memory pressure and available space
on the startup disk as well as activity on the image drive. Raising CPU scheduling
priority does not reserve RAM or eliminate storage latency.

## Expected trail bar

The **Expected trail bar** checkbox under Display toggles a lime-green,
black-outlined bar at the lower right of each image, matching the RCC batch
overlay. Its length is the predicted trail length in original PNG pixels; it
does not indicate the trail direction or an angular sky scale. Zoom and Fit scale
the bar with the image. Inversion, brightness and contrast leave it green.
Source PNGs and classifications are unchanged.

For a generic manifest, put `{"expected_trail_pixels": 24}` in that row's
`metadata` JSON (CSV writers handle the required quoting). Values must be finite,
nonnegative numbers; null or an omitted key means unavailable. Comparison rows
need their own value, including during blinking. After editing metadata, select
**Rescan**. The runtime demo supplies synthetic lengths for both panels.

In RCC compatibility mode, the length is read from the terminal `_NdPix.png`
filename suffix, including decimal values. No length is inferred from pixel
scale alone. Missing lengths, lengths below 3 pixels, and bars too large for the
image are reported in Display and omitted instead of being clipped or enlarged.
Turn the overlay off when reviewing copies that already have a bar baked in.

Validate without opening classification stores:

```sh
astro-stampede --root local/demo --manifest local/demo/manifest.csv \
  --dataset synthetic-demo --state-dir local/demo/state --validate
```

## Image points

Click a primary or comparison image to save one point for the current reviewer.
The crosshair marks the center of the selected PNG pixel. Coordinates are integer
column and row indices: `(0, 0)` is the top-left pixel, x increases rightward,
and y increases downward. These are cutout PNG coordinates, without a FITS or
sky-coordinate conversion. Fit, zoom, scrolling, and display filters do not
change the saved coordinates.

Another click replaces the point. **Clear point** removes it independently of
the image's score, tags, and note. Points do not classify images or advance the
queue. Primary and comparison frames have independent points; an ordinary paired
image retains its point when viewed on its own. During blinking or playback,
the visible frame is held from pointer-down through release, then animation
continues. Drags, canceled gestures, unloaded images, and object transitions
cannot record a point.

**Points saved** confirms that all point changes reached SQLite. A failed write
stays queued: use **Retry point saves**, or **Discard unsaved point changes** to
restore the last saved state. Export, settings changes, and shutdown wait for
point saves and stop if saving fails. When a source file changes, its older point
is labeled stale and its crosshair is hidden. Rescan and reload the image before
marking the new revision; discard any queued changes for the old revision first.

## Object keyboard navigation

Press **`,`** for the previous object and **`.`** for the next object. Navigation
follows the displayed queue, including completed objects, and wraps at both ends.
The current filters, batch, sort order, and existing 300-object display limit
apply. A newly selected object opens its first unscored image, or its first image
when all are scored. With no selection, period starts at the first object and
comma at the last. Arrow keys still navigate images within an object.

Object shortcuts are inactive in text fields, editable content, and dialogs;
modifier combinations and held-key repeats are ignored. Changing objects stops
playback while preserving the blink preference.
