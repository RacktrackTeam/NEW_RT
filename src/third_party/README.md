# Third-party code kept inside this folder

- DEIMv2/ - the DEIMv2 detector code (https://github.com/Intellindust-AI-Lab/DEIMv2, commit e658301, Apache 2.0, LICENSE.md
  inside), which the device detector (src/device_model.py, weights/device_detector_deimv2.pth) needs to build its model.
  Copied on 3 Nov 2026 from the clone the engine used to read outside this folder. Left out: .git, figures, the example
  picture and the notebook. One change: engine/core/_config.py and engine/solver/det_engine.py import tensorboard's
  SummaryWriter only when it is installed (it is used for training only).
