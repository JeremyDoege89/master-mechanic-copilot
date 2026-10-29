"""Stdlib-only PDF 1.4 writer (M6 §3): same content as the HTML report.

No new dependency (AGENTS.md §8): only ``io``/``math``-free stdlib string
work plus ``hashlib``-free byte assembly. One Helvetica font, plain text
lines, one or more letter pages. The HTML print CSS stays the fidelity
fallback for pixel-perfect output.
"""

from __future__ import annotations

_TRANSLATE = {
    "—": "-",
    "–": "-",
    "→": "->",
    "·": "*",
    "≈": "~=",
    "≤": "<=",
    "≥": ">=",
    "×": "x",
    "°": "deg",
    "…": "...",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
}

PAGE_WIDTH, PAGE_HEIGHT = 612, 792
MARGIN_LEFT, MARGIN_TOP = 50, 770
FONT_SIZE, LEADING = 11, 14
LINES_PER_PAGE = 50
WRAP_WIDTH = 95


def clean(text: object) -> str:
    """Transliterate to latin-1 so the PDF stays byte-honest."""
    out = []
    for char in str(text):
        if char in _TRANSLATE:
            out.append(_TRANSLATE[char])
        elif ord(char) < 128 or (160 <= ord(char) <= 255):
            out.append(char)
        else:
            out.append("?")
    return "".join(out)


def wrap(line: str, width: int = WRAP_WIDTH) -> list[str]:
    words = line.split()
    if not words:
        return [""]
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            while len(word) > width:
                lines.append(word[:width])
                word = word[width:]
            current = word
    if current:
        lines.append(current)
    return lines


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content_stream(lines: list[str]) -> bytes:
    parts = ["BT /F1 %d Tf %d TL %d %d Td" % (FONT_SIZE, LEADING, MARGIN_LEFT, MARGIN_TOP)]
    for line in lines:
        parts.append("(%s) Tj T*" % _pdf_escape(line))
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def _report_lines(report: dict) -> list[str]:
    lines = ["Diagnostic report", ""]
    lines.extend(wrap("Conclusion: " + str(report.get("conclusion", ""))))
    lines.append("")
    summaries = report.get("summary_by_level", {})
    for level in ("L1", "L2", "L3"):
        lines.append(f"Summary {level}:")
        lines.extend(wrap(str(summaries.get(level, ""))))
        lines.append("")
    lines.append("Hypotheses:")
    for hyp in report.get("hypotheses_final", []):
        label = hyp.get("label", {}).get("L1", hyp.get("hypothesis_id", ""))
        lines.extend(wrap(f"- {hyp.get('hypothesis_id')}: {label} p={hyp.get('current')}"))
    lines.append("")
    lines.append("Explanation trace:")
    for hyp in report.get("hypotheses_final", []):
        for entry in hyp.get("trace", []) or []:
            lines.extend(
                wrap(
                    f"[{hyp.get('hypothesis_id')}#{entry.get('seq')}] "
                    f"{entry.get('test_id')}: expected {entry.get('expected')}, "
                    f"measured {entry.get('measured')}, LR {entry.get('likelihood_ratio')} "
                    f"({entry.get('lr_source')})"
                )
            )
    lines.append("")
    lines.append("Expected vs measured:")
    for row in report.get("expected_vs_measured", []):
        origin = row.get("origin", "")
        judged = (
            "Signal processing (DSP), not AI"
            if origin == "signal_processing"
            else ("Direct observation" if origin == "direct_observation" else "Deterministic")
        )
        lines.extend(
            wrap(
                f"- {row.get('test_ref')}: expected {row.get('expected')} "
                f"measured {row.get('measured')} -> {row.get('outcome')} "
                f"[{row.get('evaluator')}; {judged}]"
            )
        )
    lines.append("")
    lines.append("Evidence:")
    for ev in report.get("evidence", []) or []:
        badge = "AI-extracted" if ev.get("ai_extracted") else str(ev.get("extractor") or "manual")
        lines.extend(wrap(f"- {ev.get('evidence_id')} ({ev.get('type')}): {badge}"))
    if report.get("fallback_used"):
        lines.append("Second opinion / fallback used (Spark Open).")
    lines.append("")
    lines.append("Safety notes:")
    for note in report.get("safety_notes", []) or []:
        lines.extend(wrap(f"- {note}"))
    lines.append("")
    lines.append("AI review - Muse Wonder (does not change the diagnosis)")
    review = report.get("wonder_review") or {}
    if review:
        lines.extend(wrap(str(review.get("narrative", ""))))
        for gap in review.get("gaps", []) or []:
            lines.extend(wrap(f"- gap: {gap.get('issue')}"))
    else:
        lines.append("AI review unavailable.")
    lines.append("")
    repro = report.get("reproducibility", {})
    lines.extend(
        wrap(
            "Reproducibility: engine %s, KP %s, prompts %s, log %s, models %s"
            % (
                repro.get("engine_version"),
                repro.get("kp_version"),
                repro.get("prompts_sha256"),
                repro.get("log_head_hash"),
                repro.get("model_pins"),
            )
        )
    )
    if report.get("vin_last6"):
        lines.append(f"Vehicle ****{report['vin_last6']}")
    return [clean(line) for line in lines]


def render_pdf(report: dict) -> bytes:
    """Render ``report`` to PDF 1.4 bytes (stdlib only)."""
    lines = _report_lines(report)
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)] or [[]]

    objects: list[bytes] = []
    # 1: catalog, 2: pages. Page/content objects follow; font is last.
    page_refs = []
    obj_no = 3
    for _ in pages:
        page_refs.append((obj_no, obj_no + 1))
        obj_no += 2
    font_no = obj_no

    kids = " ".join(f"{p} 0 R" for p, _c in page_refs)
    objects.append(f"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n".encode("latin-1"))
    objects.append(
        f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>\nendobj\n".encode(
            "latin-1"
        )
    )
    for (page_no, content_no), page_lines in zip(page_refs, pages):
        stream = _content_stream(page_lines)
        objects.append(
            f"{page_no} 0 obj\n<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_no} 0 R >> >> "
            f"/Contents {content_no} 0 R >>\nendobj\n".encode("latin-1")
        )
        objects.append(
            f"{content_no} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream\nendobj\n"
        )
    objects.append(
        f"{font_no} 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n".encode(
            "latin-1"
        )
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_at = len(out)
    total = font_no + 1
    out.extend(f"xref\n0 {total}\n".encode("latin-1"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.extend(
        f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(out)
