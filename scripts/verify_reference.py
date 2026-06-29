"""Verify the OMR pipeline end-to-end on the reference sheet.

Run:  python scripts/verify_reference.py
Asserts the grid is fully detected and prints a decode summary; writes
overlay/CSV/JSON artifacts next to the PDF for visual inspection.
"""
import sys, os, json, csv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from omr import process_document, OMRConfig  # noqa: E402

PDF = r"D:\omr_eval\OMR_sheet_2467.pdf"
OUT = r"D:\omr_eval\data\verify"

# First 25 answers established from manual inspection of the reference crop.
EXPECTED_FIRST_25 = list("DBCAA DDDBD ACCCA DDBAD BACDD".replace(" ", ""))
EXPECTED_ROLL = "107030379"


def main():
    os.makedirs(OUT, exist_ok=True)
    cfg = OMRConfig()
    res = process_document(PDF, cfg)

    print("=== GEOMETRY ===")
    print("blocks:", [len(c) for c in res.geometry.block_cols],
          "rows/block:", [len(r) for r in res.geometry.block_rows])
    print("bubble w/h:", res.geometry.bubble_w, res.geometry.bubble_h)
    print("skew applied:", res.skew_applied)
    print("roll cols/rows:", len(res.geometry.roll_cols), len(res.geometry.roll_rows))
    if res.warnings:
        print("WARNINGS:", res.warnings)

    print("\n=== COUNTS ===", res.counts())
    print("roll_number:", res.roll_number, "conf:", res.roll_confidence)

    got_first = "".join(a.answer for a in res.answers[:25])
    exp_first = "".join(EXPECTED_FIRST_25)
    print("\nfirst25 expected:", exp_first)
    print("first25 decoded :", got_first)

    # --- checks ---
    ok = True
    if res.counts()["total"] != cfg.num_questions:
        print("FAIL: question count"); ok = False
    if res.counts()["marked"] != cfg.num_questions:
        print(f"WARN: only {res.counts()['marked']} marked (expected all in this sheet)")
    if got_first != exp_first:
        print("FAIL: first-25 answers mismatch"); ok = False
    if res.roll_number != EXPECTED_ROLL:
        print(f"WARN: roll decoded {res.roll_number!r}, expected {EXPECTED_ROLL!r}")

    # --- artifacts ---
    with open(os.path.join(OUT, "overlay.png"), "wb") as f:
        f.write(res.overlay_png(cfg))
    with open(os.path.join(OUT, "result.json"), "w") as f:
        json.dump(res.to_dict(), f, indent=2)
    with open(os.path.join(OUT, "answers.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "answer", "confidence"])
        for a in res.answers:
            w.writerow([a.question, a.answer, a.confidence])
    print("\nArtifacts written to", OUT)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
