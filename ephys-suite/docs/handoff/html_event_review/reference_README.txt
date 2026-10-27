sEPSC EVENT REVIEW - how to use
================================

Nothing to install.

1. Unzip this folder anywhere (for example your Desktop). Keep the files together.
2. Double-click START_HERE.html. It opens in your web browser (Chrome works best).
3. Click a cell on the left (Cell 03 to Cell 14).
4. For each event, look at the trace and press:
      Y  (or the green button)   = yes, it is a real event
      N  (or the red button)     = no, it is noise
      U  (or the purple button)  = not sure
      Left / Right arrow         = go back / forward
   The next event appears automatically.
5. Your answers save automatically in the browser, so you can close it and continue later.
   Use the SAME browser on the SAME computer each time.
6. When you finish, or at the end of each day, click "Download results for ALL cells"
   and send the CSV file (it goes to your Downloads folder) back by email.

What a real event looks like
----------------------------
- A fast downward drop, then a slower, smooth return to the baseline.
- NOT an event: a single very thin spike, or a wiggle no bigger than the surrounding noise.
- The dashed line marks where the computer found the event.
- "Detected" (green) = the computer thinks it is an event. "Maybe" (orange) = the computer is unsure.
  Judge both the same way.
- Tick "Smoother view" if the trace is too noisy to judge, and "Zoom in" to see the event's shape.

Notes
-----
- Cell 06 has nothing to review.
- Cell 13 skips 100-130 s on purpose (a very noisy stretch).
- Cells 13 and 14 are noisy and have the most events. Do them last.

What is in the folder
---------------------
START_HERE.html   the review page
data/             the event snippets the page shows (do not edit)
recordings/       the original .abf recordings (not needed for the review, kept for reference)
labels/           the computer's event lists (not needed for the review, kept for reference)
