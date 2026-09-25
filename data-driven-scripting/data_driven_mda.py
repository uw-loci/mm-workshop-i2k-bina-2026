from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Generic, Iterable, Iterator, TypeVar

from pymmcore_plus import CMMCorePlus
from useq import MDAEvent

if TYPE_CHECKING:
    import numpy as np

# Some data type representing the target
T = TypeVar("T")


class DataDrivenMDA(Iterable[MDAEvent], Generic[T], ABC):
    """A data-driven MDA sequence that reacts to acquired frames in real time.

    Subclasses implement three methods:
    - `steady_state()`: the baseline acquisition sequence
    - `find_targets()`: detect items of interest in each frame
    - `act_on_target()`: yield MDAEvents in response to a detected target

    Pass instances directly to `CMMCorePlus.run_mda()`. Connect to
    `CMMCorePlus.mda.events.frameReady` to receive images as they are acquired.
    Each frame's `MDAEvent.metadata["source"]` will be either `"steady_state"`
    or `"event"` to indicate whether the frame came from the baseline sequence
    or from a target response.
    """

    # -- These methods must be implemented -- #

    @abstractmethod
    def steady_state(self) -> Iterable[MDAEvent]:
        """Return the baseline sequence of acquisition events."""

    # -- These methods should be implemented -- #

    def find_targets(self, _img: np.ndarray, _event: MDAEvent) -> Iterable[T]:
        """Return targets found in a frame."""
        return ()

    def act_on_target(self, _target: T) -> Iterable[MDAEvent]:
        """Return MDA events in response to a target."""
        return ()

    # -- These methods are internal details -- #

    def __init__(self, mmc: CMMCorePlus, *, act_eagerly: bool = False) -> None:
        self._mmc = mmc
        self._queue: deque[MDAEvent] = deque()
        self._act_eagerly = act_eagerly

    def _on_frame(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        if event.metadata.get("source") != "steady_state":
            return
        new_events = [
            mda_event.model_copy(update={"metadata": {**mda_event.metadata, "source": "event"}})
            for target in self.find_targets(img, event)
            for mda_event in self.act_on_target(target)
        ]
        if not new_events:
            return
        self._queue.extend(new_events)

    def _drain_queue(self) -> Iterator[MDAEvent]:
        if not self._queue:
            return
        # Snapshot current values of any properties queued events will modify,
        # so we can revert them after all events are done (without an extra snap).
        to_revert: dict[tuple[str, str], str] = {}
        while self._queue:
            event = self._queue.popleft()
            for dev, prop, _ in (event.properties or []):
                if (dev, prop) not in to_revert:
                    to_revert[dev, prop] = self._mmc.getProperty(dev, prop)
            yield event
        for (dev, prop), val in to_revert.items():
            self._mmc.setProperty(dev, prop, val)

    def __iter__(self) -> Iterator[MDAEvent]:
        self._queue.clear()
        self._mmc.mda.events.frameReady.connect(self._on_frame)
        try:
            for event in self.steady_state():
                yield event.model_copy(update={"metadata": {**event.metadata, "source": "steady_state"}})
                if self._act_eagerly:
                    yield from self._drain_queue()
            yield from self._drain_queue()
        finally:
            self._mmc.mda.events.frameReady.disconnect(self._on_frame)
