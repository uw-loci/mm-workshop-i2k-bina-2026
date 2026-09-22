from __future__ import annotations

from typing import TYPE_CHECKING

import ndv
from ndv.models import ClimsManual

import numpy as np
from pymmcore_plus import CMMCorePlus
from superqt.utils import ensure_main_thread

if TYPE_CHECKING:
    from useq import MDAEvent
    from data_driven_acq import NucleiFinder


class ScanViewer:
    """Assembles low-res tiles into a live slide map as frames arrive."""

    def __init__(self, mmcore: CMMCorePlus, seq: "NucleiFinder") -> None:
        self._mmcore = mmcore
        self._visual = np.zeros(
            (2, mmcore.getImageHeight(), mmcore.getImageWidth()),
            dtype=np.uint16,
        )
        self._min_pos: list[float | None] = [None, None]  # [max_x_um, min_y_um] of top-left corner (x-axis inverted)

        self._viewer = ndv.ArrayViewer(self._visual, channel_mode="composite", channel_axis=0)
        self._viewer.display_model.luts[0].name = "slide scan"
        self._viewer.display_model.luts[1].name = "detected nuclei"
        self._viewer.display_model.luts[1].clims = ClimsManual(min=0, max=1)
        self._viewer.widget().setWindowTitle("Slide scan")
        self._viewer.show()

        seq.labels_ready.connect(self.on_labels)
        mmcore.mda.events.frameReady.connect(self._on_frame)

    def _place_tile(self, channel: int, img: np.ndarray, event: MDAEvent) -> None:
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
                (2, self._visual.shape[1] + shift_row, self._visual.shape[2] + shift_col),
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
            expanded = np.zeros((2, new_h, new_w), dtype=self._visual.dtype)
            expanded[:, :self._visual.shape[1], :self._visual.shape[2]] = self._visual
            self._visual = expanded

        self._visual[channel, row:row + h, col:col + w] = img
        self._refresh(self._visual)

    def _on_frame(self, img: np.ndarray, event: MDAEvent) -> None:
        if event.metadata.get("source") == "steady_state":
            self._place_tile(0, img, event)

    def on_labels(self, labels: np.ndarray, event: MDAEvent) -> None:
        self._place_tile(1, labels, event)

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
        if event.metadata.get("source") != "event":
            return
        self._poi_stack.append(img)
        self._refresh(np.stack(self._poi_stack))

    @ensure_main_thread
    def _refresh(self, data: np.ndarray) -> None:
        self._viewer.data = data
        self._viewer.data_wrapper.data_changed.emit()
