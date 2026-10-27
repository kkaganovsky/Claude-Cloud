# Handoff: export detected events to a standalone HTML review package

**Goal:** add a pipeline option that takes a recording plus its model candidates CSV and writes a folder that anyone can
review in a browser. The folder needs no install, Python or model weights; the reviewer double-clicks `START_HERE.html`.
The reviewer answers Yes / No / Not sure for each event and sends back one CSV. A second command merges that CSV back
into the normal review format.

A working prototype was built by hand on 2026-10-27 for the hFA sEPSC cells 03 to 14 (about 3,650 events) and handed to a
collaborator. The reference files in this folder are that prototype, copied unchanged:

| File | What it is |
|---|---|
| `reference_make_review_pkg.py` | builder script (paths, cell numbers and thresholds are hard-coded for that data set) |
| `reference_START_HERE.html` | the review page, a single file of about 16 KB with plain JS and canvas and no libraries |
| `reference_README.txt` | instructions for a reviewer with little technical background |

Please turn this into a real, tested feature. The hard-coded parts are listed under "What to generalise".

---

## 1. How the prototype works

### Inputs
- `NN__<recording>.abf`: the recording.
- `NN__<recording>.candidates.csv`: output of `ephys_suite.ml.infer`, with columns `sweep, time_in_sweep_ms, score,
  accepted, amplitude_pA`.

### Selecting events
- Keep candidates with `score >= 0.5`. Lower scores are not shown; the user decided the lowest tier needs no review.
- Tier `detected` if `score >= thr`, otherwise `maybe`. Here `thr = 0.9` (the deployed threshold), and `0.99` for the two
  noisy cells (13, 14).
- Optional per-cell excluded time windows. Cell 13 drops 100 to 130 s (filtered on `time_in_sweep_ms`; that file is one
  long sweep).
- Events are sorted by `(sweep, time_in_sweep_ms)`.

### Snippets (the only data the page needs)
- Each needed sweep is filtered with a 4th-order Butterworth low-pass at 3 kHz using `sosfiltfilt` (zero-phase), then
  resampled to 10 kHz with `resample_poly`. **This is the same preprocessing the CNN uses (`core.prep_trace`,
  `LPF_HZ=3000`)**, so the reviewer sees what the model saw. Call `prep_trace` directly instead of copying the filter.
- Each snippet runs from **-100 ms to +200 ms** around `time_in_sweep_ms`, giving 3,000 samples at 10 kHz. Samples past the
  sweep edges are NaN, then 0.
- The snippet's median is subtracted, so the baseline sits at about 0 pA.
- Values are stored as **int16 in units of pA x 20** (0.05 pA resolution, clipped to ±32767), little-endian, then
  base64-encoded.
- For a sense of size, about 3,650 events at 6 KB each is roughly 22 MB of base64. The zip was 69 MB only because the raw
  `.abf` files were included for reference. They are optional; leave them out by default.

### Folder layout written
```
<out>/
  START_HERE.html
  README.txt
  data/cells.js        window.CELLS = [meta, ...]; window.SNIP = {fs:10000, pre:100, post:200, scale:20};
  data/cellNN.js       window.CELLDATA['NN'] = meta; .events = [[sweep, t_ms, score, tier], ...]; .b64 = '<int16 base64>'
  recordings/, labels/ (optional copies of the inputs)
```
Per-cell `meta`: `id, label, recording, file, n, n_detected, n_maybe, thr, note`.

**Why `.js` and not `.json`:** browsers block `fetch()` of local files when a page is opened from `file://`. A
`<script src>` tag loaded at runtime (`loadScript()` in the page) does work. This is what makes the package
"double-click and go". Keep it this way.

### The page (`reference_START_HERE.html`)
- **Left panel:** the cell list with progress bars. Cells with 0 events show "nothing to review". The URL `#cell=NN` opens
  a cell directly.
- **Plot:** the trace drawn on a canvas, with a dashed line at the event time, a pA and ms scale bar, and a tier badge
  ("Detected" in green, "Maybe" in orange).
- **The model score is deliberately not shown**, so the reviewer is not biased by it.
- **Buttons and keys:**
  - **Y** / Enter: yes
  - **N**: no
  - **U**: not sure
  - **← / →**: back / next
  - After an answer, the page moves to the next undecided event.
- **Options:**
  - *Smoother view*: an extra 1 kHz biquad low-pass run in JS, forwards and backwards (filtfilt).
  - *Zoom in*: shows -25 to +75 ms.
  - *Same Y scale*: fixes the axis at -45 to +15 pA.
  - *Skip answered*.
- **Persistence:** `localStorage` key `hfa_sepsc_review_v1_<cellId>` = `{d: {eventIndex: "yes"|"no"|"unsure"}, pos}`. Answers
  survive closing the browser, but only on the same browser and computer. That is why the CSV export exists.
- **Export:** "Download results for this cell" or "...for ALL cells" writes a CSV with columns `cell, label, recording,
  sweep, time_in_sweep_ms, time_s, model_score, model_tier, decision`. The decision is blank if the event was not answered.
- The page was tested in headless Chrome from `file://`. Chrome is recommended to reviewers; Safari also works for
  `localStorage` on `file://`, but this was not tested.

---

## 2. What to build in ephys-suite

### CLI
```
ephys-suite-review-export  <abf> [<abf> ...]  --candidates <csv or folder>  --out <folder>
        [--min-score 0.5] [--thr 0.9] [--thr-per-file NN=0.99 ...]
        [--exclude NN:100-130 ...]  [--pre 100 --post 200]  [--include-raw]  [--title "hFA sEPSC review"]

ephys-suite-review-import  <returned.csv>  --candidates <csv or folder>  --out <folder>
```
- Pair each recording with its candidates file using the same rule the GUI's auto-load uses (a candidates file beside the
  recording, or matching names).
- `review-import` turns the reviewer's CSV into the GUI's own *Save review…* format: `decision`, and `final_event` = 1 for
  yes. The user must choose how blank and "unsure" answers are counted; make it a flag that defaults to counting them as
  **not** events and printing how many there were. The output can then be loaded with *Resume saved review* in the GUI to
  check the remaining doubtful events.
- Match on `(recording, sweep, round(time_in_sweep_ms, 2))`. Do not use the row index; the reviewer's CSV is the source of
  truth for what was shown.

### GUI (Events tab)
A button *Export for outside review (HTML)…* in the *Review model events* group. It should export the loaded recording
and candidates, or every pair in the folder, with the same options. It should also skip events already decided in the
current review, so the user's own confirmed and rejected events are not sent out again. The user reviewed cells 01 and 02
in the app and gave only 03 to 14 to the collaborator. That split should be possible without hand-editing.

### What to generalise (hard-coded in the prototype)
- The paths `SRC` and `OUT`. The cell list skips `01` and `02` by name.
- The threshold rule `0.99 if n in ("13","14")` and the exclude window for cell 13.
- The `label` is built by string surgery on the file name (`n1` → `#1`). Use a manifest column or the file stem instead.
- The `localStorage` key prefix `hfa_sepsc_review_v1_`. Make it `<package-id>_<cellId>` with a random or hashed package id.
  Otherwise two packages opened in the same browser share answers when their cell ids collide.
- Event polarity. The prototype assumes inward (downward) events, and the README and fixed Y range say so. sIPSCs or
  outward events need the text and the default Y range flipped.
- The README text names cells 06, 13 and 14. Generate those notes from the data: 0-event cells, cells with exclusions,
  and the largest cells.

### Tests worth having
- Round trip: export, decode the snippet in Python (base64 → int16 / 20), and compare to `prep_trace` output at the event,
  to within 0.05 pA.
- Export a cell with 0 events, and an event within 100 ms of a sweep edge (padding).
- Import: a CSV with yes, no, unsure and blank rows, matched to the right candidates, and the counts printed.
- Headless Chrome smoke test: open `START_HERE.html` from `file://`, select a cell, press Y, and check that
  `localStorage` changed. The prototype was checked this way. `--allow-file-access-from-files` was needed only for an
  iframe-based self-test, not for normal use.

---

## 3. Decisions taken for the prototype (check before baking them in)
- The minimum score shown is 0.5, so candidates scored 0.25 to 0.5 are never reviewed. The GUI's `MIN_SHOW` is 0.25.
- "Detected" and "maybe" are judged with the same instructions. The tier is shown only so the reviewer knows the
  computer's guess.
- There is no "add a missed event" in the HTML. The reviewer only confirms or rejects. Finding missed events still needs
  the full app (purple possible-miss markers and the coverage strip). Say this in the docs: counts from the HTML review
  can only fall below or equal the model's, never above.
- One snippet is 300 ms. Long events or bursts may run past the window. *Zoom in* is the other way round, a narrower view.
- The prototype's snippet baseline is the median of the whole 300 ms window. A burst can shift it; this was acceptable for
  judging shape, but the snippet is not meant for measuring amplitude.

## 4. Where the prototype's data lives (user's machine, for reference only)
- Inputs: `~/Desktop/Personal Claude Workspace/ephys/hFA_sEPSC_final/` (`EPSC_files/`, `CNN_v3_candidates/`, manifest
  xlsx).
- Output sent to the collaborator: `.../ephys/hFA_sEPSC_review_package/` and the matching `.zip`.
