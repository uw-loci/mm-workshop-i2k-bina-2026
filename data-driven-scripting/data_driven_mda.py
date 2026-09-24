from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Generic, Generator, Iterable, Iterator, TypeVar

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
    def steady_state(self) -> Generator[MDAEvent, None, None]:
        """Yield the baseline sequence of acquisition events."""

    # -- These methods should be implemented -- #

    def find_targets(self, _img: np.ndarray, _event: MDAEvent) -> Generator[T, None, None]:
        """Yield targets found in a frame."""
        yield from () # no-op

    def act_on_target(self, _target: T) -> Generator[MDAEvent, None, None]:
        """Yield MDA events in response to a target."""
        yield from () # no-op

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

    def __iter__(self) -> Iterator[MDAEvent]:
        self._queue.clear()
        self._mmc.mda.events.frameReady.connect(self._on_frame)
        try:
            for event in self.steady_state():
                yield event.model_copy(update={"metadata": {**event.metadata, "source": "steady_state"}})
                if self._act_eagerly:
                    while self._queue:
                        yield self._queue.popleft()
            while self._queue:
                yield self._queue.popleft()
        finally:
            self._mmc.mda.events.frameReady.disconnect(self._on_frame)
