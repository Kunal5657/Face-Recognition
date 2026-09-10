"""SmartFace Attendance MVP.

The application intentionally uses only a Haar cascade and a small, normalized
grayscale face descriptor.  This keeps the demo easy to install on Windows
while making the recognition pipeline understandable and replaceable.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE = Path(os.getenv("SMARTFACE_DATABASE", str(DATA_DIR / "smartface.db")))
FACE_SIZE = (32, 32)
DEFAULT_THRESHOLD = float(os.getenv("FACE_MATCH_THRESHOLD", "0.42"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config["JSON_SORT_KEYS"] = False


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                email TEXT DEFAULT '',
                embedding TEXT NOT NULL,
                image_filename TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                attended_at TEXT NOT NULL,
                attendance_date TEXT NOT NULL,
                session_name TEXT NOT NULL DEFAULT 'default',
                UNIQUE(student_id, attendance_date, session_name)
            );
            CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance(attendance_date);
            """
        )


def error(message: str, status: int = 400, code: str = "bad_request"):
    return jsonify({"ok": False, "error": {"code": code, "message": message}}), status


def json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def image_bytes_from_request() -> Optional[bytes]:
    uploaded = request.files.get("image") or request.files.get("file")
    if uploaded and uploaded.filename:
        return uploaded.read()
    payload = json_body()
    value = payload.get("image_data") or payload.get("image")
    if isinstance(value, str):
        if value.startswith("data:") and "," in value:
            value = value.split(",", 1)[1]
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error):
            return None
    return None


def decode_image(raw: bytes) -> Optional[np.ndarray]:
    if not raw:
        return None
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
FACE_CASCADE = cv2.CascadeClassifier(CASCADE_PATH)


def detect_face(image: np.ndarray) -> tuple[Optional[np.ndarray], Optional[str]]:
    """Return one face crop or a human-readable validation failure."""
    if image is None or image.size == 0:
        return None, "The image could not be decoded."
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if min(height, width) < 80:
        return None, "Image is too small. Use an image at least 80px wide and tall."
    faces = FACE_CASCADE.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40)
    )
    if len(faces) == 0:
        return None, "No face detected. Face the camera with good lighting."
    if len(faces) > 1:
        return None, "Multiple faces detected. Use an image containing one person."
    x, y, w, h = [int(v) for v in faces[0]]
    # Include a small margin so the descriptor is less sensitive to detector jitter.
    pad_x, pad_y = int(w * 0.12), int(h * 0.12)
    x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
    x2, y2 = min(width, x + w + pad_x), min(height, y + h + pad_y)
    crop = gray[y1:y2, x1:x2]
    if crop.shape[0] < 40 or crop.shape[1] < 40:
        return None, "Face is too small. Move closer to the camera."
    if float(cv2.Laplacian(crop, cv2.CV_64F).var()) < 18:
        return None, "Image is too blurry. Hold still and try again."
    return crop, None


def make_embedding(face: np.ndarray) -> list[float]:
    resized = cv2.resize(face, FACE_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    # Histogram equalization and z-score normalization tolerate modest lighting changes.
    resized = cv2.equalizeHist(resized.astype(np.uint8)).astype(np.float32)
    descriptor = (resized - resized.mean()) / (resized.std() + 1e-6)
    vector = descriptor.flatten()
    vector /= np.linalg.norm(vector) + 1e-8
    return vector.astype(float).tolist()


def embedding_distance(a: list[float], b: list[float]) -> float:
    left, right = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(np.linalg.norm(left - right))


def threshold() -> float:
    try:
        value = float(os.getenv("FACE_MATCH_THRESHOLD", str(DEFAULT_THRESHOLD)))
        return max(0.05, min(value, 2.0))
    except ValueError:
        return DEFAULT_THRESHOLD


def public_student(row: sqlite3.Row, db: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    result = {
        "id": row["id"],
        "student_code": row["student_code"],
        "name": row["name"],
        "email": row["email"],
        "created_at": row["created_at"],
    }
    if db is not None:
        count = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (row["id"],)
        ).fetchone()[0]
        result["attendance_count"] = count
    return result


def recognize_embedding(embedding: list[float]) -> tuple[Optional[sqlite3.Row], Optional[float]]:
    with get_db() as db:
        rows = db.execute("SELECT * FROM students ORDER BY name").fetchall()
    if not rows:
        return None, None
    ranked = sorted(
        ((embedding_distance(embedding, json.loads(row["embedding"])), row) for row in rows),
        key=lambda item: item[0],
    )
    distance, row = ranked[0]
    return (row, distance) if distance <= threshold() else (None, distance)


def record_attendance(student_id: int, session_name: str) -> tuple[bool, dict[str, Any]]:
    session = (session_name or "default").strip()[:80] or "default"
    today = date.today().isoformat()
    timestamp = now_iso()
    with get_db() as db:
        row = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        if row is None:
            raise ValueError("Student no longer exists.")
        try:
            db.execute(
                """INSERT INTO attendance
                (student_id, attended_at, attendance_date, session_name)
                VALUES (?, ?, ?, ?)""",
                (student_id, timestamp, today, session),
            )
            created = True
        except sqlite3.IntegrityError:
            created = False
        attendance = db.execute(
            """SELECT a.*, s.name, s.student_code FROM attendance a
               JOIN students s ON s.id = a.student_id
               WHERE a.student_id = ? AND a.attendance_date = ? AND a.session_name = ?""",
            (student_id, today, session),
        ).fetchone()
    return created, dict(attendance)


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def api_config():
    return jsonify({"ok": True, "threshold": threshold(), "face_size": FACE_SIZE})


@app.post("/api/register")
def register():
    payload = json_body()
    name = str(request.form.get("name") or payload.get("name") or "").strip()
    code = str(
        request.form.get("student_code") or payload.get("student_code") or ""
    ).strip()
    email = str(request.form.get("email") or payload.get("email") or "").strip()
    if not name or not code:
        return error("Name and student code are required.")
    raw = image_bytes_from_request()
    image = decode_image(raw or b"")
    face, issue = detect_face(image)
    if issue:
        return error(issue, 422, "invalid_face")
    embedding = make_embedding(face)  # type: ignore[arg-type]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}_{secure_filename(code)}.jpg"
    cv2.imwrite(str(UPLOAD_DIR / filename), image)
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO students
                (student_code, name, email, embedding, image_filename, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (code, name, email, json.dumps(embedding), filename, now_iso()),
            )
            student_id = cursor.lastrowid
            row = db.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
    except sqlite3.IntegrityError:
        (UPLOAD_DIR / filename).unlink(missing_ok=True)
        return error("That student code is already registered.", 409, "duplicate_student")
    return jsonify({"ok": True, "student": public_student(row)})


@app.post("/api/recognize")
def recognize():
    raw = image_bytes_from_request()
    image = decode_image(raw or b"")
    face, issue = detect_face(image)
    if issue:
        return error(issue, 422, "invalid_face")
    embedding = make_embedding(face)  # type: ignore[arg-type]
    student, distance = recognize_embedding(embedding)
    if student is None:
        return error(
            "No matching student found. Ask the student to enroll or improve the image.",
            404,
            "unknown_face",
        )
    payload = json_body()
    session = request.form.get("session") or payload.get("session") or "default"
    mark = str(request.form.get("mark_attendance") or payload.get("mark_attendance", "true")).lower()
    attendance = None
    created = False
    if mark not in {"false", "0", "no"}:
        created, attendance = record_attendance(student["id"], str(session))
    return jsonify(
        {
            "ok": True,
            "match": public_student(student),
            "distance": round(float(distance or 0), 5),
            "threshold": threshold(),
            "attendance": attendance,
            "attendance_created": created,
        }
    )


@app.get("/api/students")
def students():
    with get_db() as db:
        rows = db.execute("SELECT * FROM students ORDER BY name COLLATE NOCASE").fetchall()
        return jsonify({"ok": True, "students": [public_student(row, db) for row in rows]})


@app.delete("/api/students/<int:student_id>")
def delete_student(student_id: int):
    with get_db() as db:
        cursor = db.execute("DELETE FROM students WHERE id = ?", (student_id,))
    if cursor.rowcount == 0:
        return error("Student not found.", 404, "not_found")
    return jsonify({"ok": True})


@app.route("/api/attendance", methods=["GET", "POST"])
def attendance():
    if request.method == "POST":
        payload = json_body()
        raw_id = payload.get("student_id") or request.form.get("student_id")
        try:
            student_id = int(raw_id)
        except (TypeError, ValueError):
            return error("A numeric student_id is required.")
        try:
            created, record = record_attendance(
                student_id, str(payload.get("session") or request.form.get("session") or "default")
            )
        except ValueError as exc:
            return error(str(exc), 404, "not_found")
        return jsonify({"ok": True, "attendance": record, "attendance_created": created})
    day = request.args.get("date") or date.today().isoformat()
    session = request.args.get("session")
    query = """SELECT a.id, a.attended_at, a.attendance_date, a.session_name,
                      s.id student_id, s.student_code, s.name, s.email
               FROM attendance a JOIN students s ON s.id = a.student_id
               WHERE a.attendance_date = ?"""
    params: list[Any] = [day]
    if session:
        query += " AND a.session_name = ?"
        params.append(session)
    query += " ORDER BY a.attended_at DESC"
    with get_db() as db:
        rows = [dict(row) for row in db.execute(query, params).fetchall()]
    return jsonify({"ok": True, "date": day, "attendance": rows})


@app.get("/api/history")
def history():
    start = request.args.get("start")
    end = request.args.get("end")
    query = """SELECT a.id, a.attended_at, a.attendance_date, a.session_name,
                      s.student_code, s.name, s.email
               FROM attendance a JOIN students s ON s.id = a.student_id WHERE 1=1"""
    params: list[Any] = []
    if start:
        query += " AND a.attendance_date >= ?"
        params.append(start)
    if end:
        query += " AND a.attendance_date <= ?"
        params.append(end)
    query += " ORDER BY a.attended_at DESC LIMIT 5000"
    with get_db() as db:
        rows = [dict(row) for row in db.execute(query, params).fetchall()]
    return jsonify({"ok": True, "history": rows})


@app.get("/api/export")
def export_history():
    start = request.args.get("start")
    end = request.args.get("end")
    with get_db() as db:
        query = """SELECT a.attendance_date, a.attended_at, a.session_name,
                   s.student_code, s.name, s.email
                   FROM attendance a JOIN students s ON s.id = a.student_id WHERE 1=1"""
        params: list[Any] = []
        if start:
            query += " AND a.attendance_date >= ?"
            params.append(start)
        if end:
            query += " AND a.attendance_date <= ?"
            params.append(end)
        query += " ORDER BY a.attended_at DESC"
        rows = db.execute(query, params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "time", "session", "student_code", "name", "email"])
    for row in rows:
        writer.writerow(
            [row["attendance_date"], row["attended_at"], row["session_name"], row["student_code"], row["name"], row["email"]]
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=smartface-attendance.csv"},
    )


@app.get("/api/dashboard")
def dashboard():
    today = date.today().isoformat()
    with get_db() as db:
        total_students = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        today_count = db.execute(
            "SELECT COUNT(*) FROM attendance WHERE attendance_date = ?", (today,)
        ).fetchone()[0]
        total_count = db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
        recent = [
            dict(row)
            for row in db.execute(
                """SELECT a.attended_at, a.session_name, s.student_code, s.name
                   FROM attendance a JOIN students s ON s.id = a.student_id
                   ORDER BY a.attended_at DESC LIMIT 8"""
            ).fetchall()
        ]
    return jsonify(
        {
            "ok": True,
            "today": today,
            "total_students": total_students,
            "today_attendance": today_count,
            "total_attendance": total_count,
            "attendance_rate": round(today_count / total_students * 100, 1) if total_students else 0,
            "recent": recent,
        }
    )


@app.get("/uploads/<path:filename>")
def uploaded_file(filename: str):
    return send_from_directory(UPLOAD_DIR, filename)


@app.errorhandler(413)
def too_large(_exc):
    return error("Image is too large (maximum 8 MB).", 413, "file_too_large")


init_db()

if __name__ == "__main__":
    print("SmartFace Attendance running at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False)
