"""
Rack Occlusion Classifier - single-file version.

Classifies a rack photo as "clear" or "occluded" (view blocked by cables/clutter).

Usage:
    python rack_classifier.py path\\to\\image.jpg [more images...]   -> prints result(s) in the console

Integration (import into another codebase):
    from rack_classifier import load_model, predict_image
    from PIL import Image

    model, classes = load_model()                       # load once at startup
    img = Image.open("scan.jpg").convert("RGB")
    label, confidence, probs = predict_image(img, model, classes)
    if label == "occluded":
        ...  # flag for re-capture
"""

import argparse
import pathlib
import sys

import torch
from PIL import Image
from torch import nn
from torchvision import models, transforms

# --------------------------------------------------------------------------
# CONFIG - adjust these if paths/classes change
# --------------------------------------------------------------------------
MODEL_PATH = pathlib.Path(__file__).parent / "photo_quality_check.pth"
CLASSES = ["clear", "occluded"]
IMG_SIZE = 224
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]
# --------------------------------------------------------------------------


def _transform():
    return transforms.Compose(
        [
            transforms.Resize((IMG_SIZE, IMG_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(mean=MEAN, std=STD),
        ]
    )


def _build_architecture(num_classes):
    model = models.mobilenet_v2(weights=None)
    in_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(in_features, num_classes)
    return model


def load_model(weights_path=MODEL_PATH):
    """Load the trained model once; reuse the returned (model, classes) for every prediction."""
    weights_path = pathlib.Path(weights_path)
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Model weights not found at {weights_path}. Train first or check MODEL_PATH."
        )
    ckpt = torch.load(weights_path, map_location="cpu")
    classes = ckpt["classes"]
    model = _build_architecture(len(classes))
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, classes


def predict_image(img, model, classes):
    """Run inference on an already-opened PIL Image (RGB). Returns (label, confidence, probs_dict)."""
    x = _transform()(img).unsqueeze(0)
    with torch.no_grad():
        probs = torch.softmax(model(x), dim=1)[0]
    idx = int(probs.argmax())
    return classes[idx], probs[idx].item(), {c: probs[i].item() for i, c in enumerate(classes)}


def predict_path(image_path, model, classes):
    img = Image.open(image_path).convert("RGB")
    return predict_image(img, model, classes)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Rack occlusion classifier")
    parser.add_argument("images", nargs="*", help="Image path(s) to classify")
    parser.add_argument("--verbose", action="store_true", help="Show confidence breakdown too")
    args = parser.parse_args()

    if not args.images:
        parser.print_help()
        sys.exit(1)

    model, classes = load_model()
    for image_path in args.images:
        try:
            label, confidence, probs = predict_path(image_path, model, classes)
        except Exception as e:
            print(f"{image_path}: ERROR - {e}")
            continue
        print(f"{image_path} -> {label}")
        if args.verbose:
            breakdown = "  ".join(f"{c}: {p * 100:.1f}%" for c, p in probs.items())
            print(f"    ({breakdown})")


if __name__ == "__main__":
    main()
