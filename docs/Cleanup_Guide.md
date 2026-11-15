# Cleanup Duplicate Files — Plantr repo

Applied in the Phase 0 cleanup. `main.py` is the only API entry point. Earlier drafts live in `archive/`. Planning notes live in `docs/`, and duplicate uploads of those notes live in `docs/archive/`. Do not run the commands below. They describe an older upload cleanup and would remove files this repo now keeps. The original checklist is below.

The repo had several duplicate files from uploads. Clean them up as follows:

## Files to delete (keep the newest / best version)

- Plant_Influencer_Platform_Overview-1.md
- Plant_Influencer_Platform_Overview-2.md
- Plant_Influencer_Platform_Overview-3.md
- Notion_Connection_Guide-1.md
- requirements-1.txt
- main_expanded.py (old version — keep main_expanded-1.py and rename it)

## Keep
- main_expanded-1.py → rename to main_expanded.py
- plantr_dashboard.html
- plantr_admin.html
- All schemas, guides, sample_data.json, test_api.py, requirements.txt (updated)

## How to delete on GitHub
1. Open the file on GitHub.
2. Click the trash icon (top right of the file view).
3. Commit the deletion.

Or locally:
```bash
git clone https://github.com/rossplant/influencer-pro.git
cd influencer-pro
rm Plant_Influencer_Platform_Overview-*.md
rm Notion_Connection_Guide-1.md
rm requirements-1.txt
rm main_expanded.py
mv main_expanded-1.py main_expanded.py
git add -A
git commit -m "Clean up duplicate files"
git push
```

After cleanup the repo will be much cleaner.
