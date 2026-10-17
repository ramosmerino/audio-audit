from pathlib import Path

script = r'''#!/usr/bin/env python3
"""
ffmpeg-audio-audit: analiza una pista y guarda resultados en CSV, TXT y SQLite.

Requisitos:
  - Python 3.10+
  - ffmpeg y ffprobe disponibles en PATH
Uso:
  python audio_audit.py "mi pista.wav"
  python audio_audit.py "mi pista.wav" --db audio_audit.sqlite --txt resultados.txt
  python audio_audit.py carpeta/ --recursive
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    tested_at TEXT NOT NULL,
    duration_seconds REAL,
    duration_hms TEXT,
    sample_peak_dbfs REAL,
    true_peak_dbtp REAL,
    clips INTEGER,
    true_peak_clips INTEGER,
    rms_i_dbfs REAL,
    lra_lu REAL,
    lufs_m_max REAL,
    lufs_s_max REAL,
    lufs_i REAL,
    sample_rate INTEGER,
    channels INTEGER,
    codec TEXT,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT
);
"""

CSV_FIELDS = [
    "id", "file_path", "file_name", "tested_at", "duration_seconds",
    "duration_hms", "sample_peak_dbfs", "true_peak_dbtp", "clips",
    "true_peak_clips", "rms_i_dbfs", "lra_lu", "lufs_m_max",
    "lufs_s_max", "lufs_i", "sample_rate", "channels", "codec",
    "status", "error",
]

def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")

def parse_num(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None

def parse_duration(value: Optional[str]) -> Optional[float]:
    if not value or value == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None

def hms(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    ms = round(seconds * 1000)
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"

def ffprobe_info(path: Path) -> dict:
    p = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:format_tags=encoder:stream=codec_name,sample_rate,channels",
        "-of", "json", str(path)
    ])
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or "ffprobe no pudo leer el archivo")
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams", [])
    audio = next((s for s in streams if "sample_rate" in s or "channels" in s), {})
    return {
        "duration": parse_duration(data.get("format", {}).get("duration")),
        "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        "channels": audio.get("channels"),
        "codec": audio.get("codec_name"),
    }

def analyze(path: Path) -> dict:
    info = ffprobe_info(path)

    # ebur128 calcula LUFS-I, LRA, true peak y máximos momentáneo/corto plazo.
    e = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-filter_complex", "ebur128=peak=true", "-f", "null", "-"
    ])
    elog = e.stderr
    if e.returncode != 0:
        raise RuntimeError(elog.strip()[-1200:] or "ffmpeg ebur128 falló")

    # astats aporta sample peak y RMS. RMS-I aquí se define como el RMS global
    # de toda la pista (RMS level dB), no como una métrica LUFS normalizada.
    a = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "astats=metadata=0:reset=0", "-f", "null", "-"
    ])
    alog = a.stderr
    if a.returncode != 0:
        raise RuntimeError(alog.strip()[-1200:] or "ffmpeg astats falló")

    # Resumen ebur128: I: -14.0 LUFS / LRA: 5.0 LU / Threshold: ...
    lufs_i = parse_num(r"^\s*I:\s*(-?[\d.]+)\s*LUFS", elog)
    lra = parse_num(r"^\s*LRA:\s*([\d.]+)\s*LU", elog)
    true_peak = parse_num(r"^\s*Peak:\s*(-?[\d.]+)\s*dBFS", elog)
    # En la tabla de ebur128, M y S son los valores momentáneo y short-term.
    # Los máximos se extraen de las líneas finales de resumen si están presentes.
    lufs_m_max = parse_num(r"^\s* momentary:\s*(-?[\d.]+)\s*LUFS", elog)
    lufs_s_max = parse_num(r"^\s* short term:\s*(-?[\d.]+)\s*LUFS", elog)

    # En algunas versiones de FFmpeg, los máximos aparecen como "M:" y "S:"
    # dentro de la sección de resumen. Fallback: buscar las etiquetas del resumen.
    if lufs_m_max is None:
        lufs_m_max = parse_num(r"^\s*M:\s*(-?[\d.]+)\s*LUFS", elog)
    if lufs_s_max is None:
        lufs_s_max = parse_num(r"^\s*S:\s*(-?[\d.]+)\s*LUFS", elog)

    # astats suele emitir un bloque por canal y otro "Overall". Capturamos el
    # último valor Overall para RMS y peak. El sample peak se expresa en dBFS.
    overall = alog.rsplit("Overall", 1)[-1]
    sample_peak = parse_num(r"Peak level dB:\s*(-?[\d.]+)", overall)
    rms_i = parse_num(r"RMS level dB:\s*(-?[\d.]+)", overall)

    # ebur128 reporta true peak, pero FFmpeg no ofrece un contador universal de
    # muestras recortadas. Los contadores siguientes son estimaciones por
    # umbral: clips = muestras con amplitud >= 0 dBFS y TP clips = muestras
    # reconstruidas estimadas >= 0 dBTP. No se inventan si el filtro no lo mide.
    # El filtro astats no expone un contador fiable de clipped samples; por eso
    # se dejan como NULL hasta implementar un contador de muestras específico.
    clips = None
    true_peak_clips = None

    return {
        **info,
        "sample_peak_dbfs": sample_peak,
        "true_peak_dbtp": true_peak,
        "clips": clips,
        "true_peak_clips": true_peak_clips,
        "rms_i_dbfs": rms_i,
        "lra_lu": lra,
        "lufs_m_max": lufs_m_max,
        "lufs_s_max": lufs_s_max,
        "lufs_i": lufs_i,
    }

def ensure_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA)

def append_text(txt_path: Path, row: dict) -> None:
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not txt_path.exists() or txt_path.stat().st_size == 0
    with txt_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, delimiter="\t", extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerow(row)

def save_db(db_path: Path, row: dict) -> int:
    ensure_db(db_path)
    columns = [c for c in CSV_FIELDS if c != "id"]
    placeholders = ",".join("?" for _ in columns)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO measurements ({','.join(columns)}) VALUES ({placeholders})",
            [row.get(c) for c in columns],
        )
        return int(cur.lastrowid)

def main() -> int:
    parser = argparse.ArgumentParser(description="Mide loudness, peaks y RMS con FFmpeg.")
    parser.add_argument("path", help="Archivo de audio o carpeta")
    parser.add_argument("--db", default="audio_audit.sqlite", help="Ruta de base SQLite")
    parser.add_argument("--txt", default="audio_audit.tsv", help="Archivo tabulado de resultados")
    parser.add_argument("--recursive", action="store_true", help="Buscar audio recursivamente en carpetas")
    args = parser.parse_args()

    for exe in ("ffmpeg", "ffprobe"):
        if shutil.which(exe) is None:
            print(f"Error: no se encontró {exe} en PATH.", file=sys.stderr)
            return 2

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f"Error: no existe: {target}", file=sys.stderr)
        return 2

    if target.is_file():
        files = [target]
    elif target.is_dir():
        patterns = ("*.wav", "*.flac", "*.mp3", "*.m4a", "*.aac", "*.aiff", "*.aif", "*.ogg", "*.opus", "*.wma")
        files = []
        for pattern in patterns:
            files.extend(target.rglob(pattern) if args.recursive else target.glob(pattern))
        files = sorted(set(files))
    else:
        files = []

    if not files:
        print("No se encontraron archivos de audio compatibles.", file=sys.stderr)
        return 1

    db_path, txt_path = Path(args.db), Path(args.txt)
    ensure_db(db_path)
    failures = 0

    for file in files:
        tested_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        row = {
            "file_path": str(file),
            "file_name": file.name,
            "tested_at": tested_at,
            "status": "ok",
            "error": None,
        }
        try:
            metrics = analyze(file)
            row.update(metrics)
            row["duration_hms"] = hms(row.get("duration"))
            print(
                f"OK  {file.name} | {row.get('lufs_i')} LUFS-I | "
                f"{row.get('true_peak_dbtp')} dBTP | {row.get('duration_hms')}"
            )
        except Exception as exc:
            failures += 1
            row["status"] = "error"
            row["error"] = str(exc).replace("\n", " ")[:2000]
            print(f"ERROR {file}: {row['error']}", file=sys.stderr)

        row_id = save_db(db_path, row)
        row["id"] = row_id
        append_text(txt_path, row)

    print(f"\nRegistros guardados: {len(files)}")
    print(f"SQLite: {db_path.resolve()}")
    print(f"TXT/TSV: {txt_path.resolve()}")
    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
'''
path = Path("/mnt/data/audio_audit.py")
path.write_text(script, encoding="utf-8")
print(f"Creado: {path} ({path.stat().st_size} bytes)")