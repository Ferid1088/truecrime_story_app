"""The phoneme listener talks to torch/transformers directly. This builds a tiny random wav2vec2 model on
disk and runs the real PhonemeListener code path on it, so a library upgrade that changes the API
(from_pretrained, feature extractor, logits) fails here and not during a live documentary run.
Skipped when the optional torch/transformers extras are not installed."""
import json
import math
import struct
import wave

import pytest

torch = pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

from app.documentary import pronunciation as P  # noqa: E402


def _tiny_model(path):
    from transformers import Wav2Vec2Config, Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC

    vocab = {"<pad>": 0, "<s>": 1, "</s>": 2, "<unk>": 3, "|": 4, "a": 5, "b": 6, "k": 7}
    cfg = Wav2Vec2Config(
        vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1, num_attention_heads=2, intermediate_size=32,
        conv_dim=(8, 8), conv_stride=(5, 4), conv_kernel=(10, 3), num_feat_extract_layers=2,
        num_conv_pos_embeddings=4, num_conv_pos_embedding_groups=2, pad_token_id=0,
        feat_extract_norm="group", mask_time_prob=0.0,
    )
    torch.manual_seed(0)
    Wav2Vec2ForCTC(cfg).save_pretrained(path)
    Wav2Vec2FeatureExtractor(feature_size=1, sampling_rate=16000, padding_value=0.0, do_normalize=True,
                             return_attention_mask=False).save_pretrained(path)
    (path / "vocab.json").write_text(json.dumps(vocab))


def _wav(path, seconds=0.5):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(16000 * seconds))))


def test_listener_loads_a_local_model_and_returns_timed_phonemes(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    _tiny_model(model_dir)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda name, fn, **kw: str(model_dir / fn))
    wav = tmp_path / "t.wav"
    _wav(wav)
    listener = P.PhonemeListener(str(model_dir))
    out = listener.frames(str(wav))
    # the toy model has a different frame stride than the real one, so only shape is checked here
    assert out, "random weights over a tone should still emit some non-blank frames"
    for tok, start, end in out:
        assert isinstance(tok, str) and tok in "abk" and 0 <= start < end
