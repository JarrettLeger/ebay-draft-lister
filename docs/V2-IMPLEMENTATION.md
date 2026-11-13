# V2 foundation delivery

Built on `feature/openai-v2-ui` from master commit `6a16a15689efa10cfd11f8a970c2db13b7103768`. The original script and master are preserved.

Implemented: local FastAPI server; SQLite inventory; React/TypeScript UI; file import; manual front/back pairing, separation and swapping; OpenAI Responses structured vision; editable fields and optimized titles; warnings and review checklist; explicit manual price; full-resolution Cloudinary JPEG conversion including HEIC; bounded retries/timeouts; successful-side upload caching; processing conflict protection; restart recovery; guarded eBay CSV export; secrets/configuration; Windows setup and launcher; setup/recovery documentation.

Validation: 13 pytest tests cover persistence, pairing, import failure cleanup, origin/host checks, CSV gates and legacy format, price validation, partial upload recovery, extraction contract and manual pricing, restart recovery, title optimization, HEIC/JPEG behavior, processing conflicts, and Cloudinary retry payloads. TypeScript checking and Vite production build pass. Headless Edge exercised the actual local UI: import, edit, save, refresh persistence, export blocking and 600px layout. An empty-inventory screenshot is in `v2-ui.png`.

External APIs were mocked in tests. No API credits were spent, no photos were uploaded remotely, and no eBay drafts were created during validation. The Windows command scripts were inspected; this environment uses bundled runtimes, so clean-machine double-click setup remains to be tested. TestClient emits one upstream deprecation warning about httpx.

Remaining: native Windows installer/window, background batch queue/cancellation, automatic pairing suggestions, legacy sidecar/inventory import, richer QC/audit/search, schema migrations, configurable shop templates and real-account acceptance checks. The legacy HTML QC/rename/repair commands are retained only in the original script; the UI replaces QC with its inventory checklist. Token usage is persisted, but dollar cost estimation is deferred. Repeated export can create duplicate drafts if the same file/cards are uploaded to eBay twice.
