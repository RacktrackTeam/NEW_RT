"""switch-ocr: free, production-ready text detection & OCR for photos of
network equipment — tuned for tiny, blurry and low-contrast text.

Public API:
    SwitchTextReader  — main entry point
    OCRConfig         — tunables (+ .fast() / .accurate() presets)
    OCRResult, TextDetection — typed results
"""
from .config import OCRConfig
from .identify import DeviceID, identify_device
from .reader import SwitchTextReader
from .types import OCRResult, TextDetection

__version__ = "1.5.0"
__all__ = [
    "SwitchTextReader", "OCRConfig", "OCRResult", "TextDetection",
    "DeviceID", "identify_device", "__version__",
]
