"""
XAI (Explainable AI) service for ECG classification.

Implements Integrated Gradients and simple saliency for model interpretability.
"""
import sys
from pathlib import Path

import numpy as np
import torch

# Add utils to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from utils.ecg_helpers import normalize_with_zero_mask
from utils.signal_processing import CHANNEL_NAMES as LEAD_NAMES  # noqa: E402


class ECGExplainer:
    """Explainer for ECG classification model using Integrated Gradients."""

    def __init__(self, model, mean: np.ndarray, std: np.ndarray, device: torch.device = None):
        """
        Initialize explainer.

        Args:
            model: Loaded PyTorch ECG model
            mean: Normalization mean [12]
            std: Normalization std [12]
            device: PyTorch device
        """
        self.model = model
        self.mean = mean
        self.std = std
        self.device = device or torch.device('cpu')

    def integrated_gradients(self, ecg_data: np.ndarray,
                             baseline: np.ndarray = None,
                             n_steps: int = 50) -> np.ndarray:
        """
        Compute attributions using Integrated Gradients.

        Integrated Gradients is a gradient-based attribution method that
        satisfies axioms of sensitivity and implementation invariance.

        Args:
            ecg_data: Input ECG data [12, T]
            baseline: Baseline input (default: zeros)
            n_steps: Number of interpolation steps

        Returns:
            Saliency map [12, T] - higher values = more important
        """
        # Ensure correct shape
        if ecg_data.shape[0] != 12 and ecg_data.shape[1] == 12:
            ecg_data = ecg_data.T

        # Create baseline (zeros if not provided)
        if baseline is None:
            baseline = np.zeros_like(ecg_data)

        # Convert to tensors and normalize
        x_input = torch.from_numpy(ecg_data.astype(np.float32))
        x_baseline = torch.from_numpy(baseline.astype(np.float32))

        x_input = normalize_with_zero_mask(x_input, self.mean, self.std)
        x_baseline = normalize_with_zero_mask(x_baseline, self.mean, self.std)

        # Interpolation path
        alphas = torch.linspace(0, 1, n_steps + 1)
        gradients = []

        self.model.eval()

        for alpha in alphas:
            # Interpolate between baseline and input
            x_interp = x_baseline + alpha * (x_input - x_baseline)
            x_interp = x_interp.unsqueeze(0).to(self.device)
            x_interp.requires_grad_(True)

            # Forward pass
            logits = self.model.predict(x_interp)
            output = torch.sigmoid(logits)

            # Backward pass
            self.model.zero_grad()
            output.backward()

            grad = x_interp.grad.detach().cpu().squeeze(0).numpy()
            gradients.append(grad)

        # Approximate integral using trapezoidal rule
        gradients = np.array(gradients)
        avg_gradients = np.mean(gradients[:-1] + gradients[1:], axis=0) / 2

        # Compute attributions: (input - baseline) * avg_gradients
        x_input_np = x_input.numpy()
        x_baseline_np = x_baseline.numpy()
        attributions = (x_input_np - x_baseline_np) * avg_gradients

        return attributions

    def compute_saliency(self, ecg_data: np.ndarray) -> np.ndarray:
        """
        Compute simple gradient-based saliency.

        Faster than Integrated Gradients but less accurate.

        Args:
            ecg_data: Input ECG data [12, T]

        Returns:
            Saliency map [12, T]
        """
        if ecg_data.shape[0] != 12 and ecg_data.shape[1] == 12:
            ecg_data = ecg_data.T

        x = torch.from_numpy(ecg_data.astype(np.float32))
        x = normalize_with_zero_mask(x, self.mean, self.std)
        x = x.unsqueeze(0).to(self.device)
        x.requires_grad_(True)

        self.model.eval()
        logits = self.model.predict(x)
        output = torch.sigmoid(logits)

        self.model.zero_grad()
        output.backward()

        saliency = x.grad.detach().cpu().squeeze(0).numpy()
        return np.abs(saliency)

    def rank_leads(self, saliency_map: np.ndarray, top_k: int = 3) -> list[dict]:
        """
        Rank leads by their contribution to classification.

        Args:
            saliency_map: Attribution/saliency map [12, T]
            top_k: Number of top leads to return

        Returns:
            List of dicts with lead info, sorted by importance:
                {'lead_idx': int, 'lead_name': str, 'contribution': float, 'rank': int}
        """
        # Sum absolute attributions per lead
        contributions = np.sum(np.abs(saliency_map), axis=1)

        # Normalize to percentages
        total = np.sum(contributions)
        if total > 0:
            contributions = contributions / total * 100

        # Sort by contribution
        ranked_indices = np.argsort(-contributions)

        results = []
        for rank, idx in enumerate(ranked_indices[:top_k]):
            results.append({
                'lead_idx': int(idx),
                'lead_name': LEAD_NAMES[idx],
                'contribution': round(float(contributions[idx]), 2),
                'rank': rank + 1
            })

        return results

    def get_temporal_importance(self, saliency_map: np.ndarray,
                                window_size: int = 50) -> dict:
        """
        Analyze temporal importance patterns.

        Args:
            saliency_map: Attribution map [12, T]
            window_size: Size of sliding window for analysis

        Returns:
            dict with temporal analysis:
                'peak_positions': list of important time points
                'importance_curve': averaged importance over time
        """
        # Average across leads
        temporal = np.mean(np.abs(saliency_map), axis=0)

        # Smooth with moving average
        if len(temporal) > window_size:
            kernel = np.ones(window_size) / window_size
            smoothed = np.convolve(temporal, kernel, mode='same')
        else:
            smoothed = temporal

        # Find peaks (local maxima)
        peaks = []
        for i in range(1, len(smoothed) - 1):
            if smoothed[i] > smoothed[i-1] and smoothed[i] > smoothed[i+1]:
                # Check if peak is significant (above mean)
                if smoothed[i] > np.mean(smoothed):
                    peaks.append(i)

        return {
            'peak_positions': peaks,
            'importance_curve': smoothed.tolist(),
            'max_importance_idx': int(np.argmax(smoothed)),
            'mean_importance': float(np.mean(temporal)),
            'std_importance': float(np.std(temporal))
        }

    def explain(self, ecg_data: np.ndarray, method: str = 'integrated_gradients',
                n_steps: int = 50) -> dict:
        """
        Full explanation of model prediction.

        Args:
            ecg_data: Input ECG data [12, T]
            method: 'integrated_gradients' or 'saliency'
            n_steps: Number of steps for integrated gradients

        Returns:
            dict with:
                'saliency_map': attribution map [12, T]
                'top_leads': list of top contributing leads
                'temporal_analysis': temporal importance info
        """
        if method == 'integrated_gradients':
            saliency = self.integrated_gradients(ecg_data, n_steps=n_steps)
        else:
            saliency = self.compute_saliency(ecg_data)

        top_leads = self.rank_leads(saliency)
        temporal = self.get_temporal_importance(saliency)

        return {
            'saliency_map': saliency,
            'top_leads': top_leads,
            'temporal_analysis': temporal,
            'method': method
        }
