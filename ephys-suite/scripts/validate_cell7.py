"""Run the suite on the example cell and compare with the user's reference spreadsheet row."""
import sys
import numpy as np
from ephys_suite.report import build_cell_report, compare, COLUMNS

D = sys.argv[1]
REF = {"Max AP": 80, "Adaptation": 0.59235669, "Rheobase (pA)": 401.549931, "FS Latency (ms)": 593.1,
       "steady state change in Voltage (sag)": -14.633664, "Input Resistance": 28.3091705, "Sag mV": -1.3969184,
       "Sag Ratio": 0.08714084, "Tolias ratio": 1.09545924, "Amplitude (mV)": 78.6865234, "Threshold (mV)": -47.503662,
       "Corr Threshold": -57.183662, "Rise Time (ms)": 0.325, "Decay Time (ms)": 0.855, "Half-Width (ms)": 0.73,
       "fAHP (mV)": -21.557617, "mAHP (mV)": -17.205811, "Cm (pF)": 750.946529, "RMP original": -73.56597086,
       "RMP corrected": -83.24597086, "tau (ms)": 21.2586734}
rep = build_cell_report(D + "IO.abf", spont_path=D + "spontanous?.abf")
print("%-38s %14s %14s %12s  %s" % ("column", "suite", "your value", "diff", ""))
for k, v, rv, d, flag in compare(rep.row, REF):
    print("%-38s %14.6f %14.6f %12.6f  %s" % (k, v, rv, d, flag))
print("\nDECISIONS")
for k, v in rep.decisions:
    print(f"- {k}: {v}")
