"""
Google Lens Protocol Buffers bindings compiled from Chromium lens/proto/v1.
Using official google.protobuf with C++ (upb) backend for high-speed parsing.
"""

from .lens_overlay_server_pb2 import LensOverlayServerRequest, LensOverlayServerResponse
from .lens_overlay_platform_pb2 import PLATFORM_WEB
from .lens_overlay_surface_pb2 import SURFACE_CHROMIUM
from .lens_overlay_filters_pb2 import AUTO_FILTER

__all__ = [
    "LensOverlayServerRequest",
    "LensOverlayServerResponse",
    "PLATFORM_WEB",
    "SURFACE_CHROMIUM",
    "AUTO_FILTER",
]
