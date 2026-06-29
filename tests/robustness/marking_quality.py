"""Validate the mark-decision logic against real student-marking conditions:
blank (unanswered), double-marked, partial fill, check/X marks, and erasure.

Uses the clean sheet's detected geometry to edit specific bubbles, then checks
the pipeline reports the expected status for each edited question and keeps the
untouched ones correct.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
import numpy as np
import cv2

from omr import process_document, OMRConfig

PDF = r"D:\omr_eval\OMR_sheet_2467.pdf"
OUT = r"D:\omr_eval\data\variants"
OPT = "ABCDE"


def centers(geom, cfg, q):
    bi = (q - 1) // cfg.rows_per_block
    ri = (q - 1) % cfg.rows_per_block
    return geom.block_cols[bi], geom.block_rows[bi][ri]


def erase(img, cx, cy):  # white out a bubble interior (leave printed ring)
    cv2.ellipse(img, (int(cx), int(cy)), (15, 10), 0, 0, 360, 255, -1)


def fill(img, cx, cy):   # solid mark
    cv2.ellipse(img, (int(cx), int(cy)), (16, 11), 0, 0, 360, 0, -1)


def check(img, cx, cy):  # check/X mark instead of a full fill
    cv2.line(img, (int(cx - 12), int(cy)), (int(cx - 3), int(cy + 8)), 0, 3)
    cv2.line(img, (int(cx - 3), int(cy + 8)), (int(cx + 12), int(cy - 9)), 0, 3)


def main():
    cfg = OMRConfig()
    clean = process_document(PDF, cfg)
    geom, gray = clean.geometry, clean.gray
    base = gray.copy()
    gt = {a.question: a.answer for a in clean.answers}

    cases = []  # (name, edit_fn, question, expected)

    # blanks: erase the marked bubble -> BLANK
    for q in (5, 60, 123, 200):
        cases.append(("blank", q, "BLANK"))
    # doubles: add a second mark -> MULTI
    for q in (10, 75, 150):
        cases.append(("double", q, "MULTI"))
    # partial: half-erase the mark -> should still read the same answer (or low conf)
    for q in (20, 90):
        cases.append(("partial", q, gt[q]))
    # check marks: faint X instead of fill -> ideally still the marked option
    for q in (30, 111):
        cases.append(("check", q, "CHECK"))  # accept answer or BLANK, see below
    # erasure: erase original, mark a different option -> the new option
    for q in (40, 130):
        cases.append(("erasure", q, "NEW"))

    img = base.copy()
    expected = {}
    for kind, q, exp in cases:
        cols, cy = centers(geom, cfg, q)
        cur = OPT.index(gt[q])
        if kind == "blank":
            erase(img, cols[cur], cy); expected[q] = "BLANK"
        elif kind == "double":
            other = (cur + 2) % 5
            fill(img, cols[other], cy); expected[q] = "MULTI"
        elif kind == "partial":
            # erase a right-edge sliver -> still mostly filled, should count
            cv2.rectangle(img, (int(cols[cur] + 6), int(cy - 12)),
                          (int(cols[cur] + 18), int(cy + 12)), 255, -1)
            expected[q] = gt[q]
        elif kind == "check":
            erase(img, cols[cur], cy); check(img, cols[cur], cy)
            expected[q] = ("ANY", gt[q])      # answer OR blank acceptable
        elif kind == "erasure":
            erase(img, cols[cur], cy)
            new = (cur + 1) % 5
            fill(img, cols[new], cy)
            expected[q] = OPT[new]

    path = os.path.join(OUT, "marking_quality.png")
    cv2.imwrite(path, img)
    res = process_document(path, cfg)
    got = {a.question: a for a in res.answers}

    print(f"{'q':>4} {'kind':9} {'expected':9} {'got':6} {'conf':>5}  result")
    print("-" * 56)
    edited_qs = {q for _, q, _ in cases}
    ok = True
    for kind, q, _ in cases:
        a = got[q]
        exp = expected[q]
        if isinstance(exp, tuple):  # check marks: lenient
            passed = a.answer in (exp[1], "BLANK")
            exps = f"{exp[1]}|BLK"
        else:
            passed = a.answer == exp
            exps = exp
        ok &= passed
        print(f"{q:>4} {kind:9} {exps:9} {a.answer:6} {a.confidence:5.2f}  {'OK' if passed else 'FAIL'}")

    # untouched questions must remain correct
    untouched_wrong = [q for q in gt if q not in edited_qs and got[q].answer != gt[q]]
    print("-" * 56)
    print(f"untouched questions wrong: {len(untouched_wrong)} {untouched_wrong[:10]}")
    print("RESULT:", "PASS" if (ok and not untouched_wrong) else "FAIL")


if __name__ == "__main__":
    main()
