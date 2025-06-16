// Socket.IO connection
const socket = io();

// Chart update functions
function updateProductionChart(data) {
    if (!window.productionChart || !data || !Array.isArray(data)) return;
    
    const labels = data.map(item => item.name || 'Unknown');
    const completed = data.map(item => parseInt(item.completed) || 0);
    const planned = data.map(item => parseInt(item.planned) || 0);
    
    window.productionChart.data.labels = labels;
    window.productionChart.data.datasets[0].data = completed;
    window.productionChart.data.datasets[1].data = planned;
    window.productionChart.update();
}

function updateQualityChart(data) {
    if (!window.qualityChart || !data || !Array.isArray(data)) return;
    
    const totalCompleted = data.reduce((sum, item) => sum + (parseInt(item.completed) || 0), 0);
    const totalRejected = data.reduce((sum, item) => sum + (parseInt(item.rejected) || 0), 0);
    const totalRework = data.reduce((sum, item) => sum + (parseInt(item.rework) || 0), 0);
    
    window.qualityChart.data.datasets[0].data = [totalCompleted, totalRejected, totalRework];
    window.qualityChart.update();
}

function updateMachineMetrics(data) {
    if (!window.machineChart || !data || !Array.isArray(data)) return;
    
    const labels = data.map(item => item.machine_name || 'Unknown');
    const efficiency = data.map(item => parseFloat(item.efficiency) || 0);
    
    window.machineChart.data.labels = labels;
    window.machineChart.data.datasets[0].data = efficiency;
    window.machineChart.update();
}

// Table update functions
function updateProductionTable(data) {
    const table = document.getElementById('productionTable');
    if (!table) return;
    
    const tbody = table.querySelector('tbody');
    tbody.innerHTML = '';
    
    data.forEach(item => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${item.name}</td>
            <td>${item.sap_id}</td>
            <td>${item.planned}</td>
            <td>${item.completed}</td>
            <td>${item.rejected}</td>
            <td>${item.rework}</td>
            <td>${item.completion_rate}%</td>
            <td>${item.quality_rate}%</td>
        `;
        tbody.appendChild(row);
    });
}

function updateMachineTable(data) {
    const table = document.getElementById('machineTable');
    if (!table) return;
    
    const tbody = table.querySelector('tbody');
    tbody.innerHTML = '';
    
    data.forEach(item => {
        const row = document.createElement('tr');
        const statusClass = item.status === 'running' ? 'text-success' : 
                          item.status === 'stopped' ? 'text-danger' : 'text-warning';
        
        row.innerHTML = `
            <td>${item.machine_name}</td>
            <td class="${statusClass}">${item.status}</td>
            <td>${item.operator || 'N/A'}</td>
            <td>${item.current_job || 'N/A'}</td>
            <td>${item.parts_completed}</td>
            <td>${item.parts_rejected}</td>
            <td>${item.efficiency}%</td>
        `;
        tbody.appendChild(row);
    });
}

// Socket.IO event handlers
socket.on('connect', () => {
    console.log('Connected to server');
    // Request initial data
    socket.emit('request_update');
});

socket.on('production_update', (data) => {
    if (data && data.data) {
        updateProductionChart(data.data);
        updateQualityChart(data.data);
        updateProductionTable(data.data);
    }
});

socket.on('machine_update', (data) => {
    if (data && data.data) {
        updateMachineMetrics(data.data);
    }
});

socket.on('connect_error', (error) => {
    console.error('Socket connection error:', error);
});

socket.on('error', (error) => {
    console.error('Socket error:', error);
});

// Request updates periodically
setInterval(() => {
    if (socket.connected) {
        socket.emit('request_update');
    }
}, 30000); // Every 30 seconds