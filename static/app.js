const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let stream = null;

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.className = "toast", 3600);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({ ok: false, error: { message: "Invalid server response." } }));
  if (!response.ok || data.ok === false) throw new Error(data.error?.message || "Request failed.");
  return data;
}

function initials(name) {
  return name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function navigate() {
  const page = location.hash.slice(1) || "dashboard";
  const valid = ["dashboard", "live", "register", "students", "history", "reports"];
  const selected = valid.includes(page) ? page : "dashboard";
  $$(".page").forEach((el) => el.classList.toggle("active", el.id === `page-${selected}`));
  $$("[data-page]").forEach((el) => el.classList.toggle("active", el.dataset.page === selected));
  const titles = { dashboard: ["COMMAND CENTER", "Good day, admin"], live: ["LIVE ATTENDANCE", "Scan the room"], register: ["STUDENT ENROLLMENT", "Create a face profile"], students: ["DIRECTORY", "Students"], history: ["RECORDS", "Attendance history"], reports: ["INSIGHTS", "Reports"] };
  $("#page-kicker").textContent = titles[selected][0];
  $("#page-title").textContent = titles[selected][1];
  $(".sidebar").classList.remove("open");
  if (selected === "dashboard") loadDashboard();
  if (selected === "students") loadStudents();
  if (selected === "history") loadHistory();
  if (selected === "reports") loadReport();
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    $("#stat-students").textContent = data.total_students;
    $("#stat-present").textContent = data.today_attendance;
    $("#stat-total").textContent = data.total_attendance;
    $("#stat-rate").textContent = `${data.attendance_rate}% attendance rate`;
    $("#today-label").textContent = new Date(`${data.today}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    const list = $("#recent-list");
    list.innerHTML = data.recent.length ? data.recent.map((row) => `<div class="activity"><span class="avatar">${initials(row.name)}</span><span><b>${escapeHtml(row.name)}</b><small>${escapeHtml(row.student_code)} · ${escapeHtml(row.session_name)}</small></span><time>${formatTime(row.attended_at)}</time></div>`).join("") : `<div class="empty-state">No check-ins yet. Start a live scan.</div>`;
  } catch (err) { showToast(err.message, true); }
}

async function loadStudents() {
  try {
    const data = await api("/api/students");
    window.students = data.students;
    renderStudents(data.students);
  } catch (err) { showToast(err.message, true); }
}
function renderStudents(rows) {
  const query = ($("#student-search").value || "").toLowerCase();
  const filtered = rows.filter((row) => `${row.name} ${row.student_code} ${row.email}`.toLowerCase().includes(query));
  $("#student-count").textContent = `${filtered.length} student${filtered.length === 1 ? "" : "s"}`;
  $("#students-empty").hidden = filtered.length !== 0;
  $("#students-table").innerHTML = filtered.map((row) => `<tr><td><b>${escapeHtml(row.name)}</b><small>${escapeHtml(row.created_at.slice(0, 10))}</small></td><td>${escapeHtml(row.student_code)}</td><td>${escapeHtml(row.email || "—")}</td><td>${row.attendance_count}</td><td><button class="delete-btn" data-delete="${row.id}" title="Delete student">⌫</button></td></tr>`).join("");
  $$("[data-delete]").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("Delete this student and their attendance history?")) return;
    try { await api(`/api/students/${button.dataset.delete}`, { method: "DELETE" }); showToast("Student deleted."); loadStudents(); } catch (err) { showToast(err.message, true); }
  }));
}

async function loadHistory() {
  const params = new URLSearchParams();
  if ($("#history-start").value) params.set("start", $("#history-start").value);
  if ($("#history-end").value) params.set("end", $("#history-end").value);
  try {
    const data = await api(`/api/history?${params}`);
    $("#history-empty").hidden = data.history.length !== 0;
    $("#history-table").innerHTML = data.history.map((row) => `<tr><td><b>${escapeHtml(row.attendance_date)}</b><small>${formatTime(row.attended_at)}</small></td><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.student_code)}</td><td><span class="success-badge">${escapeHtml(row.session_name)}</span></td></tr>`).join("");
    const exportParams = params.toString();
    $("#export-link").href = `/api/export${exportParams ? `?${exportParams}` : ""}`;
  } catch (err) { showToast(err.message, true); }
}

async function loadReport() {
  try {
    const [dashboard, config] = await Promise.all([api("/api/dashboard"), api("/api/config")]);
    $("#report-rate").textContent = `${dashboard.attendance_rate}%`;
    $("#report-progress").style.width = `${Math.min(100, dashboard.attendance_rate)}%`;
    $("#threshold-value").textContent = config.threshold;
  } catch (err) { showToast(err.message, true); }
}

function formatTime(value) {
  try { return new Date(value).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); } catch (_) { return value; }
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
}

async function recognize(blob) {
  const form = new FormData();
  form.append("image", blob, "capture.jpg");
  form.append("session", $("#live-session").value || "default");
  try {
    const data = await api("/api/recognize", { method: "POST", body: form });
    const student = data.match;
    $("#recognition-result").className = "match-result";
    $("#recognition-result").innerHTML = `<span class="match-avatar">${initials(student.name)}</span><h3>${escapeHtml(student.name)}</h3><p>${escapeHtml(student.student_code)} · match distance ${data.distance}</p><span class="success-badge">${data.attendance_created ? "Attendance recorded" : "Already marked today"}</span>`;
    showToast(data.attendance_created ? `${student.name} marked present.` : `${student.name} is already marked present.`);
    loadDashboard();
  } catch (err) {
    $("#recognition-result").className = "empty-result error-result";
    $("#recognition-result").innerHTML = `<span>!</span><b>Scan not completed</b><small>${escapeHtml(err.message)}</small>`;
    showToast(err.message, true);
  }
}

async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 720 } }, audio: false });
    $("#video").srcObject = stream;
    $("#camera-placeholder").style.display = "none";
    $(".scan-frame").style.display = "block";
    $("#start-camera").textContent = "Camera enabled";
    $("#capture-btn").disabled = false;
  } catch (_) {
    showToast("Camera unavailable. You can upload an image instead.", true);
  }
}

$("#start-camera").addEventListener("click", startCamera);
$("#capture-btn").addEventListener("click", () => {
  const canvas = $("#capture-canvas");
  const video = $("#video");
  canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob(recognize, "image/jpeg", .92);
});
$("#recognize-file").addEventListener("change", (event) => { if (event.target.files[0]) recognize(event.target.files[0]); });
$("#menu-btn").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
$("#student-search").addEventListener("input", () => renderStudents(window.students || []));
$("#history-filter").addEventListener("click", loadHistory);

const registerDrop = $("#register-drop");
const registerFile = $("#register-file");
registerDrop.addEventListener("click", () => registerFile.click());
registerDrop.addEventListener("dragover", (event) => { event.preventDefault(); registerDrop.style.borderColor = "var(--purple)"; });
registerDrop.addEventListener("dragleave", () => registerDrop.style.borderColor = "");
registerDrop.addEventListener("drop", (event) => { event.preventDefault(); registerFile.files = event.dataTransfer.files; showPreview(registerFile.files[0]); });
registerFile.addEventListener("change", () => showPreview(registerFile.files[0]));
$("#register-camera").addEventListener("click", async () => {
  try {
    if (!stream) stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    const video = $("#video");
    video.srcObject = stream;
    await video.play();
    await new Promise((resolve) => setTimeout(resolve, 500));
    const canvas = $("#capture-canvas");
    canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      const file = new File([blob], "camera-enrollment.jpg", { type: "image/jpeg" });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      registerFile.files = transfer.files;
      showPreview(file);
      showToast("Camera image captured. Submit to enroll.");
    }, "image/jpeg", .92);
  } catch (_) {
    showToast("Camera unavailable. Choose an image file instead.", true);
  }
});
function showPreview(file) {
  if (!file) return;
  $("#register-preview").src = URL.createObjectURL(file);
  $("#register-preview").hidden = false;
  registerDrop.querySelector("b").textContent = file.name;
}
$("#register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true; button.firstChild.textContent = "Enrolling… ";
  try {
    const data = await api("/api/register", { method: "POST", body: new FormData(event.target) });
    showToast(`${data.student.name} enrolled successfully.`);
    event.target.reset(); $("#register-preview").hidden = true; registerDrop.querySelector("b").textContent = "Drop a face image here";
    location.hash = "students";
  } catch (err) { showToast(err.message, true); } finally { button.disabled = false; button.firstChild.textContent = "Enroll student "; }
});

window.addEventListener("hashchange", navigate);
window.addEventListener("beforeunload", () => stream?.getTracks().forEach((track) => track.stop()));
navigate();
