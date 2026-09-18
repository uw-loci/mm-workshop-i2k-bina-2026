"""Scans the sample at low resolution, identifies puncta, and performs high-resolution imaging at those locations."""

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "cellcast==0.3.0",
#   "ndv==0.5.0",
#   "numpy==2.5.3",
#   "ome-writers==0.3.2",
#   "pymmcore-plus==0.18.1",
#   "pyqt6==6.11.0",
#   "qtpy==2.4.3",
#   "scipy==1.18.1",
#   "tensorstore==0.1.85",
#   "useq-schema==0.9.2",
#   "vispy == 0.17.0",
# ]
# ///

from pathlib import Path
from math import floor, ceil
from typing import TYPE_CHECKING, Generator

import numpy as np
from ome_writers import AcquisitionSettings, Dimension, Position, create_stream
from superqt.utils import ensure_main_thread
from pymmcore_plus import CMMCorePlus
from scipy.ndimage import center_of_mass, gaussian_filter

import cellcast.models.StarDist2D as sd

import ndv
from useq import MDAEvent, MDASequence, GridRowsColumns, Position as UseqPosition

if TYPE_CHECKING:
    from ome_writers import StreamView

ROOT_DIR = Path(__file__).resolve().parent
CFG_PATH = ROOT_DIR / "SimCamera.cfg"
DATA_PATH = ROOT_DIR / "data"

LOW_RES_LABEL = "10x 0.30NA"
HIGH_RES_LABEL = "100x 1.40NA Oil"


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


class LowResAcquisition:

    def __init__(self, mmcore: CMMCorePlus) -> None:
        self._mmc = mmcore
        self._fov_widths = 3
        self._fov_heights = 3

        self._visual = np.zeros(
            (3, self._fov_heights * mmcore.getImageHeight(), self._fov_widths * mmcore.getImageWidth()),
            dtype=np.uint16,
        )
        self._viewer = ndv.ArrayViewer(self._visual, channel_mode="composite", channel_axis=0)
        self._viewer.show()
        self._viewer.widget().setWindowTitle("Slide scan")

        self._centroids: list[tuple[float, float]] = []
        self._model = sd.init_fluo(gpu=True)
        self._mmc.mda.events.frameReady.connect(self._on_image)

    @property
    def centroids(self) -> list[tuple[float, float]]:
        return self._centroids

    def _stage_to_px(self, x_um: float, y_um: float) -> tuple[int, int]:
        pixel_size = self._mmc.getPixelSizeUm()
        px = int(x_um / pixel_size) + (self._fov_widths // 2) * self._mmc.getImageWidth()
        py = int(y_um / pixel_size) + (self._fov_heights // 2) * self._mmc.getImageHeight()
        return px, py

    def _on_image(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        if "g" not in event.index:
            return
        self._low_stream.append(img)
        h, w = self._mmc.getImageHeight(), self._mmc.getImageWidth()
        px, py = self._stage_to_px(event.x_pos or 0, event.y_pos or 0)
        self._visual[0, py:py + h, px:px + w] = img
        self._viewer.data_wrapper.data_changed.emit()
        self._process(img, event)

    def _process(self, img: np.ndarray, event: MDAEvent) -> None:
        gaussed = gaussian_filter(img, sigma=1)
        labels = self._model.predict_fluo(gaussed).astype(np.uint16)
        px, py = self._stage_to_px(event.x_pos or 0, event.y_pos or 0)
        h, w = self._mmc.getImageHeight(), self._mmc.getImageWidth()
        self._visual[1, py:py + h, px:px + w] = labels
        pixel_size = self._mmc.getPixelSizeUm()
        for cy, cx in center_of_mass(labels > 0, labels, index=range(1, labels.max() + 1)):
            cx1, cx2 = floor(cx), ceil(cx)
            cy1, cy2 = floor(cy), ceil(cy)
            self._visual[2, py + cy1:py + cy2 + 1, px + cx1:px + cx2 + 1] = 1
            stage_cx = (event.x_pos or 0) + (cx - w / 2) * pixel_size
            stage_cy = (event.y_pos or 0) + (cy - h / 2) * pixel_size
            self._centroids.append((stage_cy, stage_cx))

    def events(self) -> Generator[MDAEvent, None, None]:
        w, h = self._mmc.getImageWidth(), self._mmc.getImageHeight()
        px_size = self._mmc.getPixelSizeUm()
        grid_plan = GridRowsColumns(
            rows=self._fov_heights,
            columns=self._fov_widths,
            fov_height=h * px_size,
            fov_width=w * px_size,
        )
        settings = AcquisitionSettings(
            root_path=str(DATA_PATH / "low_res.ome.zarr"),
            dimensions=[
                Dimension(
                    name="p",
                    type="position",
                    coords=[
                        Position(
                            name=f"Tile_{pt.name}",
                            grid_row=pt.grid_row,
                            grid_column=pt.grid_col,
                            x_coord=pt.x,
                            y_coord=pt.y,
                        )
                        for pt in grid_plan
                    ],
                ),
                Dimension(name="y", count=h, type="space", unit="um", scale=px_size),
                Dimension(name="x", count=w, type="space", unit="um", scale=px_size),
            ],
            dtype="uint16",
            overwrite=True,
        )
        self._mmc.setProperty("SimObjectiveTurret", "Label", LOW_RES_LABEL)
        with create_stream(settings) as self._low_stream:
            yield from MDASequence(grid_plan=grid_plan).iter_events()


class HighResAcquisition:

    def __init__(self, mmcore: CMMCorePlus) -> None:
        self._mmc = mmcore
        self._viewer = ndv.ArrayViewer()
        self._viewer.widget().setWindowTitle("High-res scans")
        self._viewer.show()

    def prepare(self, centroids: list[tuple[float, float]]) -> None:
        self._centroids = centroids
        w, h = self._mmc.getImageWidth(), self._mmc.getImageHeight()
        px_size = self._mmc.getPixelSizeUm()
        self._settings = AcquisitionSettings(
            root_path=str(DATA_PATH / "high_res.ome.zarr"),
            dimensions=[
                Dimension(
                    name="p",
                    type="position",
                    coords=[
                        Position(name=f"POI_{i}", x_coord=x_um, y_coord=y_um)
                        for i, (y_um, x_um) in enumerate(centroids)
                    ],
                ),
                Dimension(name="y", count=h, type="space", unit="um", scale=px_size),
                Dimension(name="x", count=w, type="space", unit="um", scale=px_size),
            ],
            dtype="uint16",
            overwrite=True,
        )

    @ensure_main_thread
    def _connect_view(self, view: StreamView) -> None:
        self._viewer.data = view
        view.coords_changed.connect(ensure_main_thread(self._viewer.data_wrapper.dims_changed))
        view.coords_changed.connect(ensure_main_thread(self._viewer.data_wrapper.data_changed))

    def _on_image(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        if "p" not in event.index:
            return
        self._high_stream.append(img)

    def events(self) -> Generator[MDAEvent, None, None]:
        self._mmc.setProperty("SimObjectiveTurret", "Label", HIGH_RES_LABEL)
        self._mmc.mda.events.frameReady.connect(self._on_image)
        positions = [
            UseqPosition(x=x_um, y=y_um, name=f"POI_{i}")
            for i, (y_um, x_um) in enumerate(self._centroids)
        ]
        with create_stream(self._settings) as self._high_stream:
            self._connect_view(self._high_stream.view())
            yield from MDASequence(stage_positions=positions).iter_events()
        self._mmc.mda.events.frameReady.disconnect(self._on_image)


def main() -> None:
    mmc = initialize_core()
    low_res = LowResAcquisition(mmc)
    high_res = HighResAcquisition(mmc)

    def start_high_res() -> None:
        mmc.mda.events.sequenceFinished.disconnect(start_high_res)
        centroids = low_res.centroids
        if centroids:
            high_res.prepare(centroids)
            mmc.run_mda(high_res.events())

    mmc.mda.events.sequenceFinished.connect(start_high_res)
    mmc.run_mda(low_res.events())
    ndv.run_app()


if __name__ == "__main__":
    main()
