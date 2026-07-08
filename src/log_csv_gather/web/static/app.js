const state = {
  activeJobId: null,
  pollingMs: 2000,
  doctorVerificationRequired: false,
  folderTarget: null,
  currentFolder: null,
  activeConfig: null,
  schedulerIntervalDirty: false,
};

const maxSchedulerIntervalHours = 23;

function updateCurrentTime() {
  const target = document.getElementById("current-time");
  if (!target) return;
  const now = new Date();
  const date = now.toISOString().slice(0, 10);
  const time = now.toTimeString().slice(0, 8);
  target.textContent = `${date} ${time}`;
}

async function fetchJson(url, options = {}) {
  const { headers = {}, ...rest } = options;
  const response = await fetch(url, {
    ...rest,
    headers: { Accept: "application/json", ...headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload;
}

async function startAction(action) {
  if (state.doctorVerificationRequired && ["upload-once", "download-once"].includes(action)) {
    const ok = window.confirm("설정 변경 후 Doctor 검증이 아직 필요합니다. 계속 실행할까요?");
    if (!ok) return;
  }
  setJobSummary(`${action} 요청 중...`);
  setActionTone(action, "blue");
  updateJobProgress(null);
  setButtonsDisabled(true);
  try {
    const payload = await fetchJson(`/api/actions/${action}`, { method: "POST" });
    state.activeJobId = payload.job_id;
    setJobState(payload.status || "queued");
    setJobSummary(`${action} job 생성: ${payload.job_id}`);
    await refreshAll();
  } catch (error) {
    setJobState("failed");
    setJobSummary(`실행 실패: ${error.message}`);
  } finally {
    setButtonsDisabled(false);
  }
}

async function refreshJob() {
  if (!state.activeJobId) return;
  const job = await fetchJson(`/api/jobs/${state.activeJobId}`);
  setJobState(job.status);
  const progress = job.latest_progress || (job.progress && job.progress.length ? job.progress[job.progress.length - 1] : null);
  updateJobProgress(progress);
  const progressMessage = progress && progress.message ? progress.message : "";
  const result = job.result ? ` result=${JSON.stringify(job.result)}` : "";
  const error = job.error ? ` error=${job.error}` : "";
  setJobSummary(`${job.action} ${job.status}${progressMessage ? ` - ${progressMessage}` : ""}${result}${error}`);
  if (["succeeded", "failed"].includes(job.status)) {
    state.activeJobId = null;
    if (job.action === "doctor" && job.status === "succeeded") {
      state.doctorVerificationRequired = false;
    }
  }
}

async function refreshFeed() {
  const payload = await fetchJson("/api/feed?limit=40");
  const target = document.querySelector("[data-feed]");
  if (!target) return;
  const events = payload.events || [];
  target.innerHTML = events.length
    ? events.map(renderFeedEvent).join("")
    : '<div class="muted">아직 feed 이벤트가 없습니다.</div>';
}

async function refreshStatus() {
  const payload = await fetchJson("/api/status?details=true");
  setText("[data-upload-counts]", formatCounts(payload.uploads));
  setText("[data-download-counts]", formatCounts(payload.downloads));
  renderActionStatuses(payload.actions || {});
  renderConflictRows(payload);
}

async function refreshScheduler() {
  const payload = await fetchJson("/api/scheduler");
  renderScheduler(payload);
}

async function refreshActiveConfig() {
  const payload = await fetchJson("/api/config/active");
  renderActiveConfig(payload);
}

async function refreshLogTail() {
  const payload = await fetchJson("/api/logs/tail?lines=120");
  const target = document.querySelector("[data-log-tail]");
  if (!target) return;
  const lines = payload.lines || [];
  target.textContent = lines.length ? lines.join("\n") : "[local] app.log is empty or not created yet.";
}

async function refreshAll() {
  try {
    await Promise.all([
      refreshJob(),
      refreshFeed(),
      refreshStatus(),
      refreshScheduler(),
      refreshActiveConfig(),
      refreshLogTail(),
    ]);
  } catch (error) {
    setJobSummary(`새로고침 실패: ${error.message}`);
  }
}

async function registerScheduler() {
  if (state.doctorVerificationRequired) {
    const ok = window.confirm("설정 변경 후 Doctor 검증이 아직 필요합니다. 스케줄러를 등록할까요?");
    if (!ok) return;
  }
  const intervalInput = document.querySelector("[data-scheduler-interval]");
  const intervalHours = Number(intervalInput ? intervalInput.value : 1);
  if (!Number.isInteger(intervalHours) || intervalHours < 1 || intervalHours > maxSchedulerIntervalHours) {
    setJobSummary(`스케줄러 간격은 1시간부터 ${maxSchedulerIntervalHours}시간까지 입력할 수 있습니다.`);
    return;
  }
  await runSchedulerRequest("/api/scheduler/register", {
    interval_minutes: schedulerHoursToMinutes(intervalHours),
    enabled: true,
  });
}

async function unregisterScheduler() {
  await runSchedulerRequest("/api/scheduler/unregister");
}

async function setSchedulerEnabled(enabled) {
  await runSchedulerRequest(enabled ? "/api/scheduler/enable" : "/api/scheduler/disable");
}

async function runSchedulerRequest(url, body = null) {
  setSchedulerButtonsDisabled(true);
  try {
    const payload = await fetchJson(url, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (url === "/api/scheduler/register") {
      state.schedulerIntervalDirty = false;
    }
    renderScheduler(payload);
    setJobSummary(`스케줄러 처리 완료: ${payload.task_name}`);
  } catch (error) {
    setJobSummary(`스케줄러 처리 실패: ${error.message}`);
  } finally {
    setSchedulerButtonsDisabled(false);
    await refreshScheduler().catch(() => undefined);
  }
}

async function switchRole(role) {
  const label = role === "uploader" ? "현장 PC 업로드" : "관리 PC 다운로드";
  if (!window.confirm(`역할을 '${label}'로 변경할까요?`)) return;
  setRoleButtonsDisabled(true);
  try {
    const payload = await fetchJson("/api/config/role", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    renderActiveConfig(payload);
    const schedulerNote = payload.scheduler_unregistered ? " 기존 예약 작업은 등록해제되었습니다." : "";
    setJobSummary(`역할 변경 완료: ${label}.${schedulerNote}`);
    window.setTimeout(() => window.location.reload(), 700);
  } catch (error) {
    setJobSummary(`역할 변경 실패: ${error.message}`);
  } finally {
    setRoleButtonsDisabled(false);
  }
}

async function resetActiveConfig() {
  if (!window.confirm("설정을 초기화할까요? 등록된 스케줄러가 있으면 해제하고, 다음 run.bat 실행 때 역할을 다시 선택합니다.")) return;
  setRoleButtonsDisabled(true);
  try {
    const payload = await fetchJson("/api/config/active/reset", { method: "POST" });
    renderActiveConfig(payload);
    const schedulerNote = payload.scheduler_unregistered ? " 기존 스케줄러는 등록해제되었습니다." : "";
    setJobSummary(`설정 초기화 완료.${schedulerNote} 다음 run.bat 실행 때 역할을 다시 선택합니다.`);
  } catch (error) {
    setJobSummary(`설정 초기화 실패: ${error.message}`);
  } finally {
    setRoleButtonsDisabled(false);
  }
}

async function resetLocalState() {
  if (!window.confirm("로컬 처리 이력을 초기화할까요? state.sqlite만 백업 후 초기화하며 설정, token.json, 원본 CSV, Google Drive 파일은 유지됩니다.")) return;
  setRoleButtonsDisabled(true);
  try {
    const payload = await fetchJson("/api/state/reset", { method: "POST" });
    const backupNote = payload.backup_path ? ` 백업: ${payload.backup_path}` : " 기존 DB 없음.";
    setJobState("idle");
    updateJobProgress(null);
    setJobSummary(`로컬 상태 초기화 완료.${backupNote}`);
    await refreshAll();
  } catch (error) {
    setJobSummary(`로컬 상태 초기화 실패: ${error.message}`);
  } finally {
    setRoleButtonsDisabled(false);
  }
}

function renderScheduler(payload) {
  setText("[data-scheduler-state]", payload.supported ? (payload.state || "idle") : "unsupported");
  setText("[data-scheduler-task-name]", payload.task_name || "-");
  setText("[data-scheduler-command]", payload.command || "-");
  const intervalInput = document.querySelector("[data-scheduler-interval]");
  if (
    intervalInput &&
    payload.configured_interval_minutes &&
    !isSchedulerIntervalFocused() &&
    !isSchedulerIntervalDirty()
  ) {
    intervalInput.value = schedulerMinutesToHours(payload.configured_interval_minutes);
  }
  renderChip(
    "[data-scheduler-registered]",
    payload.registered ? "등록됨" : payload.supported ? "미등록" : "지원 안 됨",
    payload.registered ? "green" : payload.supported ? "gray" : "red",
  );
  const enabled = payload.enabled ?? payload.configured_enabled;
  renderChip("[data-scheduler-enabled]", enabled ? "켜짐" : "꺼짐", enabled ? "blue" : "gray");
}

function schedulerMinutesToHours(minutes) {
  const value = Number(minutes);
  if (!Number.isFinite(value) || value <= 0) return 1;
  return Math.max(1, Math.min(maxSchedulerIntervalHours, Math.ceil(value / 60)));
}

function schedulerHoursToMinutes(hours) {
  return Number(hours) * 60;
}

function isSchedulerIntervalFocused() {
  const intervalInput = document.querySelector("[data-scheduler-interval]");
  return Boolean(intervalInput && document.activeElement === intervalInput);
}

function isSchedulerIntervalDirty() {
  return state.schedulerIntervalDirty;
}

function markSchedulerIntervalDirty() {
  state.schedulerIntervalDirty = true;
}

function renderActiveConfig(payload) {
  if (!payload) return;
  state.activeConfig = payload;
  state.doctorVerificationRequired = Boolean(payload.setup_required || state.doctorVerificationRequired);
  setText("[data-active-role]", payload.role || "-");
  setText("[data-current-role]", payload.role || "-");
  setText("[data-context-role]", payload.role || "-");
  setText("[data-context-pc-id]", payload.pc_id || "-");
  setText("[data-config-path]", payload.config_path || "-");
  setText("[data-active-exists]", payload.active_exists ? "사용 중" : "없음");
  setText("[data-drive-root]", payload.drive_root_folder_id || "-");
  setText("[data-source-root]", payload.source_root || "-");
  setText("[data-download-root]", payload.download_root || "-");
  setText("[data-machine-id]", payload.machine_id || "-");
  if (!isSetupModalOpen()) {
    fillSetupForm(payload);
  }
}

function renderChip(selector, text, colorClass) {
  const target = document.querySelector(selector);
  if (!target) return;
  target.className = `chip ${colorClass}`;
  target.innerHTML = `<span></span>${escapeHtml(text)}`;
}

function renderFeedEvent(event) {
  const level = escapeHtml(event.level || "info");
  const message = escapeHtml(event.message || "");
  const action = escapeHtml(event.action || "");
  const at = escapeHtml((event.at || "").replace("T", " ").slice(0, 19));
  return `<div class="feed-event ${level}"><span>${at}</span><strong>${action}</strong><code>${message}</code></div>`;
}

function renderConflictRows(payload) {
  const target = document.querySelector("[data-conflict-list]");
  if (!target) return;
  const rows = [
    ...(payload.upload_conflicts || []),
    ...(payload.download_conflicts || []),
    ...(payload.upload_failed || []),
    ...(payload.download_failed || []),
  ];
  if (!rows.length) {
    target.innerHTML = '<tr><td colspan="3" class="muted">조회된 conflict/failed 항목이 없습니다.</td></tr>';
    return;
  }
  target.innerHTML = rows.map((row) => {
    const path = escapeHtml(row.drive_path || row.local_path || row.source_path || "-");
    const status = escapeHtml(row.status || "-");
    const error = escapeHtml(row.last_error || "-");
    return `<tr><td>${path}</td><td class="status-cell ${status}">${status}</td><td>${error}</td></tr>`;
  }).join("");
}

function renderActionStatuses(actions) {
  document.querySelectorAll("[data-action-status]").forEach((dot) => {
    const action = dot.dataset.actionStatus;
    const result = actions[action];
    setActionTone(action, result ? result.tone : "gray");
    dot.title = result ? `${result.status}: ${result.message || ""}` : "not run";
  });
}

function setActionTone(action, tone) {
  const target = document.querySelector(`[data-action-status="${action}"]`);
  if (!target) return;
  target.className = `action-dot ${tone || "gray"}`;
}

function updateJobProgress(progress) {
  const bar = document.querySelector("[data-job-progress-bar]");
  const text = document.querySelector("[data-job-progress-text]");
  const counts = document.querySelector("[data-job-counts]");
  const current = progress && Number.isFinite(Number(progress.current)) ? Number(progress.current) : 0;
  const total = progress && Number.isFinite(Number(progress.total)) ? Number(progress.total) : 0;
  const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
  if (bar) bar.style.width = `${percent}%`;
  if (text) text.textContent = total > 0 ? `${current} / ${total}` : "0 / 0";
  if (counts) {
    counts.textContent = [
      `success=${progress?.success ?? 0}`,
      `skipped=${progress?.skipped ?? 0}`,
      `failed=${progress?.failed ?? 0}`,
      `conflict=${progress?.conflict ?? 0}`,
    ].join(" ");
  }
}

function formatCounts(counts) {
  const entries = Object.entries(counts || {});
  return entries.length ? entries.map(([key, value]) => `${key}=${value}`).join(", ") : "none";
}

function setJobState(value) {
  setText("[data-job-state]", value || "idle");
}

function setJobSummary(value) {
  setText("[data-job-summary]", value);
}

function setText(selector, value) {
  const target = document.querySelector(selector);
  if (target) target.textContent = value;
}

function setButtonsDisabled(disabled) {
  document.querySelectorAll("[data-action]").forEach((button) => {
    if (!button.dataset.originalDisabled) {
      button.dataset.originalDisabled = button.disabled ? "true" : "false";
    }
    button.disabled = disabled || button.dataset.originalDisabled === "true";
  });
}

function setSchedulerButtonsDisabled(disabled) {
  document.querySelectorAll("[data-scheduler-register], [data-scheduler-unregister], [data-scheduler-enable], [data-scheduler-disable]").forEach((button) => {
    button.disabled = disabled;
  });
}

function setRoleButtonsDisabled(disabled) {
  document.querySelectorAll("[data-role-switch], [data-active-reset], [data-state-reset]").forEach((button) => {
    button.disabled = disabled;
  });
}

function fillSetupForm(payload) {
  setValue("[data-setup-role]", payload.role || "uploader");
  setValue("[data-setup-pc-id]", payload.pc_id || "");
  setValue("[data-setup-drive-root]", payload.drive_root_folder_id || "");
  setValue("[data-setup-machine-id]", payload.machine_id || "성능검사기_1");
  setValue("[data-setup-source-root]", payload.source_root || "E:\\");
  setValue("[data-setup-download-root]", payload.download_root || "../runtime/downloads");
  updateSetupRoleFields();
}

function isSetupModalOpen() {
  const modal = document.querySelector("[data-setup-modal]");
  return modal ? !modal.classList.contains("hidden") : false;
}

function setValue(selector, value) {
  const target = document.querySelector(selector);
  if (target && value !== null && value !== undefined) target.value = value;
}

function openSetupModal() {
  const modal = document.querySelector("[data-setup-modal]");
  if (!modal) return;
  if (state.activeConfig) {
    fillSetupForm(state.activeConfig);
  }
  modal.classList.remove("hidden");
  updateSetupRoleFields();
  validateSelectedSetupPath().catch(() => undefined);
}

function closeSetupModal() {
  const modal = document.querySelector("[data-setup-modal]");
  if (modal) modal.classList.add("hidden");
  closeFolderBrowser();
}

function updateSetupRoleFields() {
  const role = getSetupRole();
  document.querySelectorAll("[data-setup-role-panel]").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.setupRolePanel !== role);
  });
}

function getSetupRole() {
  const select = document.querySelector("[data-setup-role]");
  return select ? select.value : "uploader";
}

function collectSetupPayload() {
  const role = getSetupRole();
  const payload = {
    role,
    pc_id: getInputValue("[data-setup-pc-id]"),
    drive_root_folder_id: getInputValue("[data-setup-drive-root]"),
  };
  if (role === "uploader") {
    payload.machine_id = getInputValue("[data-setup-machine-id]") || "성능검사기_1";
    payload.source_root = getInputValue("[data-setup-source-root]");
  } else {
    payload.download_root = getInputValue("[data-setup-download-root]");
  }
  return payload;
}

function getInputValue(selector) {
  const target = document.querySelector(selector);
  return target ? target.value.trim() : "";
}

async function saveSetup(event) {
  event.preventDefault();
  setSetupMessage("저장 중...");
  try {
    const payload = await fetchJson("/api/config/setup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectSetupPayload()),
    });
    renderActiveConfig(payload);
    renderPathValidation(payload.path_validation);
    state.doctorVerificationRequired = true;
    const schedulerNote = payload.scheduler_unregistered ? " 기존 스케줄러는 등록해제되었습니다." : "";
    setSetupMessage(`저장 완료.${schedulerNote} Doctor를 수동으로 실행하세요.`);
    setJobSummary(`초기설정 저장 완료.${schedulerNote} Doctor 검증이 필요합니다.`);
    await refreshScheduler().catch(() => undefined);
  } catch (error) {
    setSetupMessage(`저장 실패: ${error.message}`);
    setJobSummary(`초기설정 저장 실패: ${error.message}`);
  }
}

async function validateSelectedSetupPath() {
  const role = getSetupRole();
  const path = role === "uploader" ? getInputValue("[data-setup-source-root]") : getInputValue("[data-setup-download-root]");
  if (!path) {
    renderPathValidation({ status: "error", message: "경로를 입력하세요." });
    return;
  }
  const payload = await fetchJson("/api/local/validate-path", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, path }),
  });
  renderPathValidation(payload);
}

function renderPathValidation(payload) {
  const target = document.querySelector("[data-path-validation]");
  if (!target || !payload) return;
  const status = payload.status || "error";
  target.className = `validation-box ${status}`;
  const foundText = Array.isArray(payload.found) ? ` found=${payload.found.length}` : "";
  const missingText = Array.isArray(payload.missing) && payload.missing.length ? ` missing=${payload.missing.join(", ")}` : "";
  target.textContent = `${status}: ${payload.message || ""}${foundText}${missingText}`;
}

function setSetupMessage(value) {
  setText("[data-setup-message]", value);
}

async function openFolderBrowser(targetName) {
  state.folderTarget = targetName;
  const browser = document.querySelector("[data-folder-browser]");
  if (browser) browser.classList.remove("hidden");
  const drivesPayload = await fetchJson("/api/local/drives");
  renderDrives(drivesPayload.drives || []);
  const initial = targetName === "source_root" ? getInputValue("[data-setup-source-root]") : getInputValue("[data-setup-download-root]");
  const firstDrive = (drivesPayload.drives || [])[0];
  await browseFolder(initial || (firstDrive ? firstDrive.path : "/"));
}

function closeFolderBrowser() {
  const browser = document.querySelector("[data-folder-browser]");
  if (browser) browser.classList.add("hidden");
}

function renderDrives(drives) {
  const target = document.querySelector("[data-folder-drives]");
  if (!target) return;
  target.innerHTML = drives.map((drive) => (
    `<button type="button" class="btn secondary compact" data-drive-path="${escapeHtml(drive.path)}">${escapeHtml(drive.name)}</button>`
  )).join("");
  target.querySelectorAll("[data-drive-path]").forEach((button) => {
    button.addEventListener("click", () => browseFolder(button.dataset.drivePath));
  });
}

async function browseFolder(path) {
  const payload = await fetchJson(`/api/local/folders?path=${encodeURIComponent(path)}`);
  state.currentFolder = payload.path;
  setText("[data-folder-current]", payload.path || "-");
  const target = document.querySelector("[data-folder-list]");
  if (!target) return;
  const rows = [];
  if (payload.parent) {
    rows.push(`<button type="button" class="folder-row" data-folder-path="${escapeHtml(payload.parent)}">..</button>`);
  }
  rows.push(...(payload.folders || []).map((folder) => (
    `<button type="button" class="folder-row" data-folder-path="${escapeHtml(folder.path)}">${escapeHtml(folder.name)}</button>`
  )));
  if (payload.error) {
    rows.push(`<div class="folder-row muted">${escapeHtml(payload.error)}</div>`);
  }
  target.innerHTML = rows.length ? rows.join("") : '<div class="folder-row muted">표시할 폴더가 없습니다.</div>';
  target.querySelectorAll("[data-folder-path]").forEach((button) => {
    button.addEventListener("click", () => browseFolder(button.dataset.folderPath));
  });
}

async function chooseCurrentFolder() {
  if (!state.currentFolder || !state.folderTarget) return;
  if (state.folderTarget === "source_root") {
    setValue("[data-setup-source-root]", state.currentFolder);
  } else {
    setValue("[data-setup-download-root]", state.currentFolder);
  }
  closeFolderBrowser();
  await validateSelectedSetupPath().catch((error) => setSetupMessage(`경로 검증 실패: ${error.message}`));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", () => startAction(button.dataset.action));
});

document.querySelectorAll("[data-role-switch]").forEach((button) => {
  button.addEventListener("click", () => switchRole(button.dataset.roleSwitch));
});

document.querySelectorAll("[data-setup-open]").forEach((button) => {
  button.addEventListener("click", openSetupModal);
});

document.querySelectorAll("[data-setup-close]").forEach((button) => {
  button.addEventListener("click", closeSetupModal);
});

const setupForm = document.querySelector("[data-setup-form]");
if (setupForm) {
  setupForm.addEventListener("submit", saveSetup);
}

const setupRole = document.querySelector("[data-setup-role]");
if (setupRole) {
  setupRole.addEventListener("change", () => {
    updateSetupRoleFields();
    validateSelectedSetupPath().catch(() => undefined);
  });
}

document.querySelectorAll("[data-setup-source-root], [data-setup-download-root]").forEach((input) => {
  input.addEventListener("change", () => validateSelectedSetupPath().catch(() => undefined));
});

document.querySelectorAll("[data-folder-open]").forEach((button) => {
  button.addEventListener("click", () => openFolderBrowser(button.dataset.folderOpen).catch((error) => setSetupMessage(`폴더 탐색 실패: ${error.message}`)));
});

const folderCloseButton = document.querySelector("[data-folder-browser-close]");
if (folderCloseButton) {
  folderCloseButton.addEventListener("click", closeFolderBrowser);
}

const folderChooseButton = document.querySelector("[data-folder-choose]");
if (folderChooseButton) {
  folderChooseButton.addEventListener("click", chooseCurrentFolder);
}

const activeResetButton = document.querySelector("[data-active-reset]");
if (activeResetButton) {
  activeResetButton.addEventListener("click", resetActiveConfig);
}

const stateResetButton = document.querySelector("[data-state-reset]");
if (stateResetButton) {
  stateResetButton.addEventListener("click", resetLocalState);
}

const refreshButton = document.querySelector("[data-refresh-status]");
if (refreshButton) {
  refreshButton.addEventListener("click", refreshAll);
}

const registerButton = document.querySelector("[data-scheduler-register]");
if (registerButton) {
  registerButton.addEventListener("click", registerScheduler);
}

const schedulerIntervalInput = document.querySelector("[data-scheduler-interval]");
if (schedulerIntervalInput) {
  schedulerIntervalInput.addEventListener("input", markSchedulerIntervalDirty);
  schedulerIntervalInput.addEventListener("change", markSchedulerIntervalDirty);
}

const unregisterButton = document.querySelector("[data-scheduler-unregister]");
if (unregisterButton) {
  unregisterButton.addEventListener("click", unregisterScheduler);
}

const enableButton = document.querySelector("[data-scheduler-enable]");
if (enableButton) {
  enableButton.addEventListener("click", () => setSchedulerEnabled(true));
}

const disableButton = document.querySelector("[data-scheduler-disable]");
if (disableButton) {
  disableButton.addEventListener("click", () => setSchedulerEnabled(false));
}

updateSetupRoleFields();
updateCurrentTime();
setInterval(updateCurrentTime, 1000);
refreshAll();
setInterval(refreshAll, state.pollingMs);

if (document.body.dataset.setupRequired === "true") {
  window.setTimeout(openSetupModal, 300);
}
