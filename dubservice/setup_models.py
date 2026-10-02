"""Download the F5-TTS Russian weights (CC-BY-NC-4.0, non-commercial use only).
python -m dubservice.setup_models"""
import shutil
from pathlib import Path

from huggingface_hub import hf_hub_download

from . import config

REPO = "Misha24-10/F5-TTS_RUSSIAN"
FILES = {"F5TTS_v1_Base_v2/model_last_inference.safetensors": config.F5_CKPT,
         "F5TTS_v1_Base/vocab.txt": config.F5_VOCAB}


def main():
    for remote, local in FILES.items():
        dest = Path(local)
        if dest.exists():
            print("have", dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        print("downloading", remote)
        shutil.copy(hf_hub_download(REPO, remote), dest)
    print("models ready in", config.MODELS_DIR)


if __name__ == "__main__":
    main()
