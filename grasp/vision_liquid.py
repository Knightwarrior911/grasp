"""Liquid AI LFM2.5-VL-Extract vision backend for Grasp.

On-device structured extraction from images. Complements (does not replace) the
MiniMax VLM backend. Toggle with GRASP_VLM_BACKEND=liquid.

Model registry:
  450M  -> LiquidAI/LFM2.5-VL-450M-Extract   (~1 GB RAM, bfloat16)
  1.6B  -> LiquidAI/LFM2.5-VL-1.6B-Extract   (~3.2 GB RAM, bfloat16)

Env vars:
  GRASP_LIQUID_MODEL   — override the HF model id (default: 450M)
  GRASP_VLM_BACKEND    — "minimax" (default) | "liquid"

Usage:
  from grasp.vision_liquid import LiquidVision
  lv = LiquidVision()
  json_str = lv.extract(png_bytes, "color: dominant color\\ncount: number of objects")
  answer   = lv.vlm(png_bytes, "Describe this screenshot")
"""

from __future__ import annotations

import io
import logging
import os
import warnings
from typing import Optional

log = logging.getLogger("grasp.vision_liquid")
# Suppress noisy transformers/tokenizer warnings during inference
logging.getLogger("transformers").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*clean_up_tokenization_spaces.*")
warnings.filterwarnings("ignore", message=".*return_dict.*")

_DEFAULT_MODEL = "LiquidAI/LFM2.5-VL-450M-Extract"
_MODEL_ENV = "GRASP_LIQUID_MODEL"

# Singleton _Vision instance (lazy-loaded)
_instance: Optional["_Vision"] = None


class LiquidVisionError(RuntimeError):
    pass


class _Vision:
    """Holds the loaded model + processor. Thread-safe for inference only."""

    def __init__(self, model_id: str):
        import torch
        from transformers import AutoModelForImageTextToText, AutoTokenizer
        from transformers.models.lfm2_vl import Lfm2VlImageProcessor, Lfm2VlProcessor

        log.info("Loading Liquid model %s …", model_id)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.bfloat16 if self.device == "cuda" else torch.float32

        # Load model — trust_remote_code picks up Lfm2VlForConditionalGeneration
        self.model = AutoModelForImageTextToText.from_pretrained(
            model_id,
            device_map="auto" if self.device == "cuda" else None,
            dtype=self.dtype,
            trust_remote_code=True,
        )
        if self.device == "cpu":
            self.model = self.model.to(self.device)
        self.model.eval()

        # Load processor manually — AutoProcessor can't find Lfm2VlProcessor
        # because it's not in PROCESSER_MAPPING yet (transformers 5.10.x)
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        image_processor = Lfm2VlImageProcessor.from_pretrained(model_id, trust_remote_code=True)

        # Load chat template from model repo (Lfm2VlProcessor doesn't ship one)
        from huggingface_hub import hf_hub_download
        import os
        chat_template_path = hf_hub_download(model_id, "chat_template.jinja")
        chat_template = open(chat_template_path, encoding="utf-8").read() if os.path.exists(chat_template_path) else None

        self.processor = Lfm2VlProcessor(
            image_processor=image_processor,
            tokenizer=tokenizer,
            chat_template=chat_template,
        )
        log.info("Liquid model ready on %s (%s)", self.device, self.dtype)

    def _run(self, image, text: str, max_new_tokens: int = 512) -> str:
        """Core inference: PIL image + text prompt → generated text."""
        import torch

        # Lfm2VlProcessor expects (images=..., text=...) not apply_chat_template
        # We tokenize text ourselves with the chat template, then pass to processor
        messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": text}]}]
        prompt_text = self.processor.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False,
        )

        inputs = self.processor(
            images=image,
            text=prompt_text,
            return_tensors="pt",
            return_dict=True,
        )
        # Move tensors to device
        inputs = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

        input_len = inputs["input_ids"].shape[1] if "input_ids" in inputs else 0
        response = self.processor.batch_decode(
            outputs[:, input_len:], skip_special_tokens=True,
        )[0]
        return response.strip()

    def extract(self, png_bytes: bytes, fields_yaml: str) -> str:
        """Structured extraction: image + YAML fields → JSON string."""
        from PIL import Image

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        system_prompt = (
            f"Extract the following from the image:\n\n{fields_yaml}\n\n"
            "Respond with only a JSON object. Do not include any text outside the JSON."
        )
        return self._run(image, system_prompt)

    def vlm(self, png_bytes: bytes, question: str) -> str:
        """General VLM: image + question → text answer (same interface as MiniMax)."""
        from PIL import Image

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        return self._run(image, question)


def _get() -> _Vision:
    global _instance
    if _instance is None:
        model_id = os.environ.get(_MODEL_ENV, _DEFAULT_MODEL)
        try:
            _instance = _Vision(model_id)
        except ImportError as e:
            raise LiquidVisionError(
                f"Missing dependency for Liquid backend: {e}. "
                "pip install transformers accelerate torch"
            ) from e
        except Exception as e:
            raise LiquidVisionError(f"Failed to load Liquid model {model_id}: {e}") from e
    return _instance


def is_available() -> bool:
    """True if transformers + torch are importable (model not yet downloaded)."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def extract(png_bytes: bytes, fields_yaml: str) -> str:
    """Convenience: structured extraction from PNG bytes."""
    return _get().extract(png_bytes, fields_yaml)


def vlm(png_bytes: bytes, question: str) -> str:
    """Convenience: general VLM question from PNG bytes. Same signature as vision.vlm()."""
    return _get().vlm(png_bytes, question)
