"""
DenseNet121 1D multiclass ECG classifier service.
"""
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


class ClassificationError(Exception):
    """Exception raised when classification fails."""
    pass


class DenseLayer(nn.Module):
    def __init__(self, input_channels, growth_rate, bottleneck_size, kernel_size):
        super().__init__()
        self.use_bottleneck = bottleneck_size > 0
        self.num_bottleneck_output_filters = growth_rate * bottleneck_size
        if self.use_bottleneck:
            self.bn2 = nn.BatchNorm1d(input_channels)
            self.act2 = nn.ReLU(inplace=True)
            self.conv2 = nn.Conv1d(input_channels, self.num_bottleneck_output_filters, kernel_size=1, stride=1)
        self.bn1 = nn.BatchNorm1d(self.num_bottleneck_output_filters)
        self.act1 = nn.ReLU(inplace=True)
        self.conv1 = nn.Conv1d(
            self.num_bottleneck_output_filters,
            growth_rate,
            kernel_size=kernel_size,
            stride=1,
            dilation=1,
            padding=kernel_size // 2,
        )

    def forward(self, x):
        if self.use_bottleneck:
            x = self.bn2(x)
            x = self.act2(x)
            x = self.conv2(x)
        x = self.bn1(x)
        x = self.act1(x)
        x = self.conv1(x)
        return x


class DenseBlock(nn.ModuleDict):
    def __init__(self, num_layers, input_channels, growth_rate, kernel_size, bottleneck_size):
        super().__init__()
        for i in range(num_layers):
            self.add_module(
                f"denselayer{i}",
                DenseLayer(input_channels + i * growth_rate, growth_rate, bottleneck_size, kernel_size),
            )

    def forward(self, x):
        layer_outputs = [x]
        for _, layer in self.items():
            x = layer(x)
            layer_outputs.append(x)
            x = torch.cat(layer_outputs, dim=1)
        return x


class TransitionBlock(nn.Module):
    def __init__(self, input_channels, out_channels):
        super().__init__()
        self.bn = nn.BatchNorm1d(input_channels)
        self.act = nn.ReLU(inplace=True)
        self.conv = nn.Conv1d(input_channels, out_channels, kernel_size=1, stride=1, dilation=1)
        self.pool = nn.AvgPool1d(kernel_size=2, stride=2)

    def forward(self, x):
        x = self.bn(x)
        x = self.act(x)
        x = self.conv(x)
        x = self.pool(x)
        return x


class DenseNet1d(nn.Module):
    def __init__(
        self,
        growth_rate: int = 32,
        block_config: tuple = (6, 12, 24, 16),
        num_init_features: int = 64,
        bottleneck_size: int = 4,
        kernel_size: int = 3,
        input_channels: int = 12,
        num_classes: int = 3,
    ):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv1d(input_channels, num_init_features, kernel_size=7, stride=2, padding=3, dilation=1),
            nn.BatchNorm1d(num_init_features),
            nn.ReLU(inplace=True),
            nn.MaxPool1d(kernel_size=3, stride=2, padding=1),
        )

        self.backbone = nn.Sequential()
        num_features = num_init_features
        for i, num_layers in enumerate(block_config):
            block = DenseBlock(num_layers, num_features, growth_rate, kernel_size, bottleneck_size)
            self.backbone.add_module(f"denseblock{i}", block)
            num_features += num_layers * growth_rate
            if i != len(block_config) - 1:
                trans = TransitionBlock(num_features, num_features // 2)
                self.backbone.add_module(f"transition{i}", trans)
                num_features //= 2
        self.backbone_out_features = num_features

        self.final_bn = nn.BatchNorm1d(num_features)
        self.final_act = nn.ReLU(inplace=True)
        self.final_pool = nn.AdaptiveAvgPool1d(1)
        self.classifier = nn.Linear(num_features, num_classes)

    def forward(self, x):
        features = self.stem(x)
        features = self.backbone(features)
        features = self.final_bn(features)
        features = self.final_act(features)
        features = self.final_pool(features)
        features = features.squeeze(-1)
        return self.classifier(features)


def resolve_densenet_config(width_mult: float) -> dict:
    width = float(width_mult)
    if width >= 0.75:
        return {
            "width_mult": width,
            "growth_rate": 32,
            "num_init_features": 64,
            "block_config": (6, 12, 24, 16),
            "bottleneck_size": 4,
            "head_hidden": 256,
        }
    if width >= 0.50:
        return {
            "width_mult": width,
            "growth_rate": 16,
            "num_init_features": 32,
            "block_config": (6, 12, 24, 16),
            "bottleneck_size": 4,
            "head_hidden": 128,
        }
    if width >= 0.35:
        return {
            "width_mult": width,
            "growth_rate": 12,
            "num_init_features": 24,
            "block_config": (4, 8, 16, 8),
            "bottleneck_size": 4,
            "head_hidden": 96,
        }
    return {
        "width_mult": width,
        "growth_rate": 8,
        "num_init_features": 16,
        "block_config": (3, 6, 12, 6),
        "bottleneck_size": 4,
        "head_hidden": 64,
    }


def densenet_config_from_checkpoint(ckpt: dict, width_mult_override=None) -> dict:
    if width_mult_override is not None:
        return resolve_densenet_config(width_mult_override)
    if "densenet_growth_rate" in ckpt:
        return {
            "width_mult": float(ckpt.get("width_mult", 1.0)),
            "growth_rate": int(ckpt["densenet_growth_rate"]),
            "num_init_features": int(ckpt["densenet_num_init_features"]),
            "block_config": tuple(int(x) for x in ckpt["densenet_block_config"]),
            "bottleneck_size": int(ckpt.get("densenet_bottleneck_size", 4)),
            "head_hidden": int(ckpt["densenet_head_hidden"]),
        }
    return resolve_densenet_config(float(ckpt.get("width_mult", 1.0)))


class DenseNet121_1D(nn.Module):
    def __init__(self, dropout_head: float = 0.4, num_classes: int = 3, densenet_config: dict | None = None):
        super().__init__()
        cfg = densenet_config or resolve_densenet_config(1.0)
        self.densenet_config = cfg
        base = DenseNet1d(
            input_channels=12,
            num_classes=int(num_classes),
            growth_rate=cfg["growth_rate"],
            num_init_features=cfg["num_init_features"],
            block_config=cfg["block_config"],
            bottleneck_size=cfg["bottleneck_size"],
        )
        self.stem = base.stem
        self.backbone = base.backbone
        self.final_bn = base.final_bn
        self.final_act = base.final_act
        feat_dim = base.backbone_out_features * 2
        self.avg_pool = nn.AdaptiveAvgPool1d(1)
        self.max_pool = nn.AdaptiveMaxPool1d(1)
        self.head = nn.Sequential(
            nn.Dropout(dropout_head),
            nn.Linear(feat_dim, cfg["head_hidden"]),
            nn.SiLU(inplace=True),
            nn.Dropout(dropout_head),
            nn.Linear(cfg["head_hidden"], int(num_classes)),
        )
        self.pathology_class_idx = 1

    def _features(self, x: torch.Tensor) -> torch.Tensor:
        z = self.backbone(self.stem(x))
        z = self.final_act(self.final_bn(z))
        avg = self.avg_pool(z).squeeze(-1)
        mx = self.max_pool(z).squeeze(-1)
        return torch.cat([avg, mx], dim=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self._features(x))

    def predict(self, x: torch.Tensor) -> torch.Tensor:
        logits = self.forward(x)
        return logits[:, self.pathology_class_idx]


class DenseNetMulticlassECGClassifier:
    """Service for three-class ECG classification using DenseNet121_1D."""

    def __init__(
        self,
        model_path: str,
        threshold_rules: list[dict] | None = None,
        device: str | None = None,
        expected_samples: int = 5000,
    ):
        self.model_path = Path(model_path)
        self.threshold_rules = threshold_rules or []
        self.device = torch.device(device or "cpu")
        self.expected_samples = int(expected_samples)
        self.model = None
        self.class_names = {}
        self.label_map = {}
        self.num_classes = 0
        self.norm_mode = "none"
        self.mean = np.zeros(12, dtype=np.float32)
        self.std = np.ones(12, dtype=np.float32)
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        if not self.model_path.exists():
            raise ClassificationError(f"Модель не найдена: {self.model_path}")

        ckpt = torch.load(str(self.model_path), map_location="cpu", weights_only=False)
        if not isinstance(ckpt, dict):
            raise ClassificationError("Checkpoint должен быть словарем с model_state_dict и метаданными.")

        self.num_classes = int(ckpt.get("num_classes", len(ckpt.get("class_names", {})) or 3))
        self.class_names = {int(k): str(v) for k, v in ckpt.get("class_names", {}).items()}
        if not self.class_names:
            self.class_names = {i: f"class{i}" for i in range(self.num_classes)}
        self.label_map = {str(k): int(v) for k, v in ckpt.get("label_map", {}).items()}
        self.norm_mode = str(ckpt.get("norm_mode", "none"))
        self._validate_checkpoint_metadata(ckpt)

        model_size_mb = self.model_path.stat().st_size / (1024 * 1024)
        print(
            "DenseNet model loaded from "
            f"{self.model_path} ({model_size_mb:.1f} MB), "
            f"architecture={ckpt.get('architecture')}, "
            f"class_names={self.class_names}, norm_mode={self.norm_mode}"
        )

        densenet_cfg = densenet_config_from_checkpoint(ckpt)
        dropout_head = float(ckpt.get("dropout_head", 0.4))
        self.model = DenseNet121_1D(
            dropout_head=dropout_head,
            num_classes=self.num_classes,
            densenet_config=densenet_cfg,
        )
        self.model.pathology_class_idx = self._class_idx("PATHOLOG", default=1)

        state_dict = ckpt.get("model_state_dict", ckpt)
        missing, unexpected = self.model.load_state_dict(state_dict, strict=False)
        critical_missing = [
            k for k in missing
            if k.startswith(("stem", "backbone", "final_bn", "head"))
        ]
        if critical_missing:
            raise ClassificationError(f"Критичные веса DenseNet не загружены: {critical_missing[:8]}")
        if unexpected:
            print(f"Предупреждение: лишние ключи в checkpoint DenseNet: {list(unexpected)[:8]}")

        self.model.to(self.device)
        self.model.eval()
        self._loaded = True

    def _validate_checkpoint_metadata(self, ckpt: dict):
        expected_classes = {0: "NORM", 1: "PATHOLOG", 2: "SR"}
        architecture = ckpt.get("architecture")
        if architecture != "DenseNet121_1D":
            raise ClassificationError(
                f"Wrong model architecture in {self.model_path}: "
                f"expected DenseNet121_1D, got {architecture!r}"
            )
        if self.num_classes != 3:
            raise ClassificationError(
                f"Wrong class count in {self.model_path}: expected 3, got {self.num_classes}. "
                "Use the bundled DenseNet multiclass checkpoint for inference."
            )
        if self.class_names != expected_classes:
            raise ClassificationError(
                f"Wrong class mapping in {self.model_path}: expected {expected_classes}, got {self.class_names}"
            )
        if "model_state_dict" not in ckpt:
            raise ClassificationError(f"Checkpoint has no model_state_dict: {self.model_path}")

    def _class_idx(self, class_name: str, default: int | None = None) -> int:
        for idx, name in self.class_names.items():
            if name == class_name:
                return idx
        if default is not None:
            return default
        raise ClassificationError(f"Класс не найден в checkpoint: {class_name}")

    def _normalize(self, ecg_data: np.ndarray) -> np.ndarray:
        return ecg_data.astype(np.float32)

    def _fix_length(self, ecg_data: np.ndarray) -> np.ndarray:
        arr = ecg_data.astype(np.float32)
        if self.expected_samples > 0 and arr.shape[1] != self.expected_samples:
            fixed = np.zeros((arr.shape[0], self.expected_samples), dtype=np.float32)
            n = min(arr.shape[1], self.expected_samples)
            fixed[:, :n] = arr[:, :n]
            return fixed
        return arr

    def _resolve_rule_class(self, value) -> int:
        if isinstance(value, int):
            return value
        text = str(value)
        if text.isdigit():
            return int(text)
        for idx, name in self.class_names.items():
            if name == text:
                return idx
        raise ClassificationError(f"Класс в threshold rule не найден: {value}")

    def _apply_threshold_rules(self, argmax_label: int, probabilities: np.ndarray, rules: list[dict] | None = None) -> tuple[int, dict | None]:
        for rule in (rules if rules is not None else self.threshold_rules):
            source = self._resolve_rule_class(rule["source_class"])
            target = self._resolve_rule_class(rule["target_class"])
            threshold = float(rule["threshold"])
            target_prob = float(probabilities[target])
            if argmax_label == source and target_prob >= threshold:
                return target, {
                    "source_label": source,
                    "source_class": self.class_names[source],
                    "target_label": target,
                    "target_class": self.class_names[target],
                    "threshold": threshold,
                    "target_probability": round(target_prob, 4),
                }
        return argmax_label, None

    def predict(self, ecg_data: np.ndarray, threshold_rules: list[dict] | None = None) -> dict:
        if not self._loaded:
            self.load()
        if ecg_data.shape[0] != 12 and ecg_data.shape[1] == 12:
            ecg_data = ecg_data.T

        arr = self._normalize(self._fix_length(ecg_data))
        x = torch.from_numpy(arr.astype(np.float32)).unsqueeze(0).to(self.device)
        with torch.no_grad():
            logits = self.model(x)
            probs = torch.softmax(logits, dim=1).cpu().numpy()[0]

        argmax_label = int(np.argmax(probs))
        pred_label, applied_rule = self._apply_threshold_rules(argmax_label, probs, threshold_rules)
        pred_class = self.class_names[pred_label]
        pathology_idx = self._class_idx("PATHOLOG", default=1)
        pathology_prob = float(probs[pathology_idx])
        prediction = int(pred_class == "PATHOLOG")
        verdict = "Патология" if prediction else ("Норма" if pred_class in ("NORM", "SR") else pred_class)

        rounded_probs = {self.class_names[i]: round(float(probs[i]), 4) for i in range(self.num_classes)}
        result = {
            "class_names": {i: self.class_names[i] for i in range(self.num_classes)},
            "probabilities": rounded_probs,
            "argmax_label": argmax_label,
            "argmax_class": self.class_names[argmax_label],
            "pred_label": int(pred_label),
            "pred_class": pred_class,
            "threshold_applied": applied_rule is not None,
            "threshold_rule": applied_rule,
            "probability": round(pathology_prob, 4),
            "prediction": prediction,
            "verdict": verdict,
            "confidence": round(float(probs[pred_label]), 4),
            "norm_mode": self.norm_mode,
            "runtime_normalization": "disabled",
        }
        for idx in range(self.num_classes):
            result[f"prob_class{idx}"] = round(float(probs[idx]), 4)
        return result
