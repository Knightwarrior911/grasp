"""Grasp - exhaustive Windows computer-use control surface for AI agents."""

from .computer import Computer, SafetyError
from .keys import UnknownKeyError
from .scale import Scaler

__version__ = "0.1.0"
__all__ = ["Computer", "Scaler", "SafetyError", "UnknownKeyError"]
