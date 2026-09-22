from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Generic, Generator, Iterable, Iterator, TypeVar

from pymmcore_plus import CMMCorePlus
from useq import MDAEvent

if TYPE_CHECKING:
    import numpy as np

T = TypeVar("T")


class DataDrivenMDA(Iterable[MDAEvent], Generic[T], ABC):
    """Represents a data-driven multi-dimensional acquisition (MDA) sequence."""

    # -- These methods must be implemented -- #

    @abstractmethod
    def steady_state(self) -> Generator[MDAEvent, None, None]:
        """Yield the baseline sequence of acquisition events."""

    # -- These methods should be implemented -- #

    def find_events(self, _img: np.ndarray, _event: MDAEvent) -> Generator[T, None, None]:
        """Yield detected items of interest from a frame."""
        yield from () # no-op

    def actuate_event(self, _item: T) -> Generator[MDAEvent, None, None]:
        """Yield MDA events to acquire in response to a detected item."""
        yield from () # no-op

    # -- These methods are internal details -- #

    def __init__(self, mmc: CMMCorePlus, *, image_pois_eagerly: bool = False) -> None:
        self._mmc = mmc
        self._queue: deque[MDAEvent] = deque()
        self._image_pois_eagerly = image_pois_eagerly

    def _on_frame(self, img: np.ndarray, event: MDAEvent, _: dict) -> None:
        if event.metadata.get("source") != "steady_state":
            return
        new_events = [
            mda_event.model_copy(update={"metadata": {**mda_event.metadata, "source": "event"}})
            for item in self.find_events(img, event)
            for mda_event in self.actuate_event(item)
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
                if self._image_pois_eagerly:
                    while self._queue:
                        yield self._queue.popleft()
            while self._queue:
                yield self._queue.popleft()
        finally:
            self._mmc.mda.events.frameReady.disconnect(self._on_frame)
