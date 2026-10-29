# sEPSC event analysis: guide for a collaborator

**Only checking events someone else already detected?** You do not need any of this. Open `START_HERE.html` in the package you were sent
(see `README.txt` inside it): answer Yes / No / Not sure per event, then send back the CSV it downloads.

**Analysing your own recordings?** Follow the steps below. The trained model is included, so there is nothing separate to download.

## 1. Install (once, about 10 minutes)

Python 3.9 or newer. In a terminal:

```bash
git clone https://github.com/kkaganovsky/Claude-Cloud
cd Claude-Cloud
git checkout claude/loving-euler-nqbzva           # current work is on this branch
cd ephys-suite
python -m venv .venv
source .venv/bin/activate                          # Windows: .venv\Scripts\activate
pip install -e ".[ml]"                             # the app + torch and scikit-learn (large download)
```

If the clone is refused, ask for access. The model file is a Python pickle (it can run code when loaded): use only the copy that comes with
the repository. It was saved with scikit-learn 1.9.1, torch 2.14.1 and numpy 2.4.6; if loading fails, `pip install "scikit-learn==1.9.1"`.
`ephys_suite/ml/models/MODEL_CARD.md` has its training data, performance (precision about 0.90, recall about 0.84 on held-out cells) and
threshold (0.866; 0.90 for noisy recordings). **Weak spot: very noisy, low-rate cells. Check those by hand.**

## 2. Lay out your recordings

Keep each cell's files together. For the access-resistance check, sEPSC recordings go in a folder with `EPSC` in its name and test pulses
in a folder named `CM`, e.g. `line 14/cell 3/EPSC/sEPSC.abf`.

## 3. Check access resistance (Ra < 25 MOhm)

```bash
ephys-suite-qc "path/to/folder/that/holds/the/cells"
```

Prints a verdict per cell and writes `Ra_QC_<date>.xlsx` there (the same check is the **Access resistance QC** tab). A "drift" flag means Ra
changed by 20 % or more across sweeps; it is separate from the limit.

## 4. Run the model (once per recording)

```bash
ephys-suite-classify "…/EPSC/sEPSC.abf" --out "…/EPSC/candidates"
```

Writes `candidates/sEPSC.candidates.csv` and prints how many events were accepted. Good to know:

- Events deeper than 100 pA or slow to recover are flagged `large_slow` and **not accepted** (failed spikes, escape currents); you can still see them.
- Noisy recordings are announced in the printout and use the stricter threshold.
- Events in the first 30 ms or last 120 ms of a sweep are not scored. **Test pulses are not masked:** check them before you count frequency.

## 5. Review in the app

```bash
ephys-suite
```

**File > Open .abf**, go to the **Events** tab. Because the candidates file sits beside the recording (or in `candidates/`), the app asks
whether to load it: **Yes**. Otherwise use *Review model events > Load model candidates (CSV)…*.

You see red dots on accepted events, orange rings on candidates the model was unsure about, a **Verify** panel on the right and an overview
strip with a green line for the parts you have actually looked at. The trace is shown through a 3 kHz low-pass (what the model sees); the faint
grey line is the raw trace. Buttons for 1 kHz and 500 Hz help if it is too noisy to judge. Display only: measurements use the raw data.

| Key | Action |
|---|---|
| **Y** / Enter | confirm the event (or accept the candidate) |
| **N** | reject the event (or dismiss the candidate) |
| **. / ,** | skip / go back |
| **G** | gallery: 20 events per page; click one to flip it, Enter decides the rest of the page |
| click an orange ring | accept it; click an event twice to remove it |
| **X / Z** | next / previous window |
| **V** | jump to the next stretch you have not looked at |
| **F** | jump to a place a second detector flags as a possible missed event |
| **A** | add an event by clicking it |
| **M** | mark the sweep fully reviewed |
| **Ctrl+Z / Ctrl+Y** | undo / redo (**F1** lists every key) |

The list starts with the most doubtful items. A real event is a fast deflection followed by a slower, smooth return; a thin single spike or a
wiggle the size of the noise is not one. The model cannot add events it never proposed, so also page through the trace with **V** (look, **A** to
add, **V** again) and check the **F** suggestions: the coverage strip is the record that you looked everywhere.

## 6. Save and send back

Progress autosaves next to the candidates file (`*.review_progress.json`); *Resume saved review* picks it up later. When finished:

1. *Review model events > Save review…* writes `<recording>_review.csv` (+ `.json`) into the recording's folder. `final_event = 1` marks the events that count.
2. *Export events CSV…* writes the measured events (peak, amplitude, rise, decay, FWHM, interval).
3. Send back the review CSV and `.json`, the `.review_progress.json` and the events CSV.

Counts depend on how strictly each person applies "real event", so agree the rule before comparing cells.

## Troubleshooting

| Problem | What to do |
|---|---|
| `command not found: ephys-suite` | activate the environment: `source .venv/bin/activate` |
| `ModuleNotFoundError: torch` | `pip install -e ".[ml]"` |
| Error loading the model (sklearn / torch / numpy) | version mismatch, see step 1 |
| The app does not offer the events file | it must be named `<recording>.candidates.csv` and sit beside the recording or in `candidates/` or `review/` |
| Window too big or small | it fits your screen and remembers its size; **View** menu shows / hides panels, *Reset layout* undoes it |
| Clicking an orange ring does nothing | `git pull` (a bug in earlier versions) |
