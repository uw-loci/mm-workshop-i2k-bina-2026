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

from __future__ import annotations

from collections import deque
from contextlib import ExitStack
from pathlib import Path
from typing import Generator, Iterable, Iterator

import numpy as np
from ome_writers import AcquisitionSettings, Dimension, Position, create_stream, useq_to_acquisition_settings
from superqt.utils import ensure_main_thread
from pymmcore_plus import CMMCorePlus
from scipy.ndimage import center_of_mass, gaussian_filter

import cellcast.models.StarDist2D as sd

import ndv
from useq import MDAEvent, MDASequence, GridRowsColumns

ROOT_DIR = Path(__file__).resolve().parent
CFG_PATH = ROOT_DIR / "SimCamera.cfg"
DATA_PATH = ROOT_DIR / "data"

LOW_RES_LABEL = "10x 0.30NA"
HIGH_RES_LABEL = "100x 1.40NA Oil"

# TODO: Enable POI decisions based on saved low-res scans
# TODO: Consider rewriting the script in multiple pieces? A low-res scan piece, then a decision piece, then a high-res scan piece

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


class DataDrivenMDASequence(Iterable[MDAEvent]):
    """A tile-scan that injects high-res events at points of interest."""

    def __init__(
        self,
        mmc: CMMCorePlus,
        rows: int = 7,
        cols: int = 7,
        *,
        image_pois_eagerly: bool = False,
    ) -> None:
        self._mmc = mmc
        self._low_res_rows = rows
        self._low_res_cols = cols
        self._image_pois_eagerly = image_pois_eagerly

        self._model = sd.init_fluo(gpu=True)
        self._queue: deque[MDAEvent] = deque()

    # ── 1. Scanner ────────────────────────────────────────────────────────────────

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

    def _scan_events(self) -> Generator[MDAEvent, None, None]:
        """Yield low-resolution grid scan events."""
        for event in self.scan_sequence().iter_events():
            # TODO It'd be great to inscribe these within scan_sequence() itself,
            # but that would require upstream changes.
            yield event.model_copy(update={
                "metadata": {**event.metadata, "source": "scan"},
                "properties": [("SimObjectiveTurret", "Label", LOW_RES_LABEL)],  # type: ignore[arg-type]
            })

    # ── 2. Decision ───────────────────────────────────────────────────────────────

    def _find_nuclei(self, img: np.ndarray, event: MDAEvent) -> list[tuple[float, float]]:
        """Return stage-space (y_um, x_um) centroids of nuclei detected in img."""
        gaussed = gaussian_filter(img, sigma=1)
        labels = self._model.predict_fluo(gaussed).astype(np.uint16)  # type: ignore[attr-defined]
        if not labels.max():
            return []
        h, w = img.shape[-2], img.shape[-1]
        px_size = self._mmc.getPixelSizeUm()
        centroids = []
        for cy, cx in center_of_mass(labels > 0, labels, index=range(1, labels.max() + 1)):
            stage_cx = (event.x_pos or 0) - (cx - w / 2) * px_size
            stage_cy = (event.y_pos or 0) + (cy - h / 2) * px_size
            centroids.append((stage_cy, stage_cx))
        return centroids

    # ── 3. Actuator ───────────────────────────────────────────────────────────────

    def _inject_events(self, centroids: list[tuple[float, float]]) -> None:
        """Inject high-resolution events for each centroid into the queue."""
        events = [
            MDAEvent(
                x_pos=x_um,
                y_pos=y_um,
                properties=[("SimObjectiveTurret", "Label", HIGH_RES_LABEL)],  # type: ignore[arg-type]
                metadata={"source": "data_driven", "poi_index": i},
            )
            for i, (y_um, x_um) in enumerate(centroids)
        ]
        if self._image_pois_eagerly:
            self._queue.extendleft(reversed(events))
        else:
            self._queue.extend(events)

    # ── Iteration ─────────────────────────────────────────────────────────────────

    def _on_frame(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        # frameReady fires synchronously on the MDA thread between event acquisitions,
        # so any events injected here are in the queue before the next yield.
        if event.metadata.get("source") == "scan":
            centroids = self._find_nuclei(img, event)
            if centroids:
                self._inject_events(centroids)

    def __iter__(self) -> Iterator[MDAEvent]:
        self._queue.clear()
        self._mmc.mda.events.frameReady.connect(self._on_frame)
        for event in self._scan_events():
            self._queue.append(event)
        try:
            while self._queue:
                yield self._queue.popleft()
        finally:
            self._mmc.mda.events.frameReady.disconnect(self._on_frame)


class ScanWriter:
    """Writes frames of the low-resolution scan to an OME-Zarr."""

    def __init__(self, mmcore: CMMCorePlus, seq: DataDrivenMDASequence) -> None:
        scan_seq = seq.scan_sequence()
        w, h = mmcore.getImageWidth(), mmcore.getImageHeight()
        # NB The next release of ome-writers will save the positions to OME-Zarr correctly
        settings = AcquisitionSettings(
            root_path=str(DATA_PATH / "low_res.ome.zarr"),
            **useq_to_acquisition_settings(scan_seq, w, h, pixel_size_um=mmcore.getPixelSizeUm()),  # type: ignore[arg-type]
            dtype="uint16",
            overwrite=True,
        )
        self._stack = ExitStack()
        self._stream = self._stack.enter_context(create_stream(settings))

        mmcore.mda.events.frameReady.connect(self._on_frame)
        mmcore.mda.events.sequenceFinished.connect(self._on_done)

    def _on_frame(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        if event.metadata.get("source") == "scan":
            self._stream.append(img)

    def _on_done(self, _: object) -> None:
        self._stack.close()


class POIWriter:
    """Writes frames of points of interest (POI) to an OME-Zarr."""

    def __init__(self, mmcore: CMMCorePlus) -> None:
        self._mmcore = mmcore
        # TODO: Ideally we could create a stream immediately and write frames as they arrive,
        # rather than accumulating them in memory. Unfortunately ome-writers does not yet support
        # the writing of an unknown number of frames, so we need to wait to write until all of
        # them are known.
        self._pois: list[tuple[np.ndarray, MDAEvent]] = []
        self._px_size: float = 1.0

        mmcore.mda.events.frameReady.connect(self._on_frame)
        mmcore.mda.events.sequenceFinished.connect(self._on_done)

    def _on_frame(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        if event.metadata.get("source") != "data_driven":
            return
        self._px_size = self._mmcore.getPixelSizeUm()
        self._pois.append((img, event))

    def _on_done(self, _: object) -> None:
        if not self._pois:
            return
        h, w = self._pois[0][0].shape[-2], self._pois[0][0].shape[-1]
        # NB The next release of ome-writers will save the positions to OME-Zarr correctly
        coords = [Position(
            name=f"POI_{i}",
            x_coord=event.x_pos or 0.0,
            y_coord=event.y_pos or 0.0
        ) for i, (_, event) in enumerate(self._pois)]
        settings = AcquisitionSettings(
            root_path=str(DATA_PATH / "high_res.ome.zarr"),
            dimensions=(
                Dimension(name="p", type="position", coords=coords),
                Dimension(name="y", count=h, scale=self._px_size, unit="micrometer"),
                Dimension(name="x", count=w, scale=self._px_size, unit="micrometer"),
            ),
            dtype="uint16",
            overwrite=True,
        )
        with create_stream(settings) as stream:
            for frame, _ in self._pois:
                stream.append(frame)


class ScanViewer:
    """Assembles low-res tiles into a live slide map as frames arrive."""

    def __init__(self, mmcore: CMMCorePlus) -> None:
        self._mmcore = mmcore
        self._visual = np.zeros(
            (3, mmcore.getImageHeight(), mmcore.getImageWidth()),
            dtype=np.uint16,
        )
        self._min_pos: list[float | None] = [None, None]  # [max_x_um, min_y_um] of top-left corner (x-axis inverted)

        self._viewer = ndv.ArrayViewer(self._visual, channel_mode="composite", channel_axis=0)
        self._viewer.widget().setWindowTitle("Slide scan")
        self._viewer.show()

        mmcore.mda.events.frameReady.connect(self._on_frame)

    def _on_frame(self, img: np.ndarray, event: MDAEvent) -> None:
        if event.metadata.get("source") != "scan":
            return

        h, w = img.shape[-2], img.shape[-1]
        px_size = self._mmcore.getPixelSizeUm() or 1.0
        x_um = event.x_pos or 0.0
        y_um = event.y_pos or 0.0

        x0, y0 = self._min_pos[0], self._min_pos[1]
        if x0 is None or y0 is None:
            x0 = x_um
            y0 = y_um
            self._min_pos[0] = x0
            self._min_pos[1] = y0

        # Expand canvas to the top or left if this frame falls outside current bounds
        shift_col = max(0, round((x_um - x0) / px_size))  # x-axis inverted: larger x_um → left
        shift_row = max(0, round((y0 - y_um) / px_size))
        if shift_col > 0 or shift_row > 0:
            x0 = max(x0, x_um)
            y0 = min(y0, y_um)
            self._min_pos[0] = x0
            self._min_pos[1] = y0
            expanded = np.zeros(
                (3, self._visual.shape[1] + shift_row, self._visual.shape[2] + shift_col),
                dtype=self._visual.dtype,
            )
            expanded[:, shift_row:, shift_col:] = self._visual
            self._visual = expanded

        col = round((x0 - x_um) / px_size)  # x-axis inverted
        row = round((y_um - y0) / px_size)

        # Expand canvas to the right or bottom if this frame falls outside current bounds
        new_h = max(self._visual.shape[1], row + h)
        new_w = max(self._visual.shape[2], col + w)
        if new_h > self._visual.shape[1] or new_w > self._visual.shape[2]:
            expanded = np.zeros((3, new_h, new_w), dtype=self._visual.dtype)
            expanded[:, :self._visual.shape[1], :self._visual.shape[2]] = self._visual
            self._visual = expanded

        self._visual[0, row:row + h, col:col + w] = img
        self._refresh(self._visual)

    @ensure_main_thread
    def _refresh(self, data: np.ndarray) -> None:
        self._viewer.data = data
        self._viewer.data_wrapper.data_changed.emit()


class PoiViewer:
    """Accumulates high-res POI frames into a growing stack as they arrive."""

    def __init__(self, mmcore: CMMCorePlus) -> None:
        self._poi_stack: list[np.ndarray] = []
        self._viewer = ndv.ArrayViewer()
        self._viewer.widget().setWindowTitle("High-res scans")
        self._viewer.show()

        mmcore.mda.events.frameReady.connect(self._on_frame)

    def _on_frame(self, img: np.ndarray, event: MDAEvent) -> None:
        if event.metadata.get("source") != "data_driven":
            return
        self._poi_stack.append(img)
        self._refresh(np.stack(self._poi_stack))

    @ensure_main_thread
    def _refresh(self, data: np.ndarray) -> None:
        self._viewer.data = data
        self._viewer.data_wrapper.data_changed.emit()


# ── Main ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    mmc = initialize_core()
    seq = DataDrivenMDASequence(mmc, image_pois_eagerly=True)

    slide_viewer = ScanViewer(mmc)  # noqa: F841
    slide_writer = ScanWriter(mmc, seq)  # noqa: F841

    poi_viewer = PoiViewer(mmc)  # noqa: F841
    poi_writer = POIWriter(mmc)  # noqa: F841

    mmc.run_mda(seq)
    ndv.run_app()


if __name__ == "__main__":
    main()
