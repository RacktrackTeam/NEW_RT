"""Rack device logic (no device model): find 1U units, group occupied neighbours into devices, report each device's size in U."""
from .pipeline import THR, W_OLD, DevicePipeline, group

__all__ = ["DevicePipeline", "group", "THR", "W_OLD"]
