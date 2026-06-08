# How to put these files on GitHub (no terminal needed)

## Option A — GitHub Desktop (recommended, no command line)

1. Install GitHub Desktop: https://desktop.github.com/
2. Open it, sign in with your GitHub account
3. **File → Clone repository → URL tab**
4. Paste: `https://github.com/billilkov-beep/roofmeasure-engine.git`
5. Pick a local folder (e.g. `Documents\GitHub`). It clones the empty repo.
6. Open File Explorer to the cloned folder (GitHub Desktop has a button "Show in Explorer")
7. Open another File Explorer window in the folder where you unzipped THIS file
   (the one with `measure.py`, `README.md`, `roofmeasure/`, `deploy/`, etc.)
8. Select ALL the files and folders from this folder — Ctrl+A
9. Drag them into the cloned folder (your local repo)
10. Switch back to GitHub Desktop — you'll see ~50 changes listed in the left panel
11. In the bottom left, type a summary: `Initial commit — RoofMeasure Engine v0.8`
12. Click the blue **"Commit to main"** button
13. Click **"Push origin"** at the top
14. Done. Refresh github.com/billilkov-beep/roofmeasure-engine to see the files.

## Option B — Drag-drop in the browser (no install at all)

1. On github.com, go to your empty repo:
   https://github.com/billilkov-beep/roofmeasure-engine
2. You should see "Quick setup" — click the link "uploading an existing file"
   (or just append `/upload/main` to the URL)
3. Open File Explorer to the folder where you unzipped this
4. Select ALL files and folders (Ctrl+A) and drag them into the GitHub web page
   (the drop area says "Drag files here to add them to your repository")
5. Wait for the upload progress bar to finish (might take 30 seconds for ~50 files)
6. Scroll to the bottom — fill in commit message: `Initial commit — RoofMeasure Engine v0.8`
7. Click the green **"Commit changes"** button
8. Done.

GitHub limits: max 100 files per drop, 25MB per file. We have ~50 files,
largest is around 17KB. Well within the limits.

## After the push

Either way, refresh github.com/billilkov-beep/roofmeasure-engine and you should see
the full file tree, including README.md, measure.py, roofmeasure/, deploy/, etc.
