const API_BASE = "/api";
let activeOrgId = localStorage.getItem("active_org_id") || "";
let activeOrgName = localStorage.getItem("active_org_name") || "";
let currentSection = "timetable";
let activeViewType = "class"; // or "teacher"
let activeFilterId = ""; // active group_id or resource_id

let cachedMeta = null;
let cachedSchedule = [];
let socket = null;

// DOM Elements
const views = {
    wizard: document.getElementById("wizard-container"),
    dashboard: document.getElementById("dashboard-view")
};

const sections = {
    timetable: document.getElementById("section-timetable"),
    logs: document.getElementById("section-logs")
};

// --- Inits ---
async function init() {
    if (activeOrgId) {
        enterWorkspace(activeOrgId, activeOrgName);
    } else {
        showWizard();
    }
    setupEventListeners();
}

// --- Stepper Navigation ---
let currentStep = 1;

function showWizard() {
    views.wizard.classList.remove("hidden");
    views.dashboard.classList.add("hidden");
    resetWizard();
}

function resetWizard() {
    currentStep = 1;
    document.getElementById("wiz-org-name").value = "";
    document.getElementById("file-input").value = "";
    document.getElementById("file-info-container").classList.add("hidden");
    document.getElementById("validation-errors-box").classList.add("hidden");
    showStep(1);
}

function showStep(stepNum) {
    currentStep = stepNum;
    
    // Toggle active step class
    document.querySelectorAll(".wizard-step").forEach(step => {
        step.classList.remove("active");
    });
    document.getElementById(`step-${stepNum}`).classList.add("active");

    // Toggle active indicator node
    document.querySelectorAll(".step-node").forEach(node => {
        const step = parseInt(node.getAttribute("data-step"));
        if (step <= stepNum) {
            node.classList.add("active");
        } else {
            node.classList.remove("active");
        }
    });
}

function nextStep(stepNum) {
    if (stepNum === 2) {
        const name = document.getElementById("wiz-org-name").value.stripOrEmpty();
        if (!name) {
            showToast("Please enter an organization name", "error");
            return;
        }
    }
    showStep(stepNum);
}

function prevStep(stepNum) {
    showStep(stepNum);
}

// Helper to clean string values
String.prototype.stripOrEmpty = function() {
    return this.replace(/^\s+|\s+$/g, "");
};

// --- Drag & Drop Handlers ---
const dropZone = document.getElementById("drop-zone");
const fileInput = document.getElementById("file-input");
const fileInfoContainer = document.getElementById("file-info-container");
const fileNameDisplay = document.getElementById("file-name-display");

if (dropZone) {
    dropZone.addEventListener("click", () => fileInput.click());

    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("dragover");
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("dragover");
    });

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");
        if (e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelection();
        }
    });
}

if (fileInput) {
    fileInput.addEventListener("change", handleFileSelection);
}

function handleFileSelection() {
    const file = fileInput.files[0];
    if (file) {
        fileNameDisplay.textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
        fileInfoContainer.classList.remove("hidden");
    }
}

// --- Submit Form (Organization Name + File Ingestion) ---
document.getElementById("wizard-submit-btn").addEventListener("click", async () => {
    const file = fileInput.files[0];
    const orgName = document.getElementById("wiz-org-name").value.stripOrEmpty();

    if (!file || !orgName) {
        showToast("Roster file and organization name are required", "error");
        return;
    }

    const submitBtn = document.getElementById("wizard-submit-btn");
    submitBtn.disabled = true;
    submitBtn.textContent = "Solving CP-SAT constraints...";

    const formData = new FormData();
    formData.append("org_name", orgName);
    formData.append("file", file);

    const errBox = document.getElementById("validation-errors-box");
    const errList = document.getElementById("validation-errors-list");
    errBox.classList.add("hidden");
    errList.innerHTML = "";

    try {
        const res = await fetch(`${API_BASE}/organizations/upload-roster`, {
            method: "POST",
            body: formData
        });

        const result = await res.json();
        
        if (res.ok && result.status === "success") {
            showToast("Roster imported & solved successfully!", "success");
            enterWorkspace(result.organization_id, result.organization_name);
        } else {
            errBox.classList.remove("hidden");
            const errors = result.detail?.errors || result.detail?.violations || [result.detail?.message || "Solver failed to find a valid arrangement. Make sure constraints are feasible."];
            errors.forEach(err => {
                errList.innerHTML += `<li>${err}</li>`;
            });
            showToast("Roster contains constraint violations", "error");
        }
    } catch (e) {
        showToast("Network error submitting timetable template", "error");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "Generate & Solve";
    }
});

// --- Entering Active Workspace & Dashboard ---
async function enterWorkspace(orgId, orgName) {
    activeOrgId = orgId;
    activeOrgName = orgName;
    localStorage.setItem("active_org_id", orgId);
    localStorage.setItem("active_org_name", orgName);
    
    views.wizard.classList.add("hidden");
    views.dashboard.classList.remove("hidden");
    
    document.getElementById("active-org-name").textContent = orgName;
    document.querySelector(".avatar").textContent = orgName.charAt(0).toUpperCase();
    
    // Connect WebSockets
    connectWebSocket(orgId);

    // Initial Sync
    await refreshMetadata();
    await refreshSchedule();
    updateViewSelector();
    renderTimetable();
    
    switchSection("timetable");
}

function switchSection(target) {
    currentSection = target;
    document.querySelectorAll(".nav-item").forEach(item => {
        item.classList.remove("active");
    });
    document.getElementById(`nav-${target}`).classList.add("active");

    Object.keys(sections).forEach(key => {
        sections[key].classList.remove("active");
    });
    sections[target].classList.add("active");

    if (target === "logs") {
        loadSolveLogs();
    }
}

// --- WebSocket Live updates ---
function connectWebSocket(orgId) {
    if (socket) socket.close();
    
    const loc = window.location;
    const proto = loc.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${loc.host}${API_BASE}/organizations/${orgId}/ws`;

    socket = new WebSocket(wsUrl);
    
    socket.onmessage = async (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "schedule_updated") {
            showToast("Timetable updated by another session!");
            await refreshSchedule();
            renderTimetable();
        }
    };

    socket.onclose = () => {
        setTimeout(() => connectWebSocket(orgId), 5000);
    };
}

// --- API Syncs ---
async function refreshMetadata() {
    try {
        const res = await fetch(`${API_BASE}/organizations/${activeOrgId}/meta`);
        if (res.ok) cachedMeta = await res.json();
    } catch(e) {}
}

async function refreshSchedule() {
    try {
        const res = await fetch(`${API_BASE}/organizations/${activeOrgId}/schedule`);
        if (res.ok) cachedSchedule = await res.json();
    } catch(e) {}
}

function updateViewSelector() {
    const selector = document.getElementById("view-selector");
    const label = document.getElementById("select-label");

    selector.innerHTML = "";
    if (!cachedMeta || !cachedMeta.days.length) {
        selector.innerHTML = '<option value="">-- Empty Roster --</option>';
        return;
    }

    if (activeViewType === "class") {
        label.textContent = "Select Class:";
        cachedMeta.groups.forEach(g => {
            selector.innerHTML += `<option value="${g.id}">${g.name}</option>`;
        });
    } else {
        label.textContent = "Select Teacher:";
        cachedMeta.resources.forEach(r => {
            selector.innerHTML += `<option value="${r.id}">${r.name}</option>`;
        });
    }

    if (selector.options.length > 0) {
        activeFilterId = selector.value;
    }
}

// --- Rendering Schedule Table ---
function renderTimetable() {
    const tbody = document.getElementById("grid-body");
    const headerRow = document.getElementById("grid-header-row");

    // Clean previous rows
    tbody.innerHTML = "";
    
    // Clean headers except first column
    while (headerRow.cells.length > 1) {
        headerRow.deleteCell(1);
    }

    if (!cachedMeta || !cachedMeta.days.length || !activeFilterId) {
        return;
    }

    // Ingest Dynamic Header Day Names
    cachedMeta.days.forEach(day => {
        const th = document.createElement("th");
        th.textContent = day;
        headerRow.appendChild(th);
    });

    // Dynamic slots loop (we have cachedMeta.slots_count slots)
    for (let s = 0; s < cachedMeta.slots_count; s++) {
        const tr = document.createElement("tr");

        // Period name
        const tdTime = document.createElement("td");
        tdTime.className = "slot-time";
        tdTime.textContent = `Period ${s + 1}`;
        tr.appendChild(tdTime);

        // Render days dynamically based on config days
        for (let d = 0; d < cachedMeta.days.length; d++) {
            const tdCell = document.createElement("td");
            
            let entry = null;
            if (activeViewType === "class") {
                entry = cachedSchedule.find(item => item.group_id === activeFilterId && item.day === d && item.slot === s);
            } else {
                entry = cachedSchedule.find(item => item.resource_id === activeFilterId && item.day === d && item.slot === s);
            }

            if (entry) {
                tdCell.className = "lesson-cell";
                tdCell.style.background = getTaskColor(entry.task);
                tdCell.innerHTML = `
                    <div class="cell-content">
                        <span class="cell-task">${entry.task}</span>
                        <span class="cell-sub">${activeViewType === "class" ? entry.resource_name : entry.group_name}</span>
                    </div>
                `;
            } else {
                tdCell.innerHTML = '<span class="empty-cell">-</span>';
            }

            // Click cell to manually edit
            tdCell.addEventListener("click", () => {
                let targetGroupId = activeViewType === "class" ? activeFilterId : (entry ? entry.group_id : "");
                if (targetGroupId) {
                    openEditModal(d, s, targetGroupId, entry ? entry.resource_id : "", entry ? entry.task : "");
                } else {
                    showToast("Switch to Class View to add manual assignments.", "error");
                }
            });

            tr.appendChild(tdCell);
        }
        tbody.appendChild(tr);
    }
}

function getTaskColor(task) {
    let hash = 0;
    for (let i = 0; i < task.length; i++) {
        hash = task.charCodeAt(i) + ((hash << 5) - hash);
    }
    const hue = Math.abs(hash % 360);
    return `hsla(${hue}, 55%, 26%, 0.45)`;
}

// --- Solver History Logs ---
async function loadSolveLogs() {
    const tbody = document.querySelector("#logs-table tbody");
    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center;">Loading audit logs...</td></tr>';
    
    try {
        const res = await fetch(`${API_BASE}/organizations/${activeOrgId}/logs`);
        if (!res.ok) throw new Error("Failed to load logs");
        const logs = await res.json();
        
        if (logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted);">No solve logs found.</td></tr>';
            return;
        }

        tbody.innerHTML = "";
        logs.forEach(log => {
            const timeStr = new Date(log.solved_at).toLocaleString();
            
            let statusBadge = `<span class="badge" style="color: var(--secondary); background: rgba(16, 185, 129, 0.1); border: 1px solid var(--secondary); padding: 4px 8px; border-radius: 6px;">${log.status}</span>`;
            if (log.status === "FAILED") {
                statusBadge = `<span class="badge" style="color: var(--danger); background: rgba(239, 68, 68, 0.1); border: 1px solid var(--danger); padding: 4px 8px; border-radius: 6px;">${log.status}</span>`;
            }

            let solverBadge = `<span class="badge">${log.solver_status}</span>`;
            if (log.solver_status === "OPTIMAL") {
                solverBadge = `<span class="badge" style="color: #60a5fa; background: rgba(96, 165, 250, 0.1); border: 1px solid #60a5fa; padding: 4px 8px; border-radius: 6px;">OPTIMAL</span>`;
            } else if (log.solver_status === "INFEASIBLE") {
                solverBadge = `<span class="badge" style="color: var(--danger); background: rgba(239, 68, 68, 0.1); border: 1px solid var(--danger); padding: 4px 8px; border-radius: 6px;">INFEASIBLE</span>`;
            }

            let details = "Zero violations. Verified conflict-free.";
            if (log.violations && log.violations.length > 0) {
                details = `<ul style="text-align: left; list-style: square inside; font-size: 0.8rem; color: var(--danger);">${log.violations.map(v => `<li>${v}</li>`).join("")}</ul>`;
            }

            tbody.innerHTML += `
                <tr>
                    <td>${timeStr}</td>
                    <td>${solverBadge}</td>
                    <td>${statusBadge}</td>
                    <td>${details}</td>
                </tr>
            `;
        });
    } catch(e) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--danger);">Failed to load logs.</td></tr>';
    }
}

// --- Manual Edit Modal Logic ---
function openEditModal(day, slot, groupId, currentResourceId, currentTask) {
    document.getElementById("edit-day").value = day;
    document.getElementById("edit-slot").value = slot;
    document.getElementById("edit-group-id").value = groupId;
    
    const dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    const activeDayName = cachedMeta.days[day] ? dayNames[day] : `Day ${day + 1}`;
    document.getElementById("edit-slot-desc").textContent = `Class: ${cachedMeta.groups.find(g => g.id === groupId).name} | ${activeDayName}, Period ${slot + 1}`;
    
    const resourceSelect = document.getElementById("edit-resource");
    resourceSelect.innerHTML = "";
    
    cachedMeta.resources.forEach(r => {
        const selected = r.id === currentResourceId ? "selected" : "";
        resourceSelect.innerHTML += `<option value="${r.id}" ${selected}>${r.name}</option>`;
    });

    document.getElementById("edit-task").value = currentTask || "";
    document.getElementById("edit-error-box").classList.add("hidden");
    document.getElementById("modal-edit").classList.remove("hidden");
}

function closeEditModal() {
    document.getElementById("modal-edit").classList.add("hidden");
}

document.getElementById("edit-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
        day: parseInt(document.getElementById("edit-day").value),
        slot: parseInt(document.getElementById("edit-slot").value),
        group_id: document.getElementById("edit-group-id").value,
        resource_id: document.getElementById("edit-resource").value,
        task: document.getElementById("edit-task").value
    };

    const errBox = document.getElementById("edit-error-box");
    const errList = document.getElementById("edit-error-list");
    errBox.classList.add("hidden");
    errList.innerHTML = "";

    try {
        const res = await fetch(`${API_BASE}/organizations/${activeOrgId}/schedule/edit`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const result = await res.json();
        if (res.ok) {
            closeEditModal();
            showToast("Manual override validated & saved!", "success");
            await refreshSchedule();
            renderTimetable();
        } else {
            errBox.classList.remove("hidden");
            const errors = result.detail?.violations || ["Constraint validation failed for manual edit"];
            errors.forEach(err => {
                errList.innerHTML += `<li>${err}</li>`;
            });
            showToast("Constraint violation detected", "error");
        }
    } catch (e) {
        showToast("Error saving manual override", "error");
    }
});

// --- Event Listeners Setup ---
function setupEventListeners() {
    // Navigation Sidebar
    document.getElementById("nav-schedule").addEventListener("click", (e) => {
        e.preventDefault();
        switchSection("timetable");
    });
    
    document.getElementById("nav-logs").addEventListener("click", (e) => {
        e.preventDefault();
        switchSection("logs");
    });

    // View selector dropdown
    document.getElementById("view-selector").addEventListener("change", (e) => {
        activeFilterId = e.target.value;
        renderTimetable();
    });

    // Class/Teacher View Tabs
    document.getElementById("tab-class").addEventListener("click", () => {
        document.getElementById("tab-class").classList.add("active");
        document.getElementById("tab-teacher").classList.remove("active");
        activeViewType = "class";
        updateViewSelector();
        renderTimetable();
    });

    document.getElementById("tab-teacher").addEventListener("click", () => {
        document.getElementById("tab-class").classList.remove("active");
        document.getElementById("tab-teacher").classList.add("active");
        activeViewType = "teacher";
        updateViewSelector();
        renderTimetable();
    });

    // Reset workspace button
    document.getElementById("change-org-btn").addEventListener("click", () => {
        localStorage.clear();
        activeOrgId = "";
        activeOrgName = "";
        if (socket) socket.close();
        showWizard();
    });
}

function showToast(msg, type = "info") {
    const toast = document.getElementById("toast");
    toast.textContent = msg;
    toast.className = `toast ${type}`;
    toast.classList.remove("hidden");
    setTimeout(() => toast.classList.add("hidden"), 4000);
}

// Start App
init();
