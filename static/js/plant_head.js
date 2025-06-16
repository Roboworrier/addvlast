// Chart initialization
function initializeCharts(machineNames, oeeData, shiftLabels, shiftData) {
    const oeeCtx = document.getElementById('oeeChart').getContext('2d');
    const oeeCanvas = document.getElementById('oeeChart');
    const oeeChart = new Chart(oeeCtx, {
        type: 'bar',
        data: {
            labels: machineNames,
            datasets: [{
                label: 'OEE %',
                data: oeeData,
                backgroundColor: 'rgba(54, 162, 235, 0.5)',
                borderColor: 'rgb(54, 162, 235)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100
                }
            }
        }
    });

    const shiftCtx = document.getElementById('shiftChart').getContext('2d');
    const shiftCanvas = document.getElementById('shiftChart');
    const shiftChart = new Chart(shiftCtx, {
        type: 'bar',
        data: {
            labels: shiftLabels,
            datasets: [{
                label: 'Production',
                data: shiftData,
                backgroundColor: 'rgba(75, 192, 192, 0.5)',
                borderColor: 'rgb(75, 192, 192)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });

    return { oeeChart, shiftChart };
}

// Safe data access helper
function safeGet(obj, path, defaultValue = '') {
    return path.split('.').reduce((acc, part) => (acc && acc[part] ? acc[part] : defaultValue), obj);
}

// Sanitize HTML helper
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Initialize Socket.IO and event handlers
function initializeSocketIO(csrfToken, charts) {
    const socket = io();
    const connectionStatus = document.getElementById('connectionStatus');

    socket.on('connect', function() {
        connectionStatus.classList.add('d-none');
    });

    socket.on('connect_error', function() {
        connectionStatus.classList.remove('d-none');
    });

    socket.on('disconnect', function() {
        connectionStatus.classList.remove('d-none');
    });

    // Socket.IO event handlers
    socket.on('machine_update', function(data) {
        // Update machine status cards
        const machineStatus = document.getElementById('machineStatus');
        machineStatus.innerHTML = '';
        data.machines.forEach(machine => {
            const card = document.createElement('div');
            card.className = 'col-md-4 mb-3';
            card.innerHTML = `
                <div class="card">
                    <div class="card-body">
                        <h5>${escapeHtml(machine.name)}</h5>
                        <p>Status: <span class="badge bg-${machine.status === 'available' ? 'success' : machine.status === 'in_use' ? 'warning' : 'danger'}">${escapeHtml(machine.status)}</span></p>
                        <p>Operator: ${escapeHtml(machine.operator)}</p>
                        <p>OEE: ${escapeHtml(String(machine.oee))}%</p>
                    </div>
                </div>
            `;
            machineStatus.appendChild(card);
        });

        // Update OEE chart
        charts.oeeChart.data.datasets[0].data = data.machines.map(m => m.oee);
        charts.oeeChart.update();
    });

    socket.on('production_update', function(data) {
        // Update production table
        const tbody = document.querySelector('#productionTable tbody');
        tbody.innerHTML = '';
        data.production.forEach(row => {
            tbody.innerHTML += `
                <tr>
                    <td>${escapeHtml(row.product)}</td>
                    <td>${escapeHtml(row.sap_id)}</td>
                    <td>${escapeHtml(String(row.planned))}</td>
                    <td>${escapeHtml(String(row.completed))}</td>
                    <td>${escapeHtml(String(row.rejected || 0))}</td>
                    <td>${escapeHtml(String(row.rework || 0))}</td>
                    <td>${escapeHtml(((row.completed / row.planned) * 100).toFixed(1))}%</td>
                </tr>
            `;
        });

        // Update today's production count
        document.getElementById('todaysProduction').textContent = data.production.reduce((sum, row) => sum + row.completed, 0);
    });

    socket.on('quality_update', function(data) {
        // Update quality checks
        const qualityChecks = document.getElementById('qualityChecks');
        qualityChecks.innerHTML = '';
        data.quality_checks.forEach(check => {
            qualityChecks.innerHTML += `
                <div class="mb-2">
                    <strong>${escapeHtml(check.timestamp)}</strong>
                    <span class="badge bg-${check.result === 'pass' ? 'success' : 'danger'}">${escapeHtml(check.result)}</span>
                    <div>${escapeHtml(check.inspector)} - ${escapeHtml(check.type)}</div>
                </div>
            `;
        });
    });
}

// Search Drawing Function
function searchDrawing(csrfToken) {
    const sapId = document.getElementById('sapIdSearch').value.trim();
    if (!sapId) {
        alert('Please enter a SAP ID');
        return;
    }

    fetch(`/api/search_drawing/${encodeURIComponent(sapId)}`, {
        headers: {
            'X-CSRFToken': csrfToken
        }
    })
        .then(response => response.json())
        .then(data => {
            const resultsDiv = document.getElementById('searchResults');
            const detailsDiv = document.getElementById('drawingDetails');
            resultsDiv.style.display = 'block';

            if (data.error) {
                detailsDiv.innerHTML = `<div class="alert alert-danger">Error: ${escapeHtml(data.error)}</div>`;
                return;
            }

            if (!data.exists) {
                detailsDiv.innerHTML = `<div class="alert alert-warning">No drawing found with SAP ID: ${escapeHtml(sapId)}</div>`;
                return;
            }

            let html = `
                <table class="table table-bordered">
                    <tr><th>Drawing Number</th><td>${escapeHtml(data.drawing_number)}</td></tr>
                    <tr><th>SAP ID</th><td>${escapeHtml(data.sap_id)}</td></tr>
                    <tr><th>End Product</th><td>${escapeHtml(data.end_product || 'N/A')}</td></tr>
                    <tr><th>Completed Quantity</th><td>${escapeHtml(String(data.completed_quantity))}</td></tr>
                    <tr><th>Rejected Quantity</th><td>${escapeHtml(String(data.rejected_quantity))}</td></tr>
                    <tr><th>Rework Quantity</th><td>${escapeHtml(String(data.rework_quantity))}</td></tr>
                </table>`;

            if (data.last_operation) {
                html += `
                    <h6 class="mt-3">Last Operation</h6>
                    <table class="table table-bordered">
                        <tr><th>Operator</th><td>${escapeHtml(safeGet(data.last_operation, 'operator'))}</td></tr>
                        <tr><th>Machine</th><td>${escapeHtml(safeGet(data.last_operation, 'machine'))}</td></tr>
                        <tr><th>Status</th><td>${escapeHtml(safeGet(data.last_operation, 'status'))}</td></tr>
                        <tr><th>Timestamp</th><td>${escapeHtml(safeGet(data.last_operation, 'timestamp'))}</td></tr>
                    </table>`;
            }

            detailsDiv.innerHTML = html;
        })
        .catch(error => {
            const detailsDiv = document.getElementById('drawingDetails');
            detailsDiv.innerHTML = `<div class="alert alert-danger">Error: ${escapeHtml(error.message)}</div>`;
        });
}

// Initialize everything when the DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    const machineNames = JSON.parse(document.getElementById('oeeChart').dataset.labels);
    const oeeData = JSON.parse(document.getElementById('oeeChart').dataset.values);
    const shiftLabels = JSON.parse(document.getElementById('shiftChart').dataset.labels);
    const shiftData = JSON.parse(document.getElementById('shiftChart').dataset.values);
    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

    // Initialize charts
    const charts = initializeCharts(machineNames, oeeData, shiftLabels, shiftData);

    // Initialize Socket.IO
    initializeSocketIO(csrfToken, charts);

    // Add event listener for Enter key in search
    document.getElementById('sapIdSearch').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            searchDrawing(csrfToken);
        }
    });

    // Add event listener for search button
    document.querySelector('.search-container button').addEventListener('click', function() {
        searchDrawing(csrfToken);
    });
}); 