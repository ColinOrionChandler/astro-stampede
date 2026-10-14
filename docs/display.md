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
