"""
llm_loader.py
-------------
Loads a local HuggingFace model for use as an LLM.

Forced CPU version for stability on macOS.
"""

import os
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from typing import Any

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
MODEL_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hf_model_cache")

_llm_instance = None
_tokenizer = None
_raw_pipeline = None


def load_local_llm(model_id: str = MODEL_ID) -> Any:
    global _llm_instance, _tokenizer, _raw_pipeline
    if _llm_instance is not None:
        return _llm_instance

    os.makedirs(MODEL_CACHE_DIR, exist_ok=True)

    print(f"[Loading] model '{model_id}' ...")
    if os.path.exists(os.path.join(MODEL_CACHE_DIR, "models--" + model_id.replace("/", "--"))):
        print("   (found in local cache - no download needed)")
    else:
        print(f"   First run: downloading to '{MODEL_CACHE_DIR}' ...")

    _tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        cache_dir=MODEL_CACHE_DIR,
    )

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        cache_dir=MODEL_CACHE_DIR,
        torch_dtype=torch.float32,
        device_map=None,
    )

    model = model.to("cpu")
    model.eval()

    _raw_pipeline = pipeline(
        "text-generation",
        model=model,
        tokenizer=_tokenizer,
        device=-1,
        do_sample=False,
        return_full_text=False,
    )

    _llm_instance = _raw_pipeline
    print("[OK] Model loaded on CPU.\n")
    return _llm_instance


def get_tokenizer():
    return _tokenizer


def get_raw_pipeline():
    return _raw_pipeline