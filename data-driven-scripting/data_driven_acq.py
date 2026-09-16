"""Scans the sample at low resolution, identifies puncta, and performs high-resolution imaging at those locations."""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cellcast==0.3.0",
#   "ndv==0.5.0",
#   "numpy==2.5.3",
#   "pymmcore-plus==0.18.1",
#   "pyqt6==6.11.0",
#   "qtpy==2.4.3",
#   "scipy==1.18.1",
#   "tensorstore==0.1.85",
#   "useq-schema==0.9.2",
#   "vispy == 0.17.0",
# ]
# ///

from collections import deque
from collections.abc import Iterable
from pathlib import Path
from math import floor, ceil
from typing import Generator

import numpy as np
from pymmcore_plus import CMMCorePlus
from scipy.ndimage import center_of_mass

import cellcast.models.StarDist2D as sd
import tensorstore as ts

import ndv
from useq import MDAEvent

ROOT_DIR = Path(__file__).resolve().parent
CFG_PATH = ROOT_DIR / "SimCamera.cfg"
DATA_PATH = ROOT_DIR / "data"

LOW_RES_LABEL = "10x 0.30NA"
HIGH_RES_LABEL = "100x 1.40NA Oil"

# TODO: Rewrite as low-res scan, then high-res POIs afterwards
#   This could be a setting in the script, and we could talk about the tradeoffs
# TODO: Enable POI decisions based on saved low-res scans
# TODO: Consider one file per high-res scan
# TODO: Save out positional metadata for the high-res scans (maybe use ome-writers?)
# TODO: Consider rewriting the script in multiple pieces? A low-res scan piece, then a decision piece, then a high-res scan piece

DEFAULT_GRID_SIZE = 3

def initialize_core(mmc: CMMCorePlus | None = None) -> CMMCorePlus:
    mmc = mmc or CMMCorePlus()
    mmc.setDeviceAdapterSearchPaths([*mmc.getDeviceAdapterSearchPaths(), str(CFG_PATH.parent)])
    mmc.loadSystemConfiguration(str(CFG_PATH))
    mmc.setXYStageDevice("SimXY")

    # The default slew rate is tuned to look like a real stage
    # (~100 um/s), which would make each tile-to-tile move slow
    # over an NxN grid. Speed it up, cut exposure, and extend the
    # wait timeout to match.
    mmc.setProperty("SimXY", "SlewTimePerStep_s", 0.0001)
    mmc.setExposure(10.0)
    mmc.setTimeoutMs(120_000)

    mmc.setProperty("SimCam", "Mode", "Nuclei")
    mmc.setAutoShutter(False)

    return mmc

class DataDrivenAcquisition(Iterable[MDAEvent]):

    def __init__(self, mmcore: CMMCorePlus) -> None:
        self._mmc = mmcore

        self._fov_widths = 5
        self._fov_heights = 5

        self._low_res_datastore: ts.TensorStore = self._new_datastore(DATA_PATH / 'dataset/', channels=3, fov_x=self._fov_widths, fov_y=self._fov_heights)
        self._low_res_viewer = ndv.ArrayViewer(self._low_res_datastore, channel_mode="composite", channel_axis=0)
        self._low_res_viewer.show()
        self._low_res_viewer.widget().setWindowTitle("Slide scan")

        # self._high_res_datastores: dict[int, ts.TensorStore] = {}
        self._high_res_datastore = self._new_datastore(DATA_PATH / 'high_res/', z=100)
        self._high_res_index = -1
        self._high_res_viewer = ndv.ArrayViewer(self._high_res_datastore, channel_mode="composite", channel_axis=0)
        self._high_res_viewer.widget().setWindowTitle("Nuclei scans")
        self._high_res_viewer.show()

        self._centroids_to_process = deque()

        self._mmc.mda.events.frameReady.connect(self.on_image)
        self._model = sd.init_fluo(gpu=True)

    def _new_datastore(self, path: str | Path, channels: int = 1, fov_x: int = 1, fov_y: int = 1, z: int = 1) -> ts.TensorStore:
        return ts.open({
            'driver': 'n5',
            'kvstore': {
                'driver': 'file',
                'path': str(path),
            },
            'metadata': {
                'compression': {
                    'type': 'gzip'
                },
                'dataType': 'uint32',
                'dimensions': [channels, z, fov_y * self._mmc.getImageHeight(), fov_x * self._mmc.getImageWidth()],
                'blockSize': [1, 1, self._mmc.getImageHeight(), self._mmc.getImageWidth()],
            },
            'create': True,
            'delete_existing': True,
        }).result()

    def __iter__(self) -> Iterable[MDAEvent]:
        return self

    def _stage_to_px(self, x_um: float, y_um: float) -> tuple[int, int]:
        pixel_size = self._mmc.getPixelSizeUm()
        px = int(x_um / pixel_size) + (self._fov_widths // 2) * self._mmc.getImageWidth()
        py = int(y_um / pixel_size) + (self._fov_heights // 2) * self._mmc.getImageHeight()
        return px, py

    def on_image(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        h, w = self._mmc.getImageHeight(), self._mmc.getImageWidth()
        if "low_idx" in event.index:
            px, py = self._stage_to_px(event.x_pos or 0, event.y_pos or 0)
            self._low_res_datastore[0, 0, py:py + h, px:px + w] = img
            self._low_res_viewer.data_wrapper.data_changed.emit()
            self._process(img, event)
        if "high_idx" in event.index:
            self._high_res_datastore[0, event.index["high_idx"], 0:h, 0:w] = img
            self._high_res_viewer.data_wrapper.data_changed.emit()


    def _process(self, img: np.ndarray, event: MDAEvent) -> None:
        # Find nuclei
        labels = self._model.predict_fluo(img).astype(np.uint32)
        # Highlight labels in the low-res scan
        px, py = self._stage_to_px(event.x_pos or 0, event.y_pos or 0)
        h, w = self._mmc.getImageHeight(), self._mmc.getImageWidth()
        self._low_res_datastore[1, 0, py:py + h, px:px + w] = labels
        # Compute centroids for each label
        centroids = center_of_mass(labels > 0, labels, index=range(1, labels.max() + 1))
        pixel_size = self._mmc.getPixelSizeUm()
        for cy, cx in centroids:
            # Highlight centroids in the low-res scan
            cx1, cx2 = floor(cx), ceil(cx)
            cy1, cy2 = floor(cy), ceil(cy)
            self._low_res_datastore[2, 0, py + cy1:py + cy2 + 1, px + cx1:px + cx2 + 1] = 1
            # Store centroids for high-res scan
            stage_cx = (event.x_pos or 0) + (cx - w / 2) * pixel_size
            stage_cy = (event.y_pos or 0) + (cy - h / 2) * pixel_size
            self._centroids_to_process.append((stage_cy, stage_cx))

    def events(self) -> Generator[MDAEvent, None, None]:
        # Perform a low-resolution scan...
        for event in self._low_res_scan():
            yield event
            # ...until we find an interesting point...
            while self._centroids_to_process:
                # ...where we pause to perform a high-resolution scan
                yield from self._high_res_scan(self._centroids_to_process.popleft())


    def _low_res_scan(self) -> Generator[MDAEvent, None, None]:
        idx = 0
        left, right = -1 * (self._fov_widths // 2), self._fov_widths // 2
        top, bottom = -1 * (self._fov_heights // 2), self._fov_heights // 2
        # Snake scan
        for y in range(top, bottom + 1):
            start = left if y % 2 == 0 else right
            stop = right if start == left else left
            step = 1 if start == left else -1
            for x in range(start, stop + step, step):
                self._mmc.setProperty("SimObjectiveTurret", "Label", LOW_RES_LABEL)
                pixel_size = self._mmc.getPixelSizeUm()
                yield MDAEvent(
                    index={'low_idx': idx},
                    x_pos=x * self._mmc.getImageWidth() * pixel_size,
                    y_pos=y * self._mmc.getImageHeight() * pixel_size,
                    keep_shutter_open=True,
                )
                idx += 1

    def _high_res_scan(self, centroid: tuple[float, float]) -> Generator[MDAEvent, None, None]:
        self._mmc.setProperty("SimObjectiveTurret", "Label", HIGH_RES_LABEL)

        self._high_res_index += 1
        y_um, x_um = centroid
        print(f"Snapping a single image at ({y_um}, {x_um})")
        yield MDAEvent(
            index={'high_idx': self._high_res_index},
            x_pos=x_um,
            y_pos=y_um,
            keep_shutter_open=True,
        )

def main() -> None:

    mmc: CMMCorePlus = initialize_core()

    acq = DataDrivenAcquisition(mmc)
    mmc.run_mda(acq.events())
    ndv.run_app()

if __name__ == "__main__":
    main()
