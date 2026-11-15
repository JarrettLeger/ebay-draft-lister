# eBay Draft Lister v2 — Windows setup

## Start here

1. Install Python 3.12+ (include the `py` launcher) and Node.js 22 LTS+ (includes npm).
2. Download or check out `feature/openai-v2-ui`. Double-click `setup-windows.cmd`. Internet access is required for dependency downloads. Setup creates a project-local Python environment and builds the UI.
3. Double-click `launch-windows.cmd`. The interface opens at http://127.0.0.1:8765. Open **Settings & connections** to choose OpenAI or Claude and enter its API key, plus your Cloudinary cloud name and unsigned upload preset. Changes apply immediately. Leave connections blank to use sync, pairing and manual editing offline. Keep the preset restricted to image uploads in Cloudinary.
4. Leave the server window open; press Ctrl+C there to stop. No Node server is needed after setup. Advanced users can still use `.env` or environment variables; saved Settings overrides those values. Restart after manually changing `.env`, and never commit it.

This foundation is a desktop-style browser application served on your computer. A native window/installer is not included. Use one server process and one editing tab. It is not a multi-user or remotely hosted service.

## Workflow

- **Sync folder** defaults to `Z:\Match Day Cards\eBay`. It reads all supported photos directly in that folder without a 100-photo batch limit, copies new/changed images into `data/photos`, and skips previously synced source-file versions. It does not recurse into Done, Needs Review or any other subfolder, and never moves, renames or deletes originals. A source file modified since the last sync becomes a new inventory item; its previous item and edits are preserved. Run again after dropping more photos into the folder. The source drive must be connected in the Windows session running the app.
- In **Pairing suggestions**, matching `_front`/`_back` filenames are grouped first. The default then groups unnamed photos in natural filename order (1, 2, 10, 11), two at a time. These are suggestions, not verified identities: every pair is flagged for review and an odd leftover remains a single. Choose filename-only matching or keep all photos separate when order is unreliable. Existing edited/processed cards are not regrouped. A deliberate separation is respected on repeat sync. A later named back photo can join its previously synced unprocessed named front.
- The **Import photos** file picker remains available. Select as many photos as needed; the UI sends them in chunks of 100, so the underlying per-request limit no longer limits one selection. Picker-imported photos remain separate until manually paired, or recognized by a later folder sync with the same filename/content. Browser file pickers cannot be forced to open a fixed drive folder, so **Sync folder** is the default-path workflow. Both methods support JPEG, PNG, WEBP and HEIC/HEIF with a 25 MB per-photo limit. Corrupt/unreadable folder files are reported without discarding successful imports.
- Select the front image. Choose another unprocessed image as its back and click **Pair**. Use **Swap sides** or **Separate photos** to correct a pairing. Singles are permitted; the review checkbox includes the decision to proceed without a back.
- **Identify with OpenAI** or **Identify with Claude** sends resized JPEGs only to your selected provider and vision model and validates its structured output. It creates an optimized title of at most 80 characters. Unknowns and mismatches require human inspection. Re-identifying replaces extracted fields/title/description, clears review, and retains manual price. It is a deliberate additional API call, not an automatic background action. Token usage is stored per card; no potentially stale dollar estimate is shown.
- Edit player, set, flags, title, description, price, category and condition IDs. Condition defaults to the original raw-card ID; verify it for your card. There are no asserted Mint grades, on-card autograph claims, shipping promises, comps, or generated prices. The generated description is a starting point.
- Save edits before other actions. **Upload listing photos** sends full-resolution, orientation-corrected JPEGs to Cloudinary, including HEIC inputs. These are public hosted listing photos. The API key never reaches the UI. Uploaded sides are cached and reused after a partial failure.
- Review all fields and photos, set a positive USD price with at most two decimals, check the review box, and save. The checklist shows remaining blockers. Select eligible cards in inventory and **Export selected CSV**.
- Export creates drafts only. Upload the file to Seller Hub → Reports → Upload. Review drafts, current category/condition requirements, shipping, location, return/payment policies and price in Seller Hub before publishing. The CSV keeps the original template information rows, exact column order and identifiers, CRLF line endings, and pipe-separated photo URLs. A stable inventory ID is now populated in the existing SKU column. Formula-like text is escaped for spreadsheet safety.

### Background batches

Use the inventory checkboxes or **Select visible** to select cards, then choose **Identify selected** or **Upload selected photos**. The former 100-card batch cap is removed. Save unsaved edits first. Identification sends the selected photos to your chosen provider and replaces extracted fields; upload sends them to Cloudinary. Selecting incomplete cards is allowed for processing, while **Export selected CSV** remains disabled until every selected card meets its checklist.

### Choosing OpenAI or Claude

Open **Settings & connections → Identification provider** and choose **OpenAI** or **Claude (Anthropic)**. Enter the matching API key in that provider’s field and click **Save settings**. Only the selected provider’s model field is required; leaving the other model blank preserves its saved value. Both providers’ keys and model names are stored independently; switching providers does not erase the other connection. Blank key fields keep saved keys, while each remove checkbox clears only its own provider. Existing installations keep OpenAI selected and preserve the previous OpenAI model/key.

The default model fields are `gpt-4.1-mini` for OpenAI and `claude-sonnet-4-6` for Claude. You can enter another model ID with vision and structured-output support available to your account. API keys must belong to the selected provider; the app never substitutes the other provider when a key is missing or a request fails. The sidebar and identification buttons name the selected provider. “Configured” means a key is saved, not that it has been verified with the service.

Changes apply to new identification requests. Each background identification batch keeps a snapshot of its starting provider, model and credentials, so changing Settings cannot send later cards in that batch to a different service. The job records its provider/model; cards record the provider/model from their latest successful extraction. Keys are never included in job/card records. An existing listing is not re-identified merely because you switch providers; manually re-identifying replaces extracted fields and clears review, while retaining your manual price. Both providers use the same card schema, title rules, review checks and CSV export.

Advanced environment configuration: `LISTER_AI_PROVIDER=openai` or `claude`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`. Saved Settings takes precedence. If upgrading a different checkout, rerun `setup-windows.cmd` to install the new Claude SDK and rebuild the UI. Restart the server and refresh the browser after updating application code.

The Claude adapter uses the Messages API’s Pydantic structured-output helper, JPEG image blocks, token usage recording and refusal/incomplete-response checks: [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs). No live account calls were made during automated verification.

### Settings and credentials

**Settings & connections** is available in the sidebar and beside **Sync folder**. Change the source folder, identification provider, each provider’s model/API key, and Cloudinary connection there. Blank credential fields preserve the current values; explicit remove checkboxes disable the connections, including environment-variable fallback. API keys and upload presets are never returned to the browser after saving. On Windows, credentials are encrypted with DPAPI for your current Windows account and stored in `data/settings.json`; other systems can use environment variables. Copying encrypted settings to another Windows account will not transfer access: remove/recreate that settings file and enter credentials again. Keep backups private. The folder and model are ordinary preferences.

Sync is manual (click to rescan), not a continuous folder watcher. It does not call either AI provider or upload to Cloudinary. Review suggested pairs first, then start identification/upload explicitly. Very large syncs may take time while files are copied and checked; the button shows “Syncing folder…” until the report is ready. Repeat sync after files finish copying if they changed during the scan.

One batch runs at a time, processing cards sequentially. Its progress panel shows finished and failed counts and stays available after refreshing the page. You can browse and edit other, unqueued cards while it runs. Polling preserves unsaved edits. Queued/in-flight cards reject edits, pairing changes and duplicate processing; finished cards become editable again. Individual failures do not stop the remaining batch.

**Cancel batch** finishes the current card and skips the remaining cards. It cannot undo an API call already sent. Completed fields and upload URLs stay saved, while unstarted cards return to their prior states. The panel records cancelled/completed/interrupted status; the latest 20 batch records are available through the local API, and the UI displays the latest one. To retry, filter the inventory for failed cards or select unfinished cards and start another batch.

On shutdown, the worker stops before starting another card. A hard shutdown may interrupt an in-flight call; startup marks that card failed, restores unstarted cards, and records the batch as interrupted. Work is never automatically resubmitted or charged again after restart. Jobs and reservations use the existing SQLite database; adding the jobs table is automatic and preserves existing inventory.

### Renaming and moving exported photos

After you finalize and export selected cards, the app first saves the complete eBay CSV in `data/exports/<export-id>.csv`. Only after that succeeds does it rename the original synced photos and move them into a `Done` folder alongside those originals. For your default source, this is `Z:\Match Day Cards\eBay\Done`.

Names include the player, year, set, a stable card ID suffix and side, for example `Jane_Player_2024_Test_Set_a1b2c3d4_front.jpg` and `_back.jpg`. Original photo bytes and file formats are preserved: HEIC originals retain `.heic`; hosted listing photos remain JPEG. Collisions receive a numeric suffix, and existing files are never overwritten. The copied inventory photos stay intact so previews and recovery continue to work.

Only cards included in the successfully generated CSV are archived. Failed/incomplete or unselected cards stay where they are. The app checks each original against the imported content before moving; changed photos are left untouched with a warning. It uses the source location recorded during sync, even if you later change the configured source folder. Picker-only photos have no reliable original path, so sync their source folder first if you want originals archived automatically.

If the network drive is disconnected, a photo is locked, missing or changed, or another move fails, the CSV is still available. The export summary and card’s **Export & photo archive** section show warnings. Export again to retry failed archive moves; successfully moved photos are not moved again. A journal recovers a rename completed just before an interruption. Exporting again generates another CSV, so avoid uploading duplicate drafts to eBay.

Each exported card has **Download saved CSV**, which recovers its last export without creating another export or moving more files. This also covers a failed browser download after the server has saved the CSV and moved the photos. Back up `data/exports` with the rest of `data`. “Done” means a CSV was generated, not that eBay accepted or published the listing. Tests used generated temporary photos only; real source files were not moved during development.

Export status records file generation, not confirmed Seller Hub import. Repeat export is allowed for recovery; importing twice can create duplicate eBay drafts. No eBay credentials or publishing API are used.

## Persistence and recovery

Inventory fields, photo references, extraction results, token usage, warnings, successful upload URLs, batch progress and states persist in `data/inventory.sqlite3`. Card states are imported, queued, processing, needs_review, ready, failed, exported. Restart converts interrupted processing into a retryable failed state. Failed requests retain fields and photos. The single server serializes inventory changes and rejects edits while a card is queued or processing. API/network timeouts are bounded; Both AI SDKs use a 60-second request timeout and two retries for transient failures, while Cloudinary retries network failures, 429 and server errors up to three attempts. A crash after a remote upload succeeds but before its URL is saved may create an orphan hosted image on retry.

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

The tests use temporary SQLite databases and mocked external providers; they do not spend API credit or upload photos. Live OpenAI/Claude/Cloudinary calls and real Seller Hub acceptance require separate testing with your accounts and a representative card batch. The OpenAI implementation follows the official structured-output Responses API guide: https://developers.openai.com/api/docs/guides/structured-outputs.

`requirements-lock.txt` records the exact tested Python dependencies (Windows/Python 3.12); `requirements.txt` records supported direct dependency ranges. `frontend/pnpm-lock.yaml` records the tested UI dependency resolution; developers using pnpm can run `pnpm install --frozen-lockfile` and `pnpm run build`. The Windows setup uses npm for accessibility and resolves the ranges in `package.json`.

The original `ebay_draft_lister.py` remains unchanged as a legacy reference; it still requires Anthropic if run independently. V2 never imports the legacy script; it uses its own structured extraction adapters for OpenAI and Claude. Pure title/CSV definitions were copied into `backend/compat.py`, with a regression test comparing template constants. Legacy rename/list/repair CLI workflows remain available in the original file, but are not integrated into v2. In v2, re-uploading JPEGs and exporting again can rebuild a draft; existing eBay drafts must be managed manually.

## Next steps beyond this foundation

Native Windows packaging; configurable shop description templates; better searchable inventory and audit history; legacy sidecar import; automated pairing suggestions; dependency update/CI automation; real-account integration checks. There is no automatic pricing or sold-comps service planned into this foundation.
