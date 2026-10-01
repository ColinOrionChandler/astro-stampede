"""Expected trail lengths for display; source pixels and label identities stay intact."""
import json
import math
import re


def validate_metadata(metadata):
    value = metadata.get('expected_trail_pixels')
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < 0
    ):
        raise ValueError('expected_trail_pixels must be a finite nonnegative number or null.')


def annotate(images, *, legacy=False):
    """Attach per-image lengths, including pairs, without reusing primary geometry."""
    for image in images:
        if legacy:
            name = image.get('filename') or image.get('relative_path', '')
            match = re.search(r'_(\d+(?:\.\d+)?)dPix\.png(?:\.gz)?$', name)
            value = float(match[1]) if match else None
            image['expected_trail_pixels'] = value if value is not None and math.isfinite(value) else None
        else:
            metadata = json.loads(image.get('annotation') or '{}')
            image['expected_trail_pixels'] = metadata.get('expected_trail_pixels')
        annotate(image.get('pairs', []), legacy=legacy)
        if legacy and image.get('comparison'):
            annotate([image['comparison']], legacy=True)
    return images
