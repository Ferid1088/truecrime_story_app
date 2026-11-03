"""Converts the Persian Whisper fine-tune (nezamisafa/whisper-persian-v4) to a CTranslate2 model for faster-whisper on this machine:
python scripts/convert_whisper_persian.py [--quantization int8|float32|int8_float32] [--out DIR]. The converter of the installed ctranslate2 passes a
`dtype` argument that the installed transformers does not know, so the load is wrapped to say `torch_dtype`; the tokenizer and the feature extractor
are written next to the model."""

from __future__ import annotations

import argparse
from pathlib import Path

import ctranslate2.converters.transformers as T
import torch
from transformers import AutoProcessor

MODEL = "nezamisafa/whisper-persian-v4"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quantization", default="int8")
    parser.add_argument("--out", type=Path, default=Path("data/models/whisper-persian-v4-ct2"))
    args = parser.parse_args()
    original = T.TransformersConverter.load_model

    def load_model(self, model_class, model_name_or_path, **kwargs):
        if "dtype" in kwargs:
            kwargs["torch_dtype"] = kwargs.pop("dtype")
        return original(self, model_class, model_name_or_path, **kwargs)

    T.TransformersConverter.load_model = load_model
    converter = T.TransformersConverter(MODEL)
    print(converter.convert(str(args.out), quantization=args.quantization, force=True), "torch", torch.__version__)
    processor = AutoProcessor.from_pretrained(MODEL)            # the repository has no tokenizer.json: it is written from the vocabulary files
    processor.save_pretrained(str(args.out))


if __name__ == "__main__":
    main()
