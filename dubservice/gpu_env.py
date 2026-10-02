"""Import first in every GPU stage: makes CUDA 12 DLLs shipped with torch visible to ctranslate2 (faster-whisper) on Windows,
and tolerates ctranslate2 4.7's lookup of a nonexistent ROCm directory."""
import glob
import os

_add = getattr(os, "add_dll_directory", None)
if _add:
    def _safe(path):
        try:
            return _add(path)
        except (FileNotFoundError, OSError):
            return None
    os.add_dll_directory = _safe

try:
    import torch
    for d in glob.glob(os.path.join(os.path.dirname(torch.__file__), "lib")):
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
        if _add:
            _safe(d)
except ImportError:
    pass
