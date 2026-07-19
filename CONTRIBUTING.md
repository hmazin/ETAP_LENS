# Contributing to ETAP Lens

Thanks for considering a contribution — this project is meant to be improved by whoever finds it useful.

## Getting set up

```bash
git clone https://github.com/hmazin/ETAP_LENS.git
cd ETAP_LENS
pip install -r requirements.txt
python app.py
```

See the [README](README.md) for requirements (Windows + SQL Server LocalDB for project models; study result files need neither).

## Reporting bugs

Open an issue with:
- What you were doing (loading a project? exporting? browsing folders?)
- What you expected vs. what happened
- Your ETAP version, if relevant (table/column names can differ across versions)
- Any error message from the terminal running `python app.py`

Please don't attach real project files that contain client-confidential data. A description of the schema/table involved is usually enough; a synthetic/sanitized example is even better.

## Making changes

- There's no build step — `static/app.js` and `static/style.css` are plain files, edit and refresh.
- Backend logic lives in `etap_reader/`; `app.py` should stay a thin layer of Flask routes over it.
- Match the existing style: minimal comments (only where the *why* isn't obvious from the code), no unnecessary abstractions.
- If you add a new curated category or table mapping, it belongs in `etap_reader/categories.py`.
- If you add support for a new ETAP file extension, start in `etap_reader/study_result.py` (for SQLite-based result files) or `etap_reader/locate.py` (for SQL-Server-based files).

## Testing your changes

There's no automated test suite yet (a good first contribution!). Manually verify against a real ETAP project:
1. Load a project model and confirm the category views populate correctly
2. Load a study result file (if you touched that path) and check its category views
3. Check the browser console for errors
4. If you touched table rendering, test search, sort, column visibility, pagination, and export together — they interact

## Pull requests

- Keep PRs focused — one feature or fix per PR is easier to review than a bundle
- Describe what changed and why in the PR description
- If it's a UI change, a before/after screenshot helps a lot

## Ideas if you don't know where to start

See the "Ideas for contribution" section in the [README](README.md#ideas-for-contribution) — cross-platform support (removing the SQL Server LocalDB dependency) and cross-linking model data with study results are the two most requested directions.
