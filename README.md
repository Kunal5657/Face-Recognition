# SmartFace Attendance

SmartFace is a beginner-friendly, local attendance MVP for the hackathon. It
combines a Flask API, SQLite database, OpenCV Haar face detection, and a
responsive vanilla HTML/CSS/JS dashboard. Images and recognition data never
leave the computer.

## Quick start (Windows)

1. Install **Python 3.10+** and open PowerShell in this repository.
2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies and start the server:

   ```powershell
   python -m pip install -r requirements.txt
   python app.py
   ```

4. Open <http://127.0.0.1:5000>. Allow camera access when using Live
   attendance. Camera access is optional: a JPG/PNG can be uploaded instead.

To use a different matching strictness, set the normalized descriptor distance
threshold before starting (the default is `0.42`):

```powershell
$env:FACE_MATCH_THRESHOLD="0.36"
python app.py
```

## Demo flow

1. Open **Enroll student**, enter a name and unique student ID, and upload a
   sharp, front-facing photo with exactly one face.
2. Open **Live attendance**, choose a session, enable the camera, and capture
   (or upload) a face image.
3. The first successful match creates an attendance record. Repeating the scan
   for the same student and session on the same day is safely deduplicated.
4. Review **Overview**, **Students**, **History**, and download a CSV in
   **Reports/History**.

## Architecture and data flow

```text
Browser camera/file
    -> multipart POST /api/register or /api/recognize
    -> OpenCV decode -> Haar cascade (one face required)
    -> crop, quality checks, equalize, resize 32x32, z-score + L2 normalize
    -> JSON descriptor in SQLite -> nearest-distance match
    -> optional unique attendance insert (student, date, session)
    -> JSON response -> dashboard refresh
```

`app.py` owns API routes and the small data layer. `templates/index.html` is
the page shell; `static/app.js` provides navigation and API/camera behavior;
`static/styles.css` contains the responsive visual system. Runtime data is
created in `data/` (SQLite at `data/smartface.db`, uploaded images in
`data/uploads/`).

Available API endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/register` | Enroll `name`, `student_code`, optional `email`, and image |
| POST | `/api/recognize` | Recognize an image and optionally mark attendance |
| GET | `/api/attendance` | Today's attendance (`date`/`session` filters) |
| GET | `/api/students` | List students and check-in counts |
| DELETE | `/api/students/<id>` | Delete a student and their attendance |
| GET | `/api/dashboard` | Summary cards and recent activity |
| GET | `/api/history` | Date-filtered attendance history |
| GET | `/api/export` | CSV attendance export |
| GET | `/api/config` | Effective recognition threshold |

Images may be sent as a multipart `image` file or JSON `image_data` data URL.
Recognition accepts `session` and `mark_attendance=false`.

## Limitations and next steps

This is an MVP, not biometric-grade identity verification. Haar detection can
miss faces at unusual angles, and a 32x32 normalized grayscale descriptor is
less reliable across large lighting, pose, or appearance changes. A production
system should use consented, encrypted storage, liveness/anti-spoofing,
multiple enrollment samples, access control, audit logs, retention policies,
and a tested embedding model. The included threshold is configurable because
the right tradeoff depends on the room and camera. HTTPS is also recommended
when exposing the app beyond localhost.

## Tests

The tests use Flask's test client and synthetic images; no camera or human face
is needed:

```powershell
python -m pytest -q
```
