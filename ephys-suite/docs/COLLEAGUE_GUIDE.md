# sEPSC event analysis: guide for a collaborator

Two ways to take part. Pick one.

| | **Route A: review in a browser** | **Route B: run the whole analysis yourself** |
|---|---|---|
| Install | nothing | Python + this repository + the model file |
| You get from us | a folder or .zip (`START_HERE.html`) | the model file (`.pkl`) and your own recordings |
| You do | answer Yes / No / Not sure for each event the model proposed, send back one CSV | run the model, review its events in the app, look for missed events, export results |
| Use it when | you are checking events someone else already detected | you have your own recordings to analyse |

Route A is described in `README.txt` inside the package and takes five minutes to learn. The rest of this guide is Route B.

---

## Route B

### 1. Install (once, about 10 minutes)

You need Python 3.9 or newer (3.11 is what this was developed on). In a terminal:

```bash
git clone https://github.com/kkaganovsky/Claude-Cloud
cd Claude-Cloud
git checkout claude/loving-euler-nqbzva          # the current work is on this branch, not on main
cd ephys-suite
python -m venv .venv
source .venv/bin/activate                         # Windows: .venv\Scripts\activate
pip install -e ".[ml]"                            # the app plus torch and scikit-learn for the model
```

If `git clone` is refused, ask the repository owner for access (the repository may be private).
Check the install: `ephys-suite-classify --help` should list `--model` and `--out`, and `ephys-suite` (step 6) should open a window.

### 2. Get the model file

> **TO BE FILLED IN BY THE OWNER BEFORE SENDING:** where to download the model file, its exact name
> (the README example calls it `final_models_v2.pkl`), and its size. The model file is **not** in the GitHub repository.
> Send it by a shared drive or institutional file transfer, not by committing it to GitHub.
>
> Download link / location: `__________________`
> File name: `__________________`   Checksum (optional, `shasum -a 256 file`): `__________________`

Save it somewhere permanent, for example `~/ephys-models/final_models_v2.pkl`, and note the full path.

Two cautions:

- **The model file is a Python pickle.** Opening a pickle can run code. Only use a file that came directly from the owner, never one from an unknown source.
- **Library versions matter for pickles.** If loading fails with a message about `sklearn` or `torch`, ask the owner which versions the model was saved with and install those (`pip install "scikit-learn==X.Y.Z"`). The versions are not recorded in this repository, so I cannot tell you them.

### 3. Organise your recordings

Put the `.abf` files in a folder and keep the files for one cell together. The model's results go into a `candidates/` subfolder beside the recording:

```
my_cell/
  sEPSC.abf
  candidates/            <- created in step 4
```

The access-resistance check (step 5) finds recordings by folder name: sEPSC recordings must sit in a folder with `EPSC` in its name and
test-pulse recordings in a folder named `CM` (for example `line 14/cell 3/EPSC/…`). See "Access-resistance QC" in the README for the rule.

### 4. Run the model (once per recording)

```bash
ephys-suite-classify "my_cell/sEPSC.abf" --model ~/ephys-models/final_models_v2.pkl --out "my_cell/candidates"
```

It prints how many candidates were accepted and the threshold it used, and writes `my_cell/candidates/sEPSC.candidates.csv`.
Things to know about what it does:

- It proposes candidates, scores each one, and marks the ones above the threshold as `accepted`. Candidates scored below 0.25 are not written.
- Events outside the shapes the model was trained on (deeper than 100 pA, or slow to recover; failed spikes, escape currents) are flagged `large_slow` and are **not** accepted. They stay visible for you to look at.
- Recordings with high noise (median pre-event SD of 1.86 pA or more) are marked "NOISY recording" in the printout and use a stricter threshold (0.90).
- Events in the first 30 ms or last 120 ms of a sweep are not scored.
- **Test pulses are not masked.** Check any test pulse in your sweeps before you count frequency.

### 5. Check access resistance first

The Ra QC takes a minute and tells you whether a cell should be analysed at all (limit Ra < 25 MOhm):

```bash
ephys-suite-qc "path/to/folder/with/your/cells"
```

It prints a verdict per cell and writes `Ra_QC_<date>.xlsx` into that folder. In the app the same check is the **Access resistance QC** tab.
The folder you give is the one that holds the cells, laid out as above. A "drift" flag is separate from the limit: it means Ra changed by 20 % or more across sweeps.

### 6. Open the recording in the app

```bash
ephys-suite
```

**File > Open .abf**, choose your recording, and go to the **Events** tab.
Because `candidates/sEPSC.candidates.csv` sits beside the recording, the app asks *"Found an events file for this recording ... Load it for review?"*. Answer **Yes**.
(If you do not see the question: Review model events > **Load model candidates (CSV)...**.)

You now see the trace with:

- **red dots** on events the model accepted,
- **orange rings** on candidates it did not accept but is unsure about,
- the **Verify** panel on the right,
- the **overview strip** under the trace showing every sweep, with a green line for the parts you have actually looked at.

The trace is shown through a 3 kHz low-pass by default (the same filter the model uses). The faint grey line underneath is the raw trace. This is display only; measurements use the raw data. **1 kHz** and **500 Hz** buttons are under the view controls if the trace is too noisy to judge.

### 7. Review the model's events

The default list is **Uncertain**: accepted events and borderline candidates in one list, the most doubtful first.

| Key | Action |
|---|---|
| **Y** or Enter | confirm the event (or accept the candidate) |
| **N** | reject the event (or dismiss the candidate) |
| **.** / **,** | skip / go back |
| **G** | gallery: 20 events on one page. Click a thumbnail to flip it; Enter decides the rest of the page |
| mouse | click an **orange ring** to accept it; click an event twice to remove it |
| **Ctrl+Z / Ctrl+Y** | undo / redo |

The green line in the small window is a typical event scaled to the current amplitude, so a wrong shape stands out.
A real event is a fast deflection followed by a slower, smooth return. A thin single spike, or a wiggle the size of the noise, is not one.
Press **F1** for the full list of keys.

### 8. Look for events the model missed

The model cannot add events you can see but it never proposed. Two helpers:

- **V** jumps to the next stretch of trace you have not looked at yet. A stretch only counts as viewed when the window is 2.5 s wide or narrower and stays on screen briefly. Looking for misses is a loop: look, **A** to add what you see, **V**.
- **F** jumps to places where a second, more permissive detector sees something the model did not propose. **Y** adds it, **N** dismisses it. This is only a second detector, not proof, so the green coverage strip is the real record that you looked everywhere.

Press **M** when a sweep is fully reviewed.

### 9. Save and send back

Progress saves automatically next to the candidates file (`*.review_progress.json`), so you can close the app and **Resume saved review** later.

When finished:

1. Review model events > **Save review...**. It writes `<recording>_review.csv` and a `.json` into the recording's folder.
2. **Export events CSV...** writes the measured events (peak, amplitude, rise, decay, FWHM, interval).
3. Send back: `<recording>_review.csv`, its `.json`, the `.review_progress.json`, and the events CSV.

`final_event = 1` in the review CSV marks the events that count. The event count and frequency also appear above the trace.

### 10. Things to be honest about

- The model's scores are only as good as its training. Check quiet cells (few small events) by eye.
- Review is on your judgement of each event. Counts depend on how strictly each reviewer applies "real event", so agree on the rule with the owner before comparing cells.
- This guide was written without running the real model: the model file was not available where the software was developed, so the `ephys-suite-classify` step has not been tested with real weights. Report anything that looks wrong, with the recording and the time in the sweep.

### Troubleshooting

| Problem | What to do |
|---|---|
| `command not found: ephys-suite` | activate the virtual environment: `source .venv/bin/activate` |
| `ModuleNotFoundError: torch` | you installed without the model parts: `pip install -e ".[ml]"` |
| Error when loading the model about sklearn / torch | version mismatch, see step 2 |
| The app does not offer the events file | the CSV must be named `<recording>.candidates.csv` and sit beside the recording or in a `candidates/` or `review/` subfolder |
| The window is too big or small for your screen | it sizes itself to your screen and remembers it; the **View** menu shows and hides panels, *Reset layout* undoes it |
| A click on an orange ring does nothing | update to the latest commit of the branch (`git pull`); this was a bug in earlier versions |
