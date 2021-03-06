from abc import ABC, abstractmethod
from inspect import isabstract
from pathlib import Path

import treelog as log

from typing import Iterable, Tuple
from ..typing import StepData

from ..filters import Source
from ..fields import Field

from ..util import subclasses


class Reader(Source):

    reader_name: str

    @classmethod
    def applicable(cls, filename: Path) -> bool:
        """Return true if the class can handle files of the given type."""
        return False

    @staticmethod
    def find_applicable(filename: Path) -> type:
        """Return a reader subclass that can handle files of the given type."""
        for cls in subclasses(Reader, invert=True):
            if isabstract(cls):
                continue
            if cls.applicable(filename):
                log.info(f"Using reader: {cls.reader_name}")
                return cls
            else:
                log.debug(f"Rejecting reader: {cls.reader_name}")
        raise TypeError(f"Unable to find any applicable readers for {filename}")

    def validate(self):
        """Raise an error if config options are invalid."""
        pass

    @abstractmethod
    def __enter__(self):
        pass

    @abstractmethod
    def __exit__(self, *args):
        pass
