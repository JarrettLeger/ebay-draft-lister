# eBay Draft Lister v2 — Windows setup

## Start here

1. Install Python 3.12+ (include the `py` launcher) and Node.js 22 LTS+ (includes npm).
2. Download or check out `feature/openai-v2-ui`. Double-click `setup-windows.cmd`. Internet access is required for dependency downloads. Setup creates a project-local Python environment and builds the UI.
3. Edit `.env` in this folder. Leave connections blank to use photo import, pairing, and manual editing offline. Set `OPENAI_API_KEY` for identification; set `CLOUDINARY_CLOUD_NAME` and an unsigned `CLOUDINARY_UPLOAD_PRESET` for photo hosting. Keep the preset restricted to image uploads in Cloudinary. Never commit `.env`.
4. Double-click `launch-windows.cmd`. The interface opens at http://127.0.0.1:8765. Leave the server window open; press Ctrl+C in that window to stop. Restart after changing `.env`. No Node server is needed after setup.

This foundation is a desktop-style browser application served on your computer. A native window/installer is not included. Use one server process and one editing tab. It is not a multi-user or remotely hosted service.

## Workflow

- Import photos with the file picker (multiple selection supported, 100 images per request, 25 MB each). Files are copied into `data/photos`; source photos are untouched. Supported: JPEG, PNG, WEBP, HEIC/HEIF. Each photo starts as a separate inventory item; no adjacency assumptions.
- Select the front image. Choose another unprocessed image as its back and click **Pair**. Use **Swap sides** or **Separate photos** to correct a pairing. Singles are permitted; the review checkbox includes the decision to proceed without a back.
- **Identify with OpenAI** sends resized JPEGs to the configured vision model and validates its structured output. It creates an optimized title of at most 80 characters. Unknowns and mismatches require human inspection. Re-identifying replaces extracted fields/title/description, clears review, and retains manual price. It is a deliberate additional API call, not an automatic background action. Token usage is stored per card; no potentially stale dollar estimate is shown.
- Edit player, set, flags, title, description, price, category and condition IDs. Condition defaults to the original raw-card ID; verify it for your card. There are no asserted Mint grades, on-card autograph claims, shipping promises, comps, or generated prices. The generated description is a starting point.
- Save edits before other actions. **Upload listing photos** sends full-resolution, orientation-corrected JPEGs to Cloudinary, including HEIC inputs. These are public hosted listing photos. The API key never reaches the UI. Uploaded sides are cached and reused after a partial failure.
- Review all fields and photos, set a positive USD price with at most two decimals, check the review box, and save. The checklist shows remaining blockers. Select eligible cards in inventory and **Export selected CSV**.
- Export creates drafts only. Upload the file to Seller Hub → Reports → Upload. Review drafts, current category/condition requirements, shipping, location, return/payment policies and price in Seller Hub before publishing. The CSV keeps the original template information rows, exact column order and identifiers, CRLF line endings, and pipe-separated photo URLs. A stable inventory ID is now populated in the existing SKU column. Formula-like text is escaped for spreadsheet safety.

Export status records file generation, not confirmed Seller Hub import. Repeat export is allowed for recovery; importing twice can create duplicate eBay drafts. No eBay credentials or publishing API are used.

## Persistence and recovery

Inventory fields, photo references, extraction results, token usage, warnings, successful upload URLs and states persist in `data/inventory.sqlite3`. States are imported, processing, needs_review, ready, failed, exported. Restart converts interrupted processing into a retryable failed state. Failed requests retain fields and photos. The single server serializes inventory changes and rejects edits while a card is processing. API/network timeouts are bounded; OpenAI retries connection errors, rate limits and transient server errors, while Cloudinary retries network failures, 429 and server errors up to three attempts. A crash after a remote upload succeeds but before its URL is saved may create an orphan hosted image on retry.

Back up the entire `data` folder with the app stopped; keep the database and photos together. `LISTER_DATA_DIR` may point to another local folder. No automated cleanup, schema migrations, deletion UI, or legacy inventory migration is included yet. Never delete `data` to troubleshoot without a backup. Avoid concurrent instances on the same data folder.

## Development and checks

```powershell
.venv\Scripts\python.exe -m pytest -q
cd frontend
npm install
npm run build
cd ..
.venv\Scripts\python.exe launcher.py
```

The tests use temporary SQLite databases and mocked external providers; they do not spend API credit or upload photos. Live OpenAI/Cloudinary calls and real Seller Hub acceptance require separate testing with your accounts and a representative card batch. The OpenAI implementation follows the official structured-output Responses API guide: https://developers.openai.com/api/docs/guides/structured-outputs.

`requirements-lock.txt` records the exact tested Python dependencies (Windows/Python 3.12); `requirements.txt` records supported direct dependency ranges. `frontend/pnpm-lock.yaml` records the tested UI dependency resolution; developers using pnpm can run `pnpm install --frozen-lockfile` and `pnpm run build`. The Windows setup uses npm for accessibility and resolves the ranges in `package.json`.

The original `ebay_draft_lister.py` remains unchanged as a legacy reference; it still requires Anthropic if run independently. V2 never imports it or requires Anthropic. Pure title/CSV definitions were copied into `backend/compat.py`, with a regression test comparing template constants. Legacy rename/list/repair CLI workflows remain available in the original file, but are not integrated into v2. In v2, re-uploading JPEGs and exporting again can rebuild a draft; existing eBay drafts must be managed manually.

## Next steps beyond this foundation

Native Windows packaging; background batch queue with cancellation/progress; configurable shop description templates; better searchable inventory and audit history; legacy sidecar import; automated pairing suggestions; dependency update/CI automation; real-account integration checks. There is no automatic pricing or sold-comps service planned into this foundation.
