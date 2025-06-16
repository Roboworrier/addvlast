document.addEventListener('DOMContentLoaded', function () {
    // --- Production Chart ---
    const productionData = JSON.parse(document.getElementById('production-data').value || '[]');

    const productionCtx = document.getElementById('productionChart')?.getContext('2d');
    if (productionCtx) {
        new Chart(productionCtx, {
            type: 'bar',
            data: {
                labels: productionData.map(item => item.name || 'Unknown'),
                datasets: [
                    {
                        label: 'Planned',
                        data: productionData.map(item => parseInt(item.planned) || 0),
                        backgroundColor: 'rgba(54, 162, 235, 0.8)',
                        borderColor: 'rgba(54, 162, 235, 1)',
                        borderWidth: 1
                    },
                    {
                        label: 'Completed',
                        data: productionData.map(item => parseInt(item.completed) || 0),
                        backgroundColor: 'rgba(75, 192, 192, 0.8)',
                        borderColor: 'rgba(75, 192, 192, 1)',
                        borderWidth: 1
                    },
                    {
                        label: 'Rejected',
                        data: productionData.map(item => parseInt(item.rejected) || 0),
                        backgroundColor: 'rgba(255, 99, 132, 0.8)',
                        borderColor: 'rgba(255, 99, 132, 1)',
                        borderWidth: 1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Quantity'
                        }
                    }
                },
                plugins: {
                    legend: {
                        position: 'top'
                    }
                }
            }
        });
    }

    // --- Machine Status Doughnut Chart ---
    const machineStatusCtx = document.getElementById('machineStatusChart')?.getContext('2d');
    const machineStats = JSON.parse(document.getElementById('machine-stats').value || '{"running":0,"idle":0,"breakdown":0}');
    const activeMachines = machineStats.running || 0;
    const idleMachines = machineStats.idle || 0;
    const breakdownMachines = machineStats.breakdown || 0;
    const totalMachines = activeMachines + idleMachines + breakdownMachines;

    const utilization = totalMachines > 0 ? Math.round((activeMachines / totalMachines) * 100) : 0;
    const utilElement = document.getElementById('machine-utilization');
    if (utilElement) utilElement.textContent = utilization + '%';

    if (machineStatusCtx) {
        new Chart(machineStatusCtx, {
            type: 'doughnut',
            data: {
                labels: ['Running', 'Idle', 'Breakdown'],
                datasets: [{
                    data: [activeMachines, idleMachines, breakdownMachines],
                    backgroundColor: [
                        'rgba(40, 167, 69, 0.8)',
                        'rgba(255, 193, 7, 0.8)',
                        'rgba(220, 53, 69, 0.8)'
                    ],
                    borderColor: [
                        'rgba(40, 167, 69, 1)',
                        'rgba(255, 193, 7, 1)',
                        'rgba(220, 53, 69, 1)'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }

    // --- Machine Utilization Chart ---
    const machineUtilCtx = document.getElementById('machineUtilizationChart')?.getContext('2d');
    const machineLabels = JSON.parse(document.getElementById('machine-uptime').value || '[]');
    const machineUtilData = JSON.parse(document.getElementById('machine-uptime').value || '[]');

    if (machineUtilCtx) {
        new Chart(machineUtilCtx, {
            type: 'bar',
            data: {
                labels: machineLabels,
                datasets: [{
                    label: 'Utilization (%)',
                    data: machineUtilData,
                    backgroundColor: 'rgba(54, 162, 235, 0.8)',
                    borderColor: 'rgba(54, 162, 235, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: {
                            display: true,
                            text: 'Percentage (%)'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    }
                }
            }
        });
    }

    // --- Machine Uptime Chart ---
    const uptimeCtx = document.getElementById('uptimeChart')?.getContext('2d');
    const uptimeData = JSON.parse(document.getElementById('machine-uptime').value || '[]');

    if (uptimeCtx) {
        new Chart(uptimeCtx, {
            type: 'bar',
            data: {
                labels: machineLabels,
                datasets: [{
                    label: 'Uptime (Hours)',
                    data: uptimeData,
                    backgroundColor: 'rgba(75, 192, 192, 0.8)',
                    borderColor: 'rgba(75, 192, 192, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 8,
                        title: {
                            display: true,
                            text: 'Hours'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    }
                }
            }
        });
    }

    // --- Production Statistics Chart ---
    const productionStatsCtx = document.getElementById('productionStatsChart')?.getContext('2d');
    const totalProduction = JSON.parse(document.getElementById('production-summary').value || '[]').reduce((sum, item) => sum + parseInt(item.planned) || 0, 0);
    const completedProduction = JSON.parse(document.getElementById('production-summary').value || '[]').reduce((sum, item) => sum + parseInt(item.completed) || 0, 0);
    const rejectedProduction = JSON.parse(document.getElementById('production-summary').value || '[]').reduce((sum, item) => sum + parseInt(item.rejected) || 0, 0);
    const reworkProduction = JSON.parse(document.getElementById('production-summary').value || '[]').reduce((sum, item) => sum + parseInt(item.rework) || 0, 0);

    if (productionStatsCtx) {
        new Chart(productionStatsCtx, {
            type: 'pie',
            data: {
                labels: ['Total Planned', 'Completed', 'Rejected', 'Rework'],
                datasets: [{
                    data: [totalProduction, completedProduction, rejectedProduction, reworkProduction],
                    backgroundColor: [
                        'rgba(54, 162, 235, 0.8)',
                        'rgba(75, 192, 192, 0.8)',
                        'rgba(255, 99, 132, 0.8)',
                        'rgba(255, 206, 86, 0.8)'
                    ],
                    borderColor: [
                        'rgba(54, 162, 235, 1)',
                        'rgba(75, 192, 192, 1)',
                        'rgba(255, 99, 132, 1)',
                        'rgba(255, 206, 86, 1)'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }

    // --- Machine Utilization (Running vs Idle) Chart ---
    const machineUtilRunningIdleCtx = document.getElementById('machineUtilizationRunningIdleChart')?.getContext('2d');
    const runningMachines = JSON.parse(document.getElementById('machine-stats').value || '{"running":0,"idle":0,"breakdown":0}').running || 0;
    const idleMachinesCount = JSON.parse(document.getElementById('machine-stats').value || '{"running":0,"idle":0,"breakdown":0}').idle || 0;

    if (machineUtilRunningIdleCtx) {
        new Chart(machineUtilRunningIdleCtx, {
            type: 'bar',
            data: {
                labels: ['Running', 'Idle'],
                datasets: [{
                    label: 'Machine Count',
                    data: [runningMachines, idleMachinesCount],
                    backgroundColor: [
                        'rgba(40, 167, 69, 0.8)', // Green for running
                        'rgba(255, 193, 7, 0.8)'  // Yellow for idle
                    ],
                    borderColor: [
                        'rgba(40, 167, 69, 1)',
                        'rgba(255, 193, 7, 1)'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 10, // Adjust based on your max expected value
                        title: {
                            display: true,
                            text: 'Number of Machines'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(tooltipItem) {
                                return tooltipItem.label + ': ' + tooltipItem.raw;
                            }
                        }
                    }
                }
            }
        });
    }

    // Update charts periodically
    setInterval(function() {
        fetch('/api/daily_production')
            .then(response => response.json())
            .then(data => {
                if (window.productionChart && data) {
                    window.productionChart.data.datasets[0].data = data;
                    window.productionChart.update();
                }
            })
            .catch(error => console.error('Error updating charts:', error));
    }, 30000); // Update every 30 seconds
});
