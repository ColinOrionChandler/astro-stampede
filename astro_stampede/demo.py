"""Generate new synthetic pixels and manifests outside distributable assets."""
import csv
import json
import math
from pathlib import Path
import random
import struct
import zlib


def png_bytes(frame=0, comparison=False):
    size = 160
    rng = random.Random(3100 + frame)
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            center = 140 * math.exp(-((x - 80) ** 2 + (y - 80) ** 2) / 28)
            tail = (0 if comparison else 65 * math.exp(-((y - 80 - .25 * (x - 80)) ** 2) / 35) * math.exp(-(x - 80) / 30)) if x >= 80 else 0
            star = 100 * math.exp(-((x - 25 - frame * 3) ** 2 + (y - 40) ** 2) / 8)
            raw.append(max(0, min(255, int(12 + rng.gauss(0, 3) + center + tail + star))))
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', size, size, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def generate(destination):
    root = Path(destination).expanduser()
    root.mkdir(parents=True, exist_ok=False)
    images = root / 'images'; images.mkdir()
    with (root / 'manifest.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=['image_id', 'object_id', 'relative_path', 'order', 'role', 'comparison_id', 'model_score_r3', 'model_score_operational', 'metadata'])
        writer.writeheader()
        for frame in range(6):
            for comparison in (False, True):
                identifier = f'synthetic-{frame}-' + ('comparison' if comparison else 'primary')
                filename = identifier + '.png'
                (images / filename).write_bytes(png_bytes(frame, comparison))
                writer.writerow(dict(image_id=identifier, object_id='Synthetic Object', relative_path='images/' + filename,
                    order=frame, role='comparison' if comparison else 'primary',
                    comparison_id='' if comparison else f'synthetic-{frame}-comparison',
                    metadata=json.dumps({'expected_trail_pixels': (8 if comparison else 24) + frame * 3}),
                    model_score_r3='' if comparison else .73, model_score_operational='' if comparison else .65))
