'use strict';

document.addEventListener('DOMContentLoaded', function() {
    const { project, timeline, machine } = window.chartData;

    // Project Progress Chart
    const projectProgressCtx = document.getElementById('projectProgressChart')?.getContext('2d');
    if (projectProgressCtx) {
        new Chart(projectProgressCtx, {
            type: 'bar',
            data: {
                labels: project.names,
                datasets: [{
                    label: 'Project Completion %',
                    data: project.completion,
                    backgroundColor: project.colors,
                    borderColor: project.borderColors,
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
                            text: 'Completion %'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Project Name'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    title: {
                        display: true,
                        text: 'Project Progress',
                        font: { size: 16 }
                    }
                }
            }
        });
    }

    // Production Timeline Chart
    const timelineCtx = document.getElementById('productionTimelineChart')?.getContext('2d');
    if (timelineCtx) {
        new Chart(timelineCtx, {
            type: 'line',
            data: {
                labels: timeline.dates,
                datasets: [
                    {
                        label: 'Target Production',
                        data: timeline.targetProduction,
                        borderColor: 'rgb(54, 162, 235)',
                        backgroundColor: 'rgba(54, 162, 235, 0.1)',
                        fill: true,
                        tension: 0.1
                    },
                    {
                        label: 'Actual Production',
                        data: timeline.actualProduction,
                        borderColor: 'rgb(75, 192, 192)',
                        backgroundColor: 'rgba(75, 192, 192, 0.1)',
                        fill: true,
                        tension: 0.1
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
                            text: 'Production Quantity'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Date'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    title: {
                        display: true,
                        text: 'Production Timeline',
                        font: { size: 16 }
                    }
                }
            }
        });
    }

    // Machine Workload Chart
    const workloadCtx = document.getElementById('machineWorkloadChart')?.getContext('2d');
    if (workloadCtx) {
        new Chart(workloadCtx, {
            type: 'bar',
            data: {
                labels: machine.names,
                datasets: [{
                    label: 'Current Machine Load %',
                    data: machine.workload,
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
                        max: 100,
                        title: {
                            display: true,
                            text: 'Workload %'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Machine Name'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    title: {
                        display: true,
                        text: 'Machine Workload',
                        font: { size: 16 }
                    }
                }
            }
        });
    }

    // Socket.IO connection for real-time updates
    const socket = io();
    
    socket.on('production_update', function(data) {
        if (data && data.end_products) {
            // Update project progress bars
            data.end_products.forEach(function(project) {
                project.end_products.forEach(function(ep) {
                    const progressBar = document.querySelector(`#progress-${ep.sap_id}`);
                    if (progressBar) {
                        const progress = ep.quantity > 0 ? (ep.completed / ep.quantity * 100) : 0;
                        progressBar.style.width = `${progress}%`;
                        progressBar.textContent = `${progress.toFixed(1)}%`;
                        
                        // Update progress bar color
                        progressBar.classList.remove('bg-success', 'bg-warning', 'bg-danger');
                        if (progress >= 90) {
                            progressBar.classList.add('bg-success');
                        } else if (progress >= 60) {
                            progressBar.classList.add('bg-warning');
                        } else {
                            progressBar.classList.add('bg-danger');
                        }
                    }
                });
            });
        }
    });

    // Request initial data
    socket.emit('request_update');
});
