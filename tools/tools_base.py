# tool_base.py (New file)
from abc import ABC, abstractmethod

class Tool(ABC):
    """ Base class for defining tools dynamically. """

    @abstractmethod
    def execute(self, **kwargs):
        """ Function that each tool must implement. """
        pass
