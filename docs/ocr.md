# OCR for lab documents

Uploaded lab documents are read by the OCR worker (`backend/worker.py`); the engine is set by
`OCR_ENGINE` in `.env`. Whatever the engine reads is saved **unconfirmed**: the patient reviews
every value and nothing is used by the models until they confirm it.

| `OCR_ENGINE`   | Reads                                                     | Needs                                  |
|----------------|-----------------------------------------------------------|----------------------------------------|
| `none`         | nothing; the patient types the values in (default)        | nothing                                |
| `pdf_text`     | PDFs that contain text (lab portal PDFs). Photos and scanned PDFs get "please type the values in". | nothing extra (pypdfium2 is a normal dependency) |
| `paddleocr_vl` | photos and scanned PDFs (PaddleOCR-VL); text PDFs are still read directly | NVIDIA GPU + the install step below |

Most of the group only needs `none` or `pdf_text`: a normal `uv sync` is enough.

## Installing PaddleOCR-VL (only on the machine that runs OCR)

Tested on Fedora, RTX 4050 Laptop (6 GB), NVIDIA driver with CUDA 12.6+ support, Python 3.11
(the guide supports 3.9–3.13). Official guide:
<https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html>

PaddleOCR is **not** in `pyproject.toml` on purpose. `uv.lock` is one lock for every platform
and every extra together: adding it (even as an optional extra) would put ~65 packages and
Paddle's own package index into everyone's lock, force PyYAML down to 6.0.2 for everyone
(paddlex pins it), and break `uv lock` on macOS (no `paddlepaddle-gpu` wheels). So it is a
separate step, into the same `.venv`:

```bash
# 1. GPU build of PaddlePaddle (CUDA 12.6). Never install the CPU build "paddlepaddle" next to it.
export UV_HTTP_TIMEOUT=600   # the Paddle index is slow from the Philippines
uv pip install "paddlepaddle-gpu==3.2.1" \
    --index https://www.paddlepaddle.org.cn/packages/stable/cu126/ --index-strategy unsafe-best-match

# 2. PaddleOCR with the document parser. Keep our numpy (otherwise it is moved down to 2.3).
uv pip install "paddleocr[doc-parser]" "numpy>=2.4.6"

# 3. Check
nvidia-smi
uv run --no-sync python -c "import paddle, paddleocr; paddle.utils.run_check(); print(paddle.__version__, paddleocr.__version__)"
```

Then in **your** `.env` only (`.env.example` stays `none`):

```
OCR_ENGINE=paddleocr_vl
OCR_DEVICE=gpu:0
```

The model files (about 2 GB) are downloaded on the first document into `~/.paddlex/`.

**Keep it installed:** `uv sync` removes packages that are not in `uv.lock`, so on this machine
use `uv sync --inexact` (or just `uv run`, which syncs without removing). If PaddleOCR is gone,
the app refuses to start with `OCR_ENGINE=paddleocr_vl` and tells you to install it.

## Running the worker

- **Redis off** (development): OCR runs in the API process after the upload response. The model
  is loaded once, on the first document, and kept.
- **Redis on**: run the worker as
  ```bash
  uv run rq worker --worker-class rq.SimpleWorker ocr
  ```
  The default RQ worker forks a new process for every job, which would load the 2 GB model again
  for every document (and CUDA does not survive a fork).

One document is read at a time (a lock around the GPU).

## Privacy

- Photos lose their hidden metadata (GPS, camera) before they are stored (`backend/image_privacy.py`).
- The decrypted file is never written to disk for OCR: pages are rendered in memory.
- Engine error text and document text are never logged; only the error type is.
