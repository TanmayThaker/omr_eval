"""Run the OMR pipeline on every variant and score against ground truth.

Reports per-variant: answer accuracy, roll match, blanks/multi, warnings,
and whether it failed cleanly (exception) vs silently produced garbage.
"""
import os, sys, json, glob, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from omr import process_document, OMRConfig

OUT = r"D:\omr_eval\data\variants"
GT = os.path.join(os.path.dirname(__file__), "ground_truth.json")


def main():
    gt = json.load(open(GT))
    gt_ans = {int(k): v for k, v in gt["answers"].items()}
    cfg = OMRConfig()

    files = sorted(glob.glob(os.path.join(OUT, "*.png")) +
                   glob.glob(os.path.join(OUT, "*.jpg")))
    # marking_quality.png has intentionally-altered bubbles; it's scored by its
    # own test (marking_quality.py), not against the unmodified ground truth.
    files = [f for f in files if "marking_quality" not in os.path.basename(f)]
    print(f"{'variant':24} {'acc%':>6} {'roll':>6} {'blank':>5} {'multi':>5}  warnings/notes")
    print("-" * 92)

    rows = []
    for f in files:
        name = os.path.basename(f)
        try:
            res = process_document(f, cfg)
            correct = sum(1 for a in res.answers if gt_ans.get(a.question) == a.answer)
            acc = 100.0 * correct / len(gt_ans)
            roll_ok = "OK" if res.roll_number == gt["roll"] else res.roll_number[:9]
            c = res.counts()
            note = "; ".join(res.warnings)[:48]
            rows.append((name, acc, roll_ok == "OK"))
            print(f"{name:24} {acc:6.1f} {roll_ok:>6} {c['blank']:5} {c['multi']:5}  {note}")
        except Exception as e:
            rows.append((name, 0.0, False))
            print(f"{name:24} {'ERR':>6} {'-':>6} {'-':>5} {'-':>5}  {type(e).__name__}: {str(e)[:40]}")

    print("-" * 92)
    passed = sum(1 for _, acc, _ in rows if acc >= 99.0)
    rollok = sum(1 for _, _, r in rows if r)
    print(f"answer>=99%: {passed}/{len(rows)}   roll OK: {rollok}/{len(rows)}")
    weak = [(n, a) for n, a, _ in rows if a < 99.0]
    if weak:
        print("WEAK:", ", ".join(f"{n}={a:.0f}%" for n, a in weak))


if __name__ == "__main__":
    main()
