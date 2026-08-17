# Markwell

A Windows desktop app that converts documents (PDF, DOCX, XLSX, PPTX, and more) to Markdown, optionally uploads them to Google Drive, and syncs them to a Notion database. Three specialized conversion engines are routed by file type, with bundled Tesseract OCR for scanned PDFs.

## Features
- **Desktop GUI**: Three-tab interface (변환, 동기화 점검, 설정) with a real-time log panel. Launch with `run_gui.bat` or use the packaged `Markwell.exe`.
- **Three Conversion Engines**: Automatically routes files to the best engine:
  - **pdf-inspector** (Rust, MIT): High-quality PDF conversion with page-level OCR detection.
  - **firecrawl-anydoc** (Rust, MIT): Specialized engine for Microsoft Office, OpenDocument, and other complex formats.
  - **MarkItDown** (Microsoft): HTML, JSON, XML, ZIP, plain text, and images.
- **Smart OCR for Scanned PDFs**: pdf-inspector classifies each PDF as `text_based` / `scanned` / `image_based` / `mixed` and flags individual pages needing OCR. Mixed documents use text-based pages as-is and rasterize only the scanned pages, so a mostly-text PDF never pays for a full OCR pass. If pdf-inspector itself fails on a file, MarkItDown's PDF converter is tried next, and OCR only as a last resort.
- **Bundled Tesseract OCR**: No installer wizard or manual setup. Tesseract is packaged with the app; language models are downloaded automatically as needed.
- **Optional Google Drive Upload**: Toggle on/off per session. Converted `.md` files always stay in the output folder; enabling Drive uploads a copy as well.
- **Originals are only archived when you hand them over**: files placed in `input_files/` move to `processed_files/` once converted. Files you add through the queue (file picker, folder picker, drag-and-drop) are **never moved** — they stay exactly where they were.
- **Optional Notion Sync**: Toggle on/off per session. Creates one page per document with the converted Markdown embedded. With Drive also on, the page carries the Drive link in its `URL` property; with Drive off, the page is created without that property.
- **Duplicate Prevention**: Uses SHA-256 hashing to skip files already converted.
- **Auto Cleanup & Sync Check**: Moves successfully processed files to a `processed_files` directory. The 동기화 점검 tab cleans up history and trashes Notion pages if the file is deleted from Google Drive (only available when Drive is on).

## Supported File Formats

| Engine | Formats |
|--------|---------|
| **pdf-inspector** | `.pdf` |
| **firecrawl-anydoc** | `.doc`, `.docx`, `.docm`, `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm`, `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, `.odt`, `.ods`, `.odp`, `.rtf`, `.epub`, `.csv` |
| **MarkItDown** | `.html`, `.htm`, `.zip`, `.json`, `.xml`, `.txt`, `.jpg`, `.jpeg`, `.png` |

## Prerequisites
- Windows. The packaged exe needs nothing else; running from source needs Python 3.10+.
- Google Cloud Project with Google Drive API enabled (required only if using Drive upload)
- Notion Integration Token & Database ID (required only if using Notion sync)

### Development environment

Two constraints pin the interpreter:

- **Python 3.13**, not 3.14 — every real `markitdown` release declares
  `Requires-Python <3.14`, and on 3.14 pip silently falls back to the empty
  `markitdown 0.0.2` placeholder.
- **A dedicated environment, not Anaconda's `base`** — the base install carries
  its own Qt and CRT DLLs that shadow the ones PySide6 ships, so
  `PySide6.QtCore` fails with `DLL load failed`. A plain `venv` created from the
  Anaconda base interpreter inherits the same problem.

A clean conda environment satisfies both:

```bash
conda create -n markwell python=3.13 -y
conda run -n markwell pip install -r requirements-dev.txt
```

`run_gui.bat` and `packaging/build.ps1` look for this environment first and fall
back to `venv/` if it exists.

## Setup Guide

### 1. Google Drive API Authentication (Optional)
Only follow this if you plan to use Google Drive upload. Otherwise, skip to step 2.

You need a `credentials.json` file so the app can upload files to your Google Drive.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a **New Project**.
3. Navigate to **APIs & Services > Library**, search for **Google Drive API**, and click **Enable**.
4. Go to **APIs & Services > OAuth consent screen**. Choose **External** (or Internal if you have a Google Workspace) and fill out the required basic information.
5. Go to **APIs & Services > Credentials**.
6. Click **+ CREATE CREDENTIALS** and select **OAuth client ID**.
7. Choose **Desktop app** as the Application type, give it a name, and click **Create**.
8. Download the JSON file and rename it to **`credentials.json`**.
9. **Register it in the app:** Launch the GUI, go to the 설정 tab, and use the file picker to register `credentials.json`.

### 2. Notion API Integration (Optional)
Only follow this if you plan to use Notion sync. Otherwise, skip to step 3.

You need an Internal Integration Token and a Database ID to sync files to Notion.

1. **Create an Integration (Bot):**
   - Go to [Notion My Integrations](https://www.notion.so/my-integrations).
   - Click **+ New integration**. Select the workspace you want to use, give the integration a name, and ensure the type is **Internal** (or Secret Token).
   - Once created, copy the **Internal Integration Secret** (it starts with `secret_...`).

2. **Prepare your Notion Database:**
   - In your Notion workspace, create a new Database (Full page or Inline).
   - You must have exactly these two properties (case-sensitive):
     - **`Name`** (Property type: `Title` / `제목`)
     - **`URL`** (Property type: `URL`)
   - Click the `...` menu in the top right of your database page, select **Add connections (연결 추가)**, and search for the name of the integration you just created to invite it to the database.

3. **Get the Database ID:**
   - Copy the link to your Notion database page.
   - The URL will look like this: `https://www.notion.so/myworkspace/a1b2c3d4e5f6g7h8i9j0?v=...`
   - The long alphanumeric string between your workspace name and the `?v=` is your **Database ID** (in this example, `a1b2c3d4e5f6g7h8i9j0`).

4. **Configure the app:**
   - Launch the GUI and go to the 설정 tab.
   - Paste your Integration Secret into **Notion API Key** and your Database ID into **Notion Database ID**.

### 3. Run the App

**From source:**
```bash
python main.py
```

**Or use the provided batch file:**
```
run_gui.bat
```

**Or run the packaged executable:**
```
Markwell.exe
```

Once the GUI is open:
1. Drop files into the `input_files/` folder (or use the folder picker in the 변환 tab).
2. In the 변환 tab:
   - Toggle **Google Drive 연동** on/off (default: on).
   - Toggle **Notion 연동** on/off (default: on).
   - Click **변환 시작**. With an empty queue the whole `input_files/` folder is processed.
3. Monitor progress in the log panel at the bottom.
4. Use the 동기화 점검 tab to sync deletions from Google Drive to local history and Notion (only available when Drive is on).
5. Adjust OCR language and other settings in the 설정 tab.

## Building the Packaged Executable

If you want to build the standalone Windows executable from source:

1. **Vendor Tesseract OCR:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/vendor_tesseract.ps1
   ```
   This copies `tesseract.exe` and its DLLs from an existing Tesseract installation on your machine into `resources/tesseract/` (gitignored, ~68MB). Language models (eng, kor, osd) are pre-committed to `resources/tessdata_best/`.

2. **Build the executable:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File packaging/build.ps1
   ```

### Releases

Pushing a `v*` tag builds the app on a Windows runner and attaches
`Markwell-<version>-win64.zip` to the matching GitHub release, creating the
release if it does not exist yet:

```bash
git tag v2.0.0 && git push origin v2.0.0
```

The same workflow can be run manually from the Actions tab to exercise the
packaging without tagging — that path uploads the zip as a run artifact instead.
Either way the frozen exe is smoke-tested with `--selftest` before upload, since
missing hidden imports and DLLs only show up when the packaged app actually runs.
   Output: `dist/Markwell/Markwell.exe` (onedir build with dependencies alongside).

## Data Storage

- **Running from source:** Settings, credentials, and history are stored in `app/` (`config.json`, `credentials.json`, `token.json`, `history.json`).
- **Running the packaged exe:** Settings are stored in `%LOCALAPPDATA%\Markwell\`. The 설정 tab has a "기존 app 폴더에서 가져오기" button to migrate settings from a source installation.

## Configuration

Configuration is managed through the **설정 tab** in the GUI, not by hand-editing JSON:
- **NOTION_API_KEY**: Your Notion integration secret.
- **NOTION_DATABASE_ID**: Your Notion database ID.
- **OCR_LANG**: Languages for OCR (e.g., `eng`, `eng+kor`).
- **ENABLE_DRIVE**: Toggle Google Drive upload on/off.
- **ENABLE_NOTION**: Toggle Notion sync on/off.
- **DRIVE_FOLDER_NAME**: Name of the Google Drive folder for uploads (default: `mdconversion`).
- **OCR_LANG**: Tesseract language codes, `+`-joined (e.g. `eng`, `eng+kor`, `jpn`). Missing models download automatically on first use.
- **INPUT_DIR**, **PROCESSED_DIR**, **OUTPUT_DIR**: Leave empty to use defaults (`input_files/`, `processed_files/`, `output_files/`).

A template is provided at `config.example.json` for reference. Configuration files
written by an older version still load — the removed `OCR_ENGINE` key is ignored.

---

## Acknowledgments & License

This project utilizes several powerful open-source tools:

1. [**MarkItDown**](https://github.com/microsoft/markitdown) by Microsoft. 
   - Used for HTML, JSON, XML, ZIP, and image conversion. Released under the **MIT License**.

2. [**pdf-inspector**](https://github.com/firecrawl/pdf-inspector) by Firecrawl.
   - Used for high-quality PDF conversion with page-level OCR detection. Released under the **MIT License**.

3. [**anydoc**](https://github.com/firecrawl/anydoc) by Firecrawl.
   - Used for Microsoft Office, OpenDocument, and complex format conversion. Published on PyPI as `firecrawl-anydoc`. Released under the **MIT License**.

4. [**Tesseract OCR**](https://github.com/tesseract-ocr/tesseract) by Tesseract.
   - Used for extracting text from scanned PDFs. Released under the **Apache License 2.0**. Binaries are redistributed with the packaged application.

This batch utility is provided as-is, expanding upon the core capabilities of these tools to include automation, engine routing, cloud storage, and Notion synchronization.
