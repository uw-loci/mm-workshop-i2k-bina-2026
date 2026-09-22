from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from ome_writers import AcquisitionSettings, Dimension, Position, create_stream, useq_to_acquisition_settings
from pymmcore_plus import CMMCorePlus

if TYPE_CHECKING:
    from useq import MDAEvent, MDASequence

DATA_PATH = Path(__file__).resolve().parent / "data"


class ScanWriter:
    """Writes frames of the low-resolution scan to an OME-Zarr."""

    def __init__(self, mmcore: CMMCorePlus, scan_seq: MDASequence) -> None:
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
        if event.metadata.get("source") == "steady_state":
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
        if event.metadata.get("source") != "event":
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
