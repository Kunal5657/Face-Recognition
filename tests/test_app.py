import base64
import io
import json

import cv2
import numpy as np
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app as smartface

    monkeypatch.setattr(smartface, "DATABASE", tmp_path / "test.db")
    monkeypatch.setattr(smartface, "UPLOAD_DIR", tmp_path / "uploads")
    smartface.init_db()
    smartface.app.config.update(TESTING=True)
    with smartface.app.test_client() as test_client:
        yield test_client


def image_file():
    # API validation/error tests don't require a real human face or a camera.
    image = np.full((120, 120, 3), 150, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return io.BytesIO(encoded.tobytes())


def test_dashboard_starts_empty(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    assert response.json["total_students"] == 0
    assert response.json["today_attendance"] == 0


def test_register_requires_name_and_code(client):
    response = client.post("/api/register", data={"image": (image_file(), "face.jpg")})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "bad_request"


def test_register_rejects_image_without_face(client):
    response = client.post(
        "/api/register",
        data={"name": "Demo Student", "student_code": "D-1", "image": (image_file(), "face.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 422
    assert response.json["error"]["code"] == "invalid_face"


def test_student_delete_is_explicit(client):
    response = client.delete("/api/students/999")
    assert response.status_code == 404


def test_export_is_csv(client):
    response = client.get("/api/export")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "student_code" in response.text


def test_manual_attendance_is_deduplicated(client):
    import app as smartface

    with smartface.get_db() as db:
        db.execute(
            "INSERT INTO students (student_code, name, embedding, created_at) VALUES (?, ?, ?, ?)",
            ("D-2", "Attendance Demo", json.dumps([0.0]), "2026-01-01T00:00:00"),
        )
        student_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    first = client.post("/api/attendance", json={"student_id": student_id, "session": "Demo"})
    second = client.post("/api/attendance", json={"student_id": student_id, "session": "Demo"})
    assert first.status_code == second.status_code == 200
    assert first.json["attendance_created"] is True
    assert second.json["attendance_created"] is False
