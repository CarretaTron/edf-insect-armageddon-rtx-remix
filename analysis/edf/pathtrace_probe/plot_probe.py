"""Plot measured GPU outputs from the illuminated x86 Bridge replay."""
from pathlib import Path
import json
import os

HERE = Path(__file__).resolve().parent
os.environ.setdefault('MPLCONFIGDIR', str(HERE / 'matplotlib-cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


verification = json.loads((HERE / 'x86_lit_verification.json').read_text())
assert verification['passed'] and not verification['fixtureOnly']
samples = []
for sample in verification['samples']:
    metadata_path = Path(sample['metadata'])
    metadata = json.loads(metadata_path.read_text())
    width, height = metadata['rendererResolution']
    images = {image['name']: image for image in metadata['images']}
    depth = np.fromfile(metadata_path.parent / images['linear_z']['file'], dtype='<f4').reshape(height, width)
    hits = np.isfinite(depth) & (depth != np.float32(metadata['missLinearZ']))
    albedo = np.fromfile(metadata_path.parent / images['albedo']['file'], dtype='<u4').reshape(height, width)
    albedo = np.stack([(albedo >> shift) & 1023 for shift in (0, 10, 20)], axis=-1).astype(float) / 1023
    radiance = np.fromfile(metadata_path.parent / images['direct_diffuse_radiance']['file'], dtype='<f2').reshape(height, width, 4)[..., :3].astype(float)
    assert metadata['originalTargetsUnchanged'] and metadata['jitterPixels'] == [0, 0]
    assert np.array_equal(np.any(radiance > 0, axis=-1), hits)
    samples.append((metadata, depth, hits, albedo, radiance))

bounds = np.asarray([sample[0]['hitBounds'] for sample in samples])
left, top = np.maximum(bounds[:, :2].min(axis=0) - 25, [0, 0])
right, bottom = np.minimum(bounds[:, 2:].max(axis=0) + 26, [1920, 1080])
region = np.s_[top:bottom, left:right]
extent = (left, right, bottom, top)
near = min(float((-depth[hits]).min()) for _, depth, hits, _, _ in samples)
far = max(float((-depth[hits]).max()) for _, depth, hits, _, _ in samples)
fig, axes = plt.subplots(3, 3, figsize=(16, 5.8), constrained_layout=True)
source_frames = [755, 1182, 1292]
for column, (metadata, depth, hits, albedo, radiance) in enumerate(samples):
    depth_image = np.ma.masked_where(~hits[region], -depth[region])
    depth_plot = axes[0, column].imshow(depth_image, cmap='viridis', vmin=near, vmax=far,
                                      interpolation='nearest', extent=extent, aspect='equal')
    albedo_display = np.where(hits[..., None], np.power(albedo, 1 / 2.2), 0)
    axes[1, column].imshow(albedo_display[region], interpolation='nearest', extent=extent, aspect='equal')
    radiance_display = np.power(np.maximum(radiance, 0) / (1 + np.maximum(radiance, 0)), 1 / 2.2)
    axes[2, column].imshow(radiance_display[region], interpolation='nearest', extent=extent, aspect='equal')
    axes[0, column].set_title(f'Captura {source_frames[column]} | {metadata["depthHitCount"]:,} hits GPU'.replace(',', '.'))
    for row in range(3):
        axes[row, column].set_facecolor('#111827')
        axes[row, column].tick_params(labelsize=8)
        if row < 2:
            axes[row, column].tick_params(labelbottom=False)
        if column:
            axes[row, column].tick_params(labelleft=False)
    axes[2, column].set_xlabel('X na imagem original (pixels)')
axes[0, 0].set_ylabel('Profundidade linear\nY em pixels')
axes[1, 0].set_ylabel('Albedo sintético\nY em pixels')
axes[2, 0].set_ylabel('Radiância direta\nY em pixels')
fig.colorbar(depth_plot, ax=list(axes[0]), shrink=.7, pad=.008, label='−viewZ (unidades decodificadas)')
fig.suptitle('Única mesh validada: outputs GPU offscreen via Bridge x86\n'
             'Primeiras duas câmeras quase paradas; terceira após movimento. Raster original preservado.', fontsize=13)
fig.savefig(HERE / 'gpu_probe_comparison.png', dpi=150)
fig.savefig(HERE / 'gpu_probe_comparison.svg')
plt.close(fig)
print(str(HERE / 'gpu_probe_comparison.png'))
