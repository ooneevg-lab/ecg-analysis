"""Small ECG tensor helpers used by runtime services."""

import torch


def normalize_with_zero_mask(x, mean, std):
    """
    Normalize each channel and keep fully zero channels at zero.

    Args:
        x: torch.Tensor [12, T]
        mean: np.ndarray [12]
        std: np.ndarray [12]
    """
    zero_mask = (x == 0).all(dim=-1)
    mean_t = torch.from_numpy(mean).float().unsqueeze(-1)
    std_t = torch.from_numpy(std).float().unsqueeze(-1)
    x = (x - mean_t) / (std_t + 1e-8)
    x[zero_mask] = 0.0
    return x
