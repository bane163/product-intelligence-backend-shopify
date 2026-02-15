# Document -> Collabora -> PDF -> PNG -> Agent workflow

Quick notes to run the example workflow included in `ai/`.

Prerequisites:
- A Collabora Online (CODE) instance reachable at `COLLABORA_URL` (default: `http://localhost:9980`).
- An agent backend compatible with `agent_framework.openai.OpenAIChatClient` configured via env vars:
  - `OLLAMA_API_KEY`
  - `OLLAMA_CLOUD_URL` (optional)
  - `OLLAMA_MODEL_ID` (optional)
- Python dependencies listed in `requirements_workflow.txt`.

Quick test (local):

1. Install dependencies (in your venv):

```bash
pip install -r ai/requirements_workflow.txt
```

2. Run the example against a local .xlsx file:

```bash
python -m ai.run_workflow_example path/to/sample.xlsx
```

3. To call the endpoint from the running FastAPI app (if your backend is running):

Use curl (multipart form upload):

```bash
curl -F "file=@sample.xlsx" -F "prompt=Summarize the spreadsheet" http://localhost:8000/microsoft/agent/excel
```

Notes and caveats:
- This is a demo integration. Passing images as base64 inside a text prompt is a simplification; for true multimodal workflows use a model/client that supports image inputs or attach images via a supported channel.
- Collabora's convert endpoint path may vary depending on deployment and version. The code uses `/lool/convert-to/pdf` which works for common CODE builds.
- PyMuPDF (pymupdf) is used for PDF->PNG conversion and is pure-Python wheel on many platforms.
