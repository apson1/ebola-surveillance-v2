"""Render the genuine current outputs to terminal-styled PNGs for the writeup.

These are programmatic renders of real output (not OS screen captures): the live
`run_scan` alert and the `run_evals` summary. Requires Pillow (dev-only; not a runtime dep).

Run from the repo root:  python -m scripts.make_doc_images
"""
import os

from PIL import Image, ImageDraw, ImageFont

from src.orchestrator import run_scan
from evals.run_evals import run_evals

OUT_DIR = "docs/images"
FONT_PATH = "C:/Windows/Fonts/consola.ttf"
BG = (13, 17, 23)        # GitHub-dark
FG = (201, 209, 217)
ACCENT = (63, 185, 80)   # green prompt
PAD = 24
SIZE = 18


def _render(lines, out_path, prompt=None):
    font = ImageFont.truetype(FONT_PATH, SIZE)
    body = ([f"$ {prompt}", ""] if prompt else []) + lines
    line_h = SIZE + 7
    width = max((font.getlength(ln) for ln in body), default=200)
    img = Image.new("RGB", (int(width) + 2 * PAD, line_h * len(body) + 2 * PAD), BG)
    draw = ImageDraw.Draw(img)
    y = PAD
    for ln in body:
        color = ACCENT if (prompt and ln.startswith("$ ")) else FG
        draw.text((PAD, y), ln, font=font, fill=color)
        y += line_h
    img.save(out_path)
    print("wrote", out_path)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    alert = run_scan("data/incoming/incoming_multi_signal.json")["alert"]
    # wrap nothing; keep verbatim lines
    _render(
        alert.split("\n"),
        os.path.join(OUT_DIR, "alert_example.png"),
        prompt='python -c "from src.orchestrator import run_scan; print(run_scan(\'data/incoming/incoming_multi_signal.json\')[\'alert\'])"',
    )

    report = run_evals(results_path=None)  # don't overwrite the committed artifact
    eval_lines = [f"[{'PASS' if r['passed'] else 'FAIL'}] {r['scenario']}" for r in report["results"]]
    s = report["summary"]
    eval_lines += ["", f"{s['passed']}/{s['total']} scenarios passed. Artifact: evals/eval_results.json"]
    _render(eval_lines, os.path.join(OUT_DIR, "eval_results.png"), prompt="python -m evals.run_evals")


if __name__ == "__main__":
    main()
