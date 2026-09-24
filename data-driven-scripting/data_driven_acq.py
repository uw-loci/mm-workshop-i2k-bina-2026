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
#   "vispy==0.17.0",
# ]
# ///

from __future__ import annotations

from pathlib import Path
from typing import Generator, NamedTuple

import numpy as np
from psygnal import Signal
from pymmcore_plus import CMMCorePlus
from scipy.ndimage import center_of_mass, gaussian_filter

import cellcast.models.StarDist2D as sd # type: ignore

import ndv
from useq import MDAEvent, MDASequence, GridRowsColumns

from data_driven_mda import DataDrivenMDA
from _writers import ScanWriter, POIWriter
from _viewers import ScanViewer, PoiViewer


class Centroid(NamedTuple):
    y_um: float
    x_um: float


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


class NucleiFinder(DataDrivenMDA[Centroid]):
    """A tile-scan that injects high-res events at points of interest."""

    labels_ready = Signal(np.ndarray, MDAEvent)

    def __init__(
        self,
        mmc: CMMCorePlus,
        rows: int = 7,
        cols: int = 7,
        *,
        act_eagerly: bool = False,
    ) -> None:
        super().__init__(mmc, act_eagerly=act_eagerly)
        self._low_res_rows = rows
        self._low_res_cols = cols
        self._model = sd.init_fluo(gpu=True)

    def scan_sequence(self) -> MDASequence:
        """Return the MDASequence for the low-resolution grid scan."""
        return MDASequence(
            grid_plan=GridRowsColumns(
                rows=self._low_res_rows,
                columns=self._low_res_cols,
                fov_height=self._mmc.getImageHeight() * self._mmc.getPixelSizeUm(),
                fov_width=self._mmc.getImageWidth() * self._mmc.getPixelSizeUm(),
            )
        )

    def steady_state(self) -> Generator[MDAEvent, None, None]:
        for event in self.scan_sequence().iter_events():
            # TODO It'd be great to inscribe these within scan_sequence() itself,
            # but that would require upstream changes.
            yield event.model_copy(update={
                "properties": [("SimObjectiveTurret", "Label", LOW_RES_LABEL)],  # type: ignore[arg-type]
            })

    def find_targets(self, img: np.ndarray, event: MDAEvent) -> Generator[Centroid, None, None]:
        gaussed = gaussian_filter(img, sigma=1)
        labels = self._model.predict_fluo(gaussed).astype(np.uint16)  # type: ignore[attr-defined]
        self.labels_ready.emit(labels, event)
        if not labels.max():
            return
        h, w = img.shape[-2], img.shape[-1]
        px_size = self._mmc.getPixelSizeUm()
        # Each centroid corresponds to a detected nucleus in the image...
        for cy, cx in center_of_mass(labels > 0, labels, index=range(1, labels.max() + 1)):
            # ...which we need to convert to stage coordinates
            yield Centroid(
                y_um=(event.y_pos or 0) + (cy - h / 2) * px_size,
                x_um=(event.x_pos or 0) - (cx - w / 2) * px_size,
            )

    def act_on_target(self, target: Centroid) -> Generator[MDAEvent, None, None]:
        # Perform a single high-resolution snapshot at the detected centroid
        yield MDAEvent(
            x_pos=target.x_um,
            y_pos=target.y_um,
            properties=[("SimObjectiveTurret", "Label", HIGH_RES_LABEL)],  # type: ignore[arg-type]
        )


# ── Main ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    mmc = initialize_core()
    seq = NucleiFinder(mmc, act_eagerly=False)

    slide_viewer = ScanViewer(mmc, seq)  # noqa: F841
    slide_writer = ScanWriter(mmc, seq)  # noqa: F841

    poi_viewer = PoiViewer(mmc)  # noqa: F841
    poi_writer = POIWriter(mmc)  # noqa: F841

    mmc.run_mda(seq)
    ndv.run_app()


if __name__ == "__main__":
    main()
