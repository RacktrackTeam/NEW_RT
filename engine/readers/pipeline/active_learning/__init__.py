"""Active-learning memory layer for RackTrack.

Wraps the standalone Active Learning CDP modules so they can be invoked from
Node via subprocess. Stores per-model corrections (cable, device, port) plus
a verified port-layout store that bypasses the YOLO model on re-uploads.
"""
