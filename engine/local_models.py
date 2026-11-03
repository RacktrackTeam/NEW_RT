"""Every pretrained weight the engine loads lives inside this folder, and no model load may reach the internet.

Importing this module, before torch, open_clip, timm, huggingface_hub, ultralytics or easyocr are imported, points their
caches at models/ and turns their network lookups off (unless the caller already set the switches). open_clip's
pretrained="openai" and timm's pretrained=True then read the local Hugging Face copy unchanged. The files:
    models/torch/hub/facebookresearch_dinov2_main + checkpoints/dinov2_vitl14_pretrain.pth   DINOv2 ViT-L/14 (teammate 1 v2)
    models/huggingface/hub/models--timm--vit_base_patch32_clip_224.openai                     CLIP ViT-B/32 openai (open_clip)
    models/huggingface/hub/models--timm--vit_base_patch14_dinov2.lvd142m                      DINOv2 ViT-B/14 (timm)
    models/easyocr/model/{craft_mlt_25k.pth, english_g2.pth}                                  easyocr detector and reader
models/MANIFEST.txt lists their checksums. engine.sh sets the same switches.
"""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
TORCH_HOME = MODELS / "torch"
HF_HOME = MODELS / "huggingface"
EASYOCR_HOME = MODELS / "easyocr"

for _k, _v in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"), ("HF_HUB_DISABLE_TELEMETRY", "1"),
               ("HF_HOME", str(HF_HOME)), ("TORCH_HOME", str(TORCH_HOME)), ("YOLO_OFFLINE", "1"),
               ("YOLO_CONFIG_DIR", str(ROOT / "_cache" / "ultralytics")), ("EASYOCR_MODULE_PATH", str(EASYOCR_HOME))):
    os.environ.setdefault(_k, _v)
pathlib.Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)


def dinov2_hub(model_name):
    """torch.hub DINOv2 from the local copy of facebookresearch/dinov2 (source="local"): no request to github.com;
    its checkpoint is read from models/torch/hub/checkpoints."""
    import torch
    hub = TORCH_HOME / "hub"
    torch.hub.set_dir(str(hub))
    return torch.hub.load(str(hub / "facebookresearch_dinov2_main"), model_name, source="local")
