"""Re-key script test: old cache entries gain new keys without re-recording."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rekey_llm_cache.py"

ORIG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4  # 1028 fake-JPEG bytes


def _old_spark_record() -> dict:
    b64 = base64.b64encode(ORIG).decode()
    return {
        "kind": "llm_call",
        "model": "muse-spark-1.3",
        "request_sha256": "old-spark-key",
        "request": {
            "op": "spark.components.v1",
            "input": [{"role": "user", "content": "hi"}],
            "image": {
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{b64[:32]}...<{len(ORIG)} bytes>",
                "_bytes_len": len(ORIG),
            },
        },
        "output_text": '{"components": [], "anomalies": []}',
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "cost_usd": 0.001,
        "cached": False,
    }


def _old_sam_record() -> dict:
    return {
        "kind": "sam_call",
        "model": "sam-3.1",
        "request_sha256": "old-sam-key",
        "request": {
            "op": "sam.segment_image.v1",
            "concept": "battery",
            "prompt": "battery",
            "image_sha256": hashlib.sha256(ORIG).hexdigest(),
            "image_bytes": len(ORIG),
            "orig_bytes": len(ORIG),
        },
        "output_text": "",
        "usage": {"images_processed": 1},
        "cost_usd": 0.0025,
        "cached": False,
    }


def test_rekey_script_migrates_old_entries(tmp_path):
    cache = tmp_path / "llm_cache"
    cache.mkdir()
    (cache / "old-spark-key.json").write_text(json.dumps(_old_spark_record()))
    (cache / "old-sam-key.json").write_text(json.dumps(_old_sam_record()))
    original = tmp_path / "orig.jpg"
    original.write_bytes(ORIG)

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(cache), "--original", str(original)],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "rekeyed=2" in proc.stdout, proc.stdout

    # Old files stay (no --prune-old); new files carry outputs + new keys.
    from mmc.meta.client import request_sha256
    from mmc.meta.sam import image_cache_key

    spark_new = json.loads((cache / "old-spark-key.json").read_text())
    assert spark_new["output_text"] == '{"components": [], "anomalies": []}'
    new_files = [p for p in cache.glob("*.json") if p.stem not in ("old-spark-key", "old-sam-key")]
    assert len(new_files) == 2
    by_sha = {p.stem: json.loads(p.read_text()) for p in new_files}

    spark_records = [r for r in by_sha.values() if r["request"].get("op", "").startswith("spark.")]
    assert len(spark_records) == 1
    spark = spark_records[0]
    assert spark["request"]["image"]["sha256"] == hashlib.sha256(ORIG).hexdigest()
    assert by_sha[request_sha256({"model": "muse-spark-1.3", **spark["request"]})] is spark

    sam_records = [r for r in by_sha.values() if r["request"].get("op") == "sam.segment_image.v1"]
    assert len(sam_records) == 1
    sam = sam_records[0]
    assert sam["request"]["orig_sha256"] == hashlib.sha256(ORIG).hexdigest()
    assert sam["usage"] == {"images_processed": 1}  # preserved, not re-recorded
    want = request_sha256(
        {"model": "sam-3.1", **image_cache_key("battery", "battery", ORIG)}
    )
    assert want in by_sha
