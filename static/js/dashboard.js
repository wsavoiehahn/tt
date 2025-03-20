// static/js/dashboard.js

// Global variables to store report data
let allReports = [];
let aggregatedMetrics = {
    overallMetrics: {
        accuracy: 0,
        empathy: 0,
        responseTime: 0,
        successRate: 0
    },
    byPersona: {},
    byBehavior: {}
};

// Chart objects
let personaChart = null;
let behaviorChart = null;

// Initialize the dashboard
document.addEventListener('DOMContentLoaded', function() {
    // Initialize charts with empty data initially
    initializeCharts();
    
    // Fetch initial data
    fetchReports();
    fetchPersonasAndBehaviors();
    
    // Add event listeners for buttons
    setupEventListeners();
});

// Initialize charts
function initializeCharts() {
    const personaCtx = document.getElementById('personaChart').getContext('2d');
    personaChart = new Chart(personaCtx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Accuracy',
                    data: [],
                    backgroundColor: 'rgba(54, 162, 235, 0.7)'
                },
                {
                    label: 'Empathy',
                    data: [],
                    backgroundColor: 'rgba(75, 192, 192, 0.7)'
                }
            ]
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
                        text: 'Score (%)'
                    }
                }
            }
        }
    });

    const behaviorCtx = document.getElementById('behaviorChart').getContext('2d');
    behaviorChart = new Chart(behaviorCtx, {
        type: 'bar',
        data: {
            labels: [],
            datasets: [
                {
                    label: 'Accuracy',
                    data: [],
                    backgroundColor: 'rgba(54, 162, 235, 0.7)'
                },
                {
                    label: 'Empathy',
                    data: [],
                    backgroundColor: 'rgba(75, 192, 192, 0.7)'
                }
            ]
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
                        text: 'Score (%)'
                    }
                }
            }
        }
    });
}

// Update the metrics display with new data
function updateMetricsDisplay(metrics) {
    document.getElementById('overallAccuracy').textContent = `${Math.round(metrics.accuracy * 100)}%`;
    document.getElementById('overallEmpathy').textContent = `${Math.round(metrics.empathy * 100)}%`;
    document.getElementById('avgResponseTime').textContent = `${metrics.responseTime.toFixed(1)}s`;
    document.getElementById('successRate').textContent = `${Math.round(metrics.successRate * 100)}%`;
    
    // Update trends (these could be calculated from historical data in a real application)
    document.getElementById('accuracyTrend').textContent = '+0.0%';
    document.getElementById('empathyTrend').textContent = '+0.0%';
    document.getElementById('responseTrend').textContent = '+0.0s';
    document.getElementById('successTrend').textContent = '+0.0%';
}

// Calculate aggregated metrics from all reports
function calculateAggregatedMetrics(reports) {
    // Initialize metrics objects
    let metrics = {
        overallMetrics: {
            accuracy: 0,
            empathy: 0,
            responseTime: 0,
            successRate: 0,
            totalReports: 0,
            successfulReports: 0
        },
        byPersona: {},
        byBehavior: {}
    };
    
    // Skip if no reports
    if (!reports || reports.length === 0) {
        return metrics;
    }
    
    console.log("Calculating metrics from", reports.length, "reports");
    
    // Process each report
    reports.forEach(report => {
        // Get actual report data, either from data property or directly
        const reportData = report.data || report;
        
        // Extract metrics
        const reportMetrics = reportData.metrics || reportData.overall_metrics || {};        
        // Extract persona and behavior
        const personaName = 
            reportData.persona_name || 
            (reportData.test_case && reportData.test_case.config ? reportData.test_case.config.persona_name : null) || 
            (reportData.config ? reportData.config.persona_name : null) || 
            'Unknown';
        
        const behaviorName = 
            reportData.behavior_name || 
            (reportData.test_case && reportData.test_case.config ? reportData.test_case.config.behavior_name : null) || 
            (reportData.config ? reportData.config.behavior_name : null) || 
            'Unknown';
        
        // Only include reports with valid metrics
        if (reportMetrics && typeof reportMetrics.accuracy === 'number') {
            // Count total reports
            metrics.overallMetrics.totalReports++;
            
            // Check if successful (no error message)
            const isSuccessful = reportMetrics.successful !== false && !reportMetrics.error_message;
            if (isSuccessful) {
                metrics.overallMetrics.successfulReports++;
                
                // Add to overall totals
                metrics.overallMetrics.accuracy += reportMetrics.accuracy || 0;
                metrics.overallMetrics.empathy += reportMetrics.empathy || 0;
                metrics.overallMetrics.responseTime += reportMetrics.response_time || 0;
                
                // Initialize persona metrics if not exists
                if (!metrics.byPersona[personaName]) {
                    metrics.byPersona[personaName] = {
                        accuracy: 0,
                        empathy: 0,
                        count: 0
                    };
                }
                
                // Add to persona metrics
                metrics.byPersona[personaName].accuracy += reportMetrics.accuracy || 0;
                metrics.byPersona[personaName].empathy += reportMetrics.empathy || 0;
                metrics.byPersona[personaName].count++;
                
                // Initialize behavior metrics if not exists
                if (!metrics.byBehavior[behaviorName]) {
                    metrics.byBehavior[behaviorName] = {
                        accuracy: 0,
                        empathy: 0,
                        count: 0
                    };
                }
                
                // Add to behavior metrics
                metrics.byBehavior[behaviorName].accuracy += reportMetrics.accuracy || 0;
                metrics.byBehavior[behaviorName].empathy += reportMetrics.empathy || 0;
                metrics.byBehavior[behaviorName].count++;
            }
        } else {
            console.log("Skipping report with invalid metrics:", reportMetrics);
        }
    });
    
    // Calculate averages for overall metrics
    if (metrics.overallMetrics.successfulReports > 0) {
        metrics.overallMetrics.accuracy /= metrics.overallMetrics.successfulReports;
        metrics.overallMetrics.empathy /= metrics.overallMetrics.successfulReports;
        metrics.overallMetrics.responseTime /= metrics.overallMetrics.successfulReports;
    }
    
    // Calculate success rate
    metrics.overallMetrics.successRate = 
        metrics.overallMetrics.totalReports > 0 
            ? metrics.overallMetrics.successfulReports / metrics.overallMetrics.totalReports 
            : 0;
    
    // Calculate averages for persona metrics
    for (const persona in metrics.byPersona) {
        if (metrics.byPersona[persona].count > 0) {
            metrics.byPersona[persona].accuracy /= metrics.byPersona[persona].count;
            metrics.byPersona[persona].empathy /= metrics.byPersona[persona].count;
        }
    }
    
    // Calculate averages for behavior metrics
    for (const behavior in metrics.byBehavior) {
        if (metrics.byBehavior[behavior].count > 0) {
            metrics.byBehavior[behavior].accuracy /= metrics.byBehavior[behavior].count;
            metrics.byBehavior[behavior].empathy /= metrics.byBehavior[behavior].count;
        }
    }
    
    console.log("Final calculated metrics:", metrics);
    return metrics;
}

// Fetch reports from the API
async function fetchReports() {
    try {
        document.getElementById('loadingReports').style.display = 'block';
        
        const response = await fetch('/api/reports');
        let reports = [];
        
        if (response.ok) {
            reports = await response.json();
            console.log("Fetched reports:", reports);
            
            // Filter out any reports that might be problematic
            reports = reports.filter(report => {
                // Additional checks to ensure report has necessary information
                return report.report_id && 
                       (report.data || report.test_case_name || report.name);
            });
            
            // Store reports globally
            allReports = reports;
            
            // Calculate aggregated metrics
            aggregatedMetrics = calculateAggregatedMetrics(reports);
            
            // Update UI with metrics
            updateMetricsDisplay({
                accuracy: aggregatedMetrics.overallMetrics.accuracy,
                empathy: aggregatedMetrics.overallMetrics.empathy,
                responseTime: aggregatedMetrics.overallMetrics.responseTime,
                successRate: aggregatedMetrics.overallMetrics.successRate
            });
            
            // Update charts
            updatePersonaChart(aggregatedMetrics.byPersona);
            updateBehaviorChart(aggregatedMetrics.byBehavior);
        } else {
            console.error('Error fetching reports:', response.statusText);
        }
        
        displayReports(reports);
        
    } catch (error) {
        console.error('Error fetching reports:', error);
        document.getElementById('loadingReports').style.display = 'none';
        document.getElementById('reportsTableBody').innerHTML = `
            <tr>
                <td colspan="8" class="text-center">
                    <div class="alert alert-warning">
                        Error loading reports. Please try again.
                    </div>
                </td>
            </tr>
        `;
    }
}

// Display reports in the table
function displayReports(reports) {
    const tableBody = document.getElementById('reportsTableBody');
    tableBody.innerHTML = '';
    
    if (reports.length === 0) {
        document.getElementById('noReportsMessage').style.display = 'block';
    } else {
        document.getElementById('noReportsMessage').style.display = 'none';
        
        reports.forEach(report => {
            // Try different ways of extracting data
            let reportData = report.data || report;
            
            // Try multiple paths to find persona and behavior names
            const personaName = 
                reportData.persona_name || 
                (reportData.test_case && reportData.test_case.config ? reportData.test_case.config.persona_name : null) || 
                (reportData.config ? reportData.config.persona_name : null) || 
                'Unknown';
            
            const behaviorName = 
                reportData.behavior_name || 
                (reportData.test_case && reportData.test_case.config ? reportData.test_case.config.behavior_name : null) || 
                (reportData.config ? reportData.config.behavior_name : null) || 
                'Unknown';

            // Check both metrics and overall_metrics properties
            const metrics = reportData.metrics || reportData.overall_metrics || {};

            // Try to extract test case ID
            const testCaseId = 
                reportData.test_case_id || 
                reportData.id || 
                report.report_id || 
                'unknown';

            const row = document.createElement('tr');
            row.innerHTML = `
                <td><a href="/dashboard/reports/${report.report_id}">${report.report_id.substring(0, 8)}...</a></td>
                <td>${reportData.test_case_name || reportData.name || 'Unknown Test Case'}</td>
                <td>
                    <span class="persona-badge">${personaName}</span>
                    <span class="behavior-badge">${behaviorName}</span>
                </td>
                <td>${new Date(report.date).toLocaleString('en-US', { 
                    year: 'numeric', 
                    month: '2-digit', 
                    day: '2-digit', 
                    hour: '2-digit', 
                    minute: '2-digit', 
                    hour12: false, 
                    timeZone: 'America/Chicago' 
                }).replace(',', '')}</td>
                <td>${Math.round((metrics.accuracy || 0) * 100)}%</td>
                <td>${Math.round((metrics.empathy || 0) * 100)}%</td>
                <td>${((metrics.response_time || 0)).toFixed(1)}s</td>
                <td>
                    <div class="btn-group">
                        <a href="/dashboard/reports/${report.report_id}" class="btn btn-sm btn-outline-primary">View</a>
                        <button class="btn btn-sm btn-outline-secondary view-json" data-id="${report.report_id}">JSON</button>
                        <button class="btn btn-sm btn-outline-danger delete-test" data-test-id="${testCaseId}" data-report-id="${report.report_id}">Delete</button>
                    </div>
                </td>
            `;
            
            tableBody.appendChild(row);
        });
    }
    
    document.getElementById('loadingReports').style.display = 'none';
}

// Update persona chart
function updatePersonaChart(personaData) {
    // Convert the persona data to arrays for the chart
    const personaLabels = Object.keys(personaData);
    const accuracyData = personaLabels.map(persona => personaData[persona].accuracy * 100);
    const empathyData = personaLabels.map(persona => personaData[persona].empathy * 100);
    
    // Update chart data
    personaChart.data.labels = personaLabels;
    personaChart.data.datasets[0].data = accuracyData;
    personaChart.data.datasets[1].data = empathyData;
    
    // Update the chart
    personaChart.update();
}

// Update behavior chart
function updateBehaviorChart(behaviorData) {
    // Convert the behavior data to arrays for the chart
    const behaviorLabels = Object.keys(behaviorData);
    const accuracyData = behaviorLabels.map(behavior => behaviorData[behavior].accuracy * 100);
    const empathyData = behaviorLabels.map(behavior => behaviorData[behavior].empathy * 100);
    
    // Update chart data
    behaviorChart.data.labels = behaviorLabels;
    behaviorChart.data.datasets[0].data = accuracyData;
    behaviorChart.data.datasets[1].data = empathyData;
    
    // Update the chart
    behaviorChart.update();
}

// Fetch personas and behaviors
async function fetchPersonasAndBehaviors() {
    try {
        // Try to fetch from API first
        let personas = [];
        let behaviors = [];
        
        try {
            const response = await fetch('/api/personas-behaviors');
            if (response.ok) {
                const data = await response.json();
                if (data.personas && data.personas.length > 0) {
                    personas = data.personas;
                }
                if (data.behaviors && data.behaviors.length > 0) {
                    behaviors = data.behaviors;
                }
            }
        } catch (apiError) {
            console.warn('Could not fetch from API:', apiError);
        }
        
        if (personas.length === 0 || behaviors.length === 0) {
            try {
                const fallbackResponse = await fetch('/api/personas-behaviors');
                if (fallbackResponse.ok) {
                    const fallbackData = await fallbackResponse.json();
                    if (fallbackData.personas && fallbackData.personas.length > 0) {
                        personas = fallbackData.personas;
                    }
                    if (fallbackData.behaviors && fallbackData.behaviors.length > 0) {
                        behaviors = fallbackData.behaviors;
                    }
                }
            } catch (fallbackError) {
                console.warn('Could not fetch from fallback endpoint:', fallbackError);
            }
        }
        
        console.log('Final personas to be used:', personas);
        console.log('Final behaviors to be used:', behaviors);
        
        populateSelects(personas, behaviors);
        
    } catch (error) {
        console.error('Error fetching personas and behaviors:', error);
    }
}

// Populate select dropdowns
function populateSelects(personas, behaviors) {
    if (!personas.length || !behaviors.length) {
        return;
    }
    
    // Populate the persona select
    const personaSelect = document.getElementById('personaSelect');
    personaSelect.innerHTML = '<option value="">Select persona...</option>';
    
    personas.forEach((persona, index) => {
        const option = document.createElement('option');
        option.value = persona.name;
        option.textContent = persona.name;
        option.setAttribute('data-traits', persona.traits.join(', '));
        // Select the first persona by default
        if (index === 0) {
            option.selected = true;
            // Show traits for the default selected persona
            setTimeout(() => {
                const event = new Event('change');
                personaSelect.dispatchEvent(event);
            }, 0);
        }
        personaSelect.appendChild(option);
    });
    
    // Populate the behavior select
    const behaviorSelect = document.getElementById('behaviorSelect');
    behaviorSelect.innerHTML = '<option value="">Select behavior...</option>';
    
    behaviors.forEach((behavior, index) => {
        const option = document.createElement('option');
        option.value = behavior.name;
        option.textContent = behavior.name;
        option.setAttribute('data-characteristics', behavior.characteristics.join(', '));
        // Select the first behavior by default
        if (index === 0) {
            option.selected = true;
            // Show characteristics for the default selected behavior
            setTimeout(() => {
                const event = new Event('change');
                behaviorSelect.dispatchEvent(event);
            }, 0);
        }
        behaviorSelect.appendChild(option);
    });
}

// Form validation function
function validateTestForm() {
    let isValid = true;
    // Reset previous validation visuals
    resetFormValidation();
    
    // Validate test name
    const testNameInput = document.getElementById('testName');
    if (!testNameInput.value.trim()) {
        markInvalid(testNameInput, 'Please enter a test name');
        isValid = false;
    }
    
    // Validate persona selection
    const personaSelect = document.getElementById('personaSelect');
    if (!personaSelect.value) {
        markInvalid(personaSelect, 'Please select a persona');
        isValid = false;
    }
    
    // Validate behavior selection
    const behaviorSelect = document.getElementById('behaviorSelect');
    if (!behaviorSelect.value) {
        markInvalid(behaviorSelect, 'Please select a behavior');
        isValid = false;
    }
    
    // Validate question
    const questionInput = document.getElementById('questionInput');
    if (!questionInput.value.trim()) {
        markInvalid(questionInput, 'Please enter a question');
        isValid = false;
    }
    
    return isValid;
}

// Mark a form input as invalid with visual feedback
function markInvalid(element, message) {
    element.classList.add('is-invalid');
    
    // Create error message if it doesn't exist
    if (!element.nextElementSibling || !element.nextElementSibling.classList.contains('invalid-feedback')) {
        const feedback = document.createElement('div');
        feedback.className = 'invalid-feedback';
        feedback.textContent = message;
        element.parentNode.insertBefore(feedback, element.nextElementSibling);
    }
    
    // Focus on the first invalid element
    if (!document.querySelector('.is-invalid:focus')) {
        element.focus();
    }
}

// Reset form validation visuals
function resetFormValidation() {
    document.querySelectorAll('.is-invalid').forEach(el => {
        el.classList.remove('is-invalid');
    });
    
    document.querySelectorAll('.invalid-feedback').forEach(el => {
        el.parentNode.removeChild(el);
    });
}

// Create a new test
async function createNewTest() {
    const testName = document.getElementById('testName').value;
    const testDescription = document.getElementById('testDescription').value;
    const personaName = document.getElementById('personaSelect').value;
    const behaviorName = document.getElementById('behaviorSelect').value;
    const specialInstructions = document.getElementById('specialInstructions').value;
    const question = document.getElementById('questionInput').value;

    const faqQuestion = document.getElementById('faqQuestion')?.value?.trim();
    const expectedAnswer = document.getElementById('expectedAnswer')?.value?.trim();
    const maxTurns = parseInt(document.getElementById('maxTurns').value) || 4;

    // Construct test case object
    const testCase = {
        name: testName,
        description: testDescription,
        config: {
            persona_name: personaName,
            behavior_name: behaviorName,
            question: question, 
            special_instructions: specialInstructions || null,
            max_turns: maxTurns
        }
    };

    // Add FAQ question and expected answer if both are provided
    if (faqQuestion && expectedAnswer) {
        testCase.config.faq_question = faqQuestion;
        testCase.config.expected_answer = expectedAnswer;
        console.log("Added FAQ question evaluation data to test");
    } else if (faqQuestion || expectedAnswer) {
        console.warn("Both FAQ question and expected answer are required to use this feature. Ignoring incomplete data.");
    }

    try {
        // Submit to API
        const response = await fetch('/api/tests', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(testCase)
        });
        
        const data = await response.json();
        
        if (response.ok) {
            // Close modal and refresh reports
            const modal = bootstrap.Modal.getInstance(document.getElementById('newTestModal'));
            modal.hide();
            
            // Reset form
            document.getElementById('newTestForm').reset();
            
            // Show success message
            showToast(`Test created successfully! Test ID: ${data.test_id}`, 'success');
            
            // Fetch reports after a delay to allow test to complete
            setTimeout(fetchReports, 2000);
        } else {
            const errorDetails = typeof data.detail === 'object' 
            ? JSON.stringify(data.detail, null, 2) 
            : (data.detail || 'Unknown error');
          
            showToast(`Error creating test: ${errorDetails}`, 'error');
        }
    } catch (error) {
        console.error('Error creating test:', error);
        showToast('Error creating test. Please try again.', 'error');
    }
}

// Delete a test
async function deleteTest(testId, reportId) {
    if (!testId) {
        showToast('Could not determine the test ID to delete.', 'error');
        return;
    }
    
    try {
        // Show a loading indicator
        showToast('Deleting test...', 'info');
        
        console.log(`Making DELETE request to /api/tests/${testId}`);
        const response = await fetch(`/api/tests/${testId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        
        const data = await response.json();
        console.log('Delete response:', data);
        
        if (response.ok) {
            // Show success message
            showToast('Test and associated reports deleted successfully.', 'success');
            
            // Refresh the reports list
            fetchReports();
        } else {
            showToast(`Error deleting test: ${data.detail || 'Unknown error'}`, 'error');
        }
    } catch (error) {
        console.error('Error deleting test:', error);
        showToast('Error deleting test. Please try again.', 'error');
    }
}

// Export reports as CSV
function exportReportsAsCSV() {
    // Get the reports table data
    const tableBody = document.getElementById('reportsTableBody');
    const rows = tableBody.querySelectorAll('tr');
    
    if (rows.length === 0) {
        alert('No reports available to export');
        return;
    }
    
    // Create CSV header
    let csv = 'Report ID,Test Case,Persona,Behavior,Date,Accuracy,Empathy,Response Time\n';
    
    // Add rows
    rows.forEach(row => {
        const cells = row.querySelectorAll('td');
        const reportId = cells[0].innerText;
        const testCase = cells[1].innerText;
        const personaText = cells[2].querySelector('.persona-badge').innerText;
        const behaviorText = cells[2].querySelector('.behavior-badge').innerText;
        const date = cells[3].innerText;
        const accuracy = cells[4].innerText;
        const empathy = cells[5].innerText;
        const responseTime = cells[6].innerText;
        
        // Add row to CSV
        csv += `"${reportId}","${testCase}","${personaText}","${behaviorText}","${date}","${accuracy}","${empathy}","${responseTime}"\n`;
    });
    
    // Create download link
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.setAttribute('hidden', '');
    a.setAttribute('href', url);
    a.setAttribute('download', `ai-call-center-reports-${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// Toast notification helper function
function showToast(message, type = 'info') {
    // Check if toast container exists, if not create it
    let toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }
    
    // Create toast element
    const toastEl = document.createElement('div');
    const toastId = 'toast-' + Date.now();
    toastEl.id = toastId;
    toastEl.className = `toast align-items-center text-white ${type === 'error' ? 'bg-danger' : type === 'success' ? 'bg-success' : 'bg-primary'}`;
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');
    
    toastEl.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;
    
    toastContainer.appendChild(toastEl);
    
    // Initialize and show the toast
    const toast = new bootstrap.Toast(toastEl, {
        autohide: true,
        delay: 3000
    });
    toast.show();
    
    return toast;
}

// Set up event listeners
function setupEventListeners() {
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', function() {
        fetchReports();
    });
    
    // New Test button
    document.getElementById('newTestBtn').addEventListener('click', function() {
        // Reset form validation visuals
        resetFormValidation();
        
        // Pre-populate the default question
        const questionInput = document.getElementById('questionInput');
        if (questionInput) {
            questionInput.value = "How can I find my member ID?";
        }
        
        // Set default max turns value
        const maxTurnsInput = document.getElementById('maxTurns');
        if (maxTurnsInput) {
            maxTurnsInput.value = "4";
        }
        
        const modal = new bootstrap.Modal(document.getElementById('newTestModal'));
        modal.show();
    });
    
    // Submit Test button
    document.getElementById('submitTestBtn').addEventListener('click', function() {
        // Validate form before proceeding
        if (validateTestForm()) {
            createNewTest();
        }
    });
    
    // Export Reports button
    document.getElementById('exportReportsBtn').addEventListener('click', exportReportsAsCSV);
    
    // Add event delegation for view JSON buttons
    document.getElementById('reportsTableBody').addEventListener('click', function(e) {
        if (e.target.classList.contains('view-json') || e.target.parentElement.classList.contains('view-json')) {
            const button = e.target.closest('.view-json');
            if (button) {
                const reportId = button.getAttribute('data-id');
                window.open(`/api/reports/${reportId}`, '_blank');
            }
        }
        
        // Event delegation for delete test buttons
        if (e.target.classList.contains('delete-test') || e.target.parentElement.classList.contains('delete-test')) {
            const button = e.target.closest('.delete-test');
            if (button) {
                const testId = button.getAttribute('data-test-id');
                const reportId = button.getAttribute('data-report-id');
                
                // For debugging
                console.log(`Delete button clicked with test_id: ${testId}, report_id: ${reportId}`);
                
                // Store the IDs for use by the confirmation button
                document.getElementById('confirmDeleteBtn').setAttribute('data-test-id', testId);
                document.getElementById('confirmDeleteBtn').setAttribute('data-report-id', reportId);
                
                // Show the confirmation modal
                const deleteModal = new bootstrap.Modal(document.getElementById('deleteConfirmModal'));
                deleteModal.show();
            }
        }
    });
    
    // Add event listener for the confirm delete button
    document.getElementById('confirmDeleteBtn').addEventListener('click', function() {
        const testId = this.getAttribute('data-test-id');
        const reportId = this.getAttribute('data-report-id');
        
        console.log(`Confirming deletion of test: ${testId}, report: ${reportId}`);
        
        // Close the modal
        const deleteModal = bootstrap.Modal.getInstance(document.getElementById('deleteConfirmModal'));
        deleteModal.hide();
        
        // Call the delete function
        deleteTest(testId, reportId);
    });
    
    // Set up persona and behavior select dropdowns to show tooltips with traits
    const personaSelect = document.getElementById('personaSelect');
    personaSelect.addEventListener('change', function() {
        const selectedOption = personaSelect.options[personaSelect.selectedIndex];
        const traits = selectedOption.getAttribute('data-traits');
        if (traits) {
            const traitsContainer = document.createElement('div');
            traitsContainer.className = 'mt-2 small text-muted';
            traitsContainer.innerHTML = `<strong>Traits:</strong> ${traits}`;
            
            // Remove existing traits container if any
            const existingTraits = personaSelect.parentNode.querySelector('.text-muted');
            if (existingTraits) {
                personaSelect.parentNode.removeChild(existingTraits);
            }
            
            personaSelect.parentNode.appendChild(traitsContainer);
        }
    });
    
    const behaviorSelect = document.getElementById('behaviorSelect');
    behaviorSelect.addEventListener('change', function() {
        const selectedOption = behaviorSelect.options[behaviorSelect.selectedIndex];
        const characteristics = selectedOption.getAttribute('data-characteristics');
        if (characteristics) {
            const characteristicsContainer = document.createElement('div');
            characteristicsContainer.className = 'mt-2 small text-muted';
            characteristicsContainer.innerHTML = `<strong>Characteristics:</strong> ${characteristics}`;
            
            // Remove existing characteristics container if any
            const existingCharacteristics = behaviorSelect.parentNode.querySelector('.text-muted');
            if (existingCharacteristics) {
                behaviorSelect.parentNode.removeChild(existingCharacteristics);
            }
            
            behaviorSelect.parentNode.appendChild(characteristicsContainer);
        }
    });
}