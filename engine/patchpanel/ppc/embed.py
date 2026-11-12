"""DINOv2 tile encoder.

DINOv2 over CLIP: the classes here separate on fine-grained shape and colour
(bayonet vs threaded coupling, green APC vs beige UPC ferrules), which is what
self-supervised ViT features are strong at. CLIP leans semantic and tends to
collapse all of these to "network equipment".
"""

import numpy as np
import timm
import torch

MODELS = {
    "s": "vit_small_patch14_dinov2.lvd142m",   # 384-d, fast
    "b": "vit_base_patch14_dinov2.lvd142m",    # 768-d, stronger
}


class Encoder:
    def __init__(self, size="s", img_size=224, device="cpu", batch_size=32):
        name = MODELS[size]
        # Native DINOv2 input is 518px; at 224 we run ~5x faster on CPU with
        # little loss, because our tiles are already cropped to port scale.
        self.model = timm.create_model(
            name, pretrained=True, num_classes=0,
            img_size=img_size, dynamic_img_size=True,
        )
        self.model.eval().to(device)
        cfg = timm.data.resolve_model_data_config(self.model)
        self.mean = torch.tensor(cfg["mean"]).view(1, 3, 1, 1)
        self.std = torch.tensor(cfg["std"]).view(1, 3, 1, 1)
        self.device = device
        self.batch_size = batch_size
        self.dim = self.model.num_features
        self.name = "%s@%d" % (name, img_size)

    @torch.no_grad()
    def encode(self, pil_tiles):
        """Encode PIL tiles -> (N, dim) L2-normalised float32 array."""
        if not pil_tiles:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = []
        for i in range(0, len(pil_tiles), self.batch_size):
            chunk = pil_tiles[i:i + self.batch_size]
            x = np.stack([np.asarray(t, dtype=np.float32) / 255.0
                          for t in chunk])
            x = torch.from_numpy(x).permute(0, 3, 1, 2)
            x = ((x - self.mean) / self.std).to(self.device)
            f = self.model(x).float().cpu()
            out.append(torch.nn.functional.normalize(f, dim=1).numpy())
        return np.concatenate(out).astype(np.float32)
