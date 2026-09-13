from enum import Enum
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict


class Engine(Enum):
    VIDEOSUBFINDER = "videosubfinder"

    def __str__(self):
        return self.value

    @classmethod
    def from_string(cls, value: str):
        for engine in cls:
            if engine.value.lower() == value.lower():
                return engine
        raise ValueError(f"Unknown engine: {value}. Available: {[e.value for e in cls]}")

class OCREngine(ABC):
    """Abstract base class for OCR engines."""
    
    @abstractmethod
    def __call__(self, images_dir: Path) -> Dict[str, str]:
        pass
    
    @property
    @abstractmethod
    def engine_name(self) -> str:
        pass

class OCREngineType(Enum):
    GGLENS = "gglens"
    BING = "bing"
    
    @classmethod
    def from_string(cls, value: str):
        clean_value = value.lower().replace("_", "").replace("-", "")
        for engine in cls:
            if engine.value.lower().replace("_", "").replace("-", "") == clean_value:
                return engine
        raise ValueError(f"Unknown OCR engine: {value}. Available: {[e.value for e in cls]}")