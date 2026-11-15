# eBay Draft Lister v2 — Windows setup

## Start here

1. Install Python 3.12+ (include the `py` launcher) and Node.js 22 LTS+ (includes npm).
2. Download or check out `feature/openai-v2-ui`. Double-click `setup-windows.cmd`. Internet access is required for dependency downloads. Setup creates a project-local Python environment and builds the UI.
3. Double-click `launch-windows.cmd`. The interface opens at http://127.0.0.1:8765. Open **Settings & connections** to enter your OpenAI API key, Cloudinary cloud name and unsigned upload preset. Changes apply immediately. Leave connections blank to use sync, pairing and manual editing offline. Keep the preset restricted to image uploads in Cloudinary.
4. Leave the server window open; press Ctrl+C there to stop. No Node server is needed after setup. Advanced users can still use `.env` or environment variables; saved Settings overrides those values. Restart after manually changing `.env`, and never commit it.

This foundation is a desktop-style browser application served on your computer. A native window/installer is not included. Use one server process and one editing tab. It is not a multi-user or remotely hosted service.

## Workflow

- **Sync folder** defaults to `Z:\Match Day Cards\eBay`. It reads all supported photos directly in that folder without a 100-photo batch limit, copies new/changed images into `data/photos`, and skips previously synced source-file versions. It does not recurse into Done, Needs Review or any other subfolder, and never moves, renames or deletes originals. A source file modified since the last sync becomes a new inventory item; its previous item and edits are preserved. Run again after dropping more photos into the folder. The source drive must be connected in the Windows session running the app.
- In **Pairing suggestions**, matching `_front`/`_back` filenames are grouped first. The default then groups unnamed photos in natural filename order (1, 2, 10, 11), two at a time. These are suggestions, not verified identities: every pair is flagged for review and an odd leftover remains a single. Choose filename-only matching or keep all photos separate when order is unreliable. Existing edited/processed cards are not regrouped. A deliberate separation is respected on repeat sync. A later named back photo can join its previously synced unprocessed named front.
- The **Import photos** file picker remains available. Select as many photos as needed; the UI sends them in chunks of 100, so the underlying per-request limit no longer limits one selection. Picker-imported photos remain separate until manually paired, or recognized by a later folder sync with the same filename/content. Browser file pickers cannot be forced to open a fixed drive folder, so **Sync folder** is the default-path workflow. Both methods support JPEG, PNG, WEBP and HEIC/HEIF with a 25 MB per-photo limit. Corrupt/unreadable folder files are reported without discarding successful imports.
- Select the front image. Choose another unprocessed image as its back and click **Pair**. Use **Swap sides** or **Separate photos** to correct a pairing. Singles are permitted; the review checkbox includes the decision to proceed without a back.
- **Identify with OpenAI** sends resized JPEGs to the configured vision model and validates its structured output. It creates an optimized title of at most 80 characters. Unknowns and mismatches require human inspection. Re-identifying replaces extracted fields/title/description, clears review, and retains manual price. It is a deliberate additional API call, not an automatic background action. Token usage is stored per card; no potentially stale dollar estimate is shown.
- Edit player, set, flags, title, description, price, category and condition IDs. Condition defaults to the original raw-card ID; verify it for your card. There are no asserted Mint grades, on-card autograph claims, shipping promises, comps, or generated prices. The generated description is a starting point.
- Save edits before other actions. **Upload listing photos** sends full-resolution, orientation-corrected JPEGs to Cloudinary, including HEIC inputs. These are public hosted listing photos. The API key never reaches the UI. Uploaded sides are cached and reused after a partial failure.
- Review all fields and photos, set a positive USD price with at most two decimals, check the review box, and save. The checklist shows remaining blockers. Select eligible cards in inventory and **Export selected CSV**.
- Export creates drafts only. Upload the file to Seller Hub → Reports → Upload. Review drafts, current category/condition requirements, shipping, location, return/payment policies and price in Seller Hub before publishing. The CSV keeps the original template information rows, exact column order and identifiers, CRLF line endings, and pipe-separated photo URLs. A stable inventory ID is now populated in the existing SKU column. Formula-like text is escaped for spreadsheet safety.

### Background batches

Use the inventory checkboxes or **Select visible** to select cards, then choose **Identify selected** or **Upload selected photos**. The former 100-card batch cap is removed. Save unsaved edits first. Identification sends the selected photos to OpenAI and replaces extracted fields; upload sends them to Cloudinary. Selecting incomplete cards is allowed for processing, while **Export selected CSV** remains disabled until every selected card meets its checklist.

### Settings and credentials

**Settings & connections** is available in the sidebar and beside **Sync folder**. Change the source folder, OpenAI model/API key, and Cloudinary connection there. Blank credential fields preserve the current values; explicit remove checkboxes disable the connections, including environment-variable fallback. API keys and upload presets are never returned to the browser after saving. On Windows, credentials are encrypted with DPAPI for your current Windows account and stored in `data/settings.json`; other systems can use environment variables. Copying encrypted settings to another Windows account will not transfer access: remove/recreate that settings file and enter credentials again. Keep backups private. The folder and model are ordinary preferences.

Sync is manual (click to rescan), not a continuous folder watcher. It does not call OpenAI or upload to Cloudinary. Review suggested pairs first, then start identification/upload explicitly. Very large syncs may take time while files are copied and checked; the button shows “Syncing folder…” until the report is ready. Repeat sync after files finish copying if they changed during the scan.

One batch runs at a time, processing cards sequentially. Its progress panel shows finished and failed counts and stays available after refreshing the page. You can browse and edit other, unqueued cards while it runs. Polling preserves unsaved edits. Queued/in-flight cards reject edits, pairing changes and duplicate processing; finished cards become editable again. Individual failures do not stop the remaining batch.

**Cancel batch** finishes the current card and skips the remaining cards. It cannot undo an API call already sent. Completed fields and upload URLs stay saved, while unstarted cards return to their prior states. The panel records cancelled/completed/interrupted status; the latest 20 batch records are available through the local API, and the UI displays the latest one. To retry, filter the inventory for failed cards or select unfinished cards and start another batch.

On shutdown, the worker stops before starting another card. A hard shutdown may interrupt an in-flight call; startup marks that card failed, restores unstarted cards, and records the batch as interrupted. Work is never automatically resubmitted or charged again after restart. Jobs and reservations use the existing SQLite database; adding the jobs table is automatic and preserves existing inventory.

Export status records file generation, not confirmed Seller Hub import. Repeat export is allowed for recovery; importing twice can create duplicate eBay drafts. No eBay credentials or publishing API are used.

## Persistence and recovery

Inventory fields, photo references, extraction results, token usage, warnings, successful upload URLs, batch progress and states persist in `data/inventory.sqlite3`. Card states are imported, queued, processing, needs_review, ready, failed, exported. Restart converts interrupted processing into a retryable failed state. Failed requests retain fields and photos. The single server serializes inventory changes and rejects edits while a card is queued or processing. API/network timeouts are bounded; OpenAI retries connection errors, rate limits and transient server errors, while Cloudinary retries network failures, 429 and server errors up to three attempts. A crash after a remote upload succeeds but before its URL is saved may create an orphan hosted image on retry.

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

Native Windows packaging; configurable shop description templates; better searchable inventory and audit history; legacy sidecar import; automated pairing suggestions; dependency update/CI automation; real-account integration checks. There is no automatic pricing or sold-comps service planned into this foundation.
