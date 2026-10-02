# lms_translater

Dubbing and translation service for Teachbase courses (EN → RU), fully on open models:
Whisper → local LLM (LM Studio) → F5-TTS Russian with a cloned narrator voice; course texts and PDFs are translated too.

The service lives in [`dubservice/`](dubservice/README.md) (setup, configuration, API, limitations).
`export.py` and `tb.py` are a small read-only Teachbase exporter used while analysing the API.

Licences to check before any non-internal use: the F5-TTS Russian weights are CC-BY-NC-4.0 (non-commercial) and PyMuPDF is AGPL-3.0.
