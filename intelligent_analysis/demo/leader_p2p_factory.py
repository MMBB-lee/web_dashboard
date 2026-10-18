"""CPU experimental factory for the separately supplied, reviewed team bundle."""


def build():
    from types import SimpleNamespace

    import torch
    from models import build_model

    if torch.cuda.is_available():
        raise RuntimeError("Team AnchorPoints selects CUDA globally; unmodified CPU factory is not validated on CUDA hosts")
    return build_model(SimpleNamespace(backbone="vgg16_bn", row=2, line=2), training=False)
