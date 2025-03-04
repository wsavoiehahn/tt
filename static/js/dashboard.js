// static/js/dashboard.js

// Sample data for initial rendering - this would be replaced with actual API data
const sampleData = {
    overallMetrics: {
        accuracy: 0.86,
        empathy: 0.79,
        responseTime: 2.4,
        successRate: 0.92
    },
    byPersona: {
        "Tech-Savvy": { accuracy: 0.90, empathy: 0.75 },
        "Non-Native Speaker": { accuracy: 0.82, empathy: 0.85 },
        "First Time Customer": { accuracy: 0.88, empathy: 0.80 },
        "Accidental Customer": { accuracy: 0.84, empathy: 0.76 }
    },
    byBehavior: {
        "frustrated": { accuracy: 0.81, empathy: 0.77 },
        "confused": { accuracy: 0.89, empathy: 0.83 },
        "urgent": { accuracy: 0.88, empathy: 0.76 }
    }
};

// Chart objects
let personaChart = null;
let behaviorChart = null;

// Initialize the dashboard
document.addEventListener('DOMContentLoaded', function() {
    // Initialize charts
    initializeCharts();
    
    // Fetch initial data
    fetchReports();
    fetchPersonasAndBehaviors();
    fetchAggregateData();
    
    // Add event listeners for buttons
    setupEventListeners();
});

// Initialize charts
function initializeCharts() {
    const personaCtx = document.getElementById('personaChart').getContext('2d');
    personaChart = new Chart(personaCtx, {
        type: 'bar',
        data: {
            labels: Object.keys(sampleData.byPersona),
            datasets: [
                {
                    label: 'Accuracy',
                    data: Object.values(sampleData.byPersona).map(p => p.accuracy * 100),
                    backgroundColor: 'rgba(54, 162, 235, 0.7)'
                },
                {
                    label: 'Empathy',
                    data: Object.values(sampleData.byPersona).map(p => p.empathy * 100),
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
            labels: Object.keys(sampleData.byBehavior),
            datasets: [
                {
                    label: 'Accuracy',
                    data: Object.values(sampleData.byBehavior).map(b => b.accuracy * 100),
                    backgroundColor: 'rgba(54, 162, 235, 0.7)'
                },
                {
                    label: 'Empathy',
                    data: Object.values(sampleData.byBehavior).map(b => b.empathy * 100),
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

    // Update initial metrics display
    updateMetricsDisplay(sampleData.overallMetrics);
}

// Update the metrics display with new data
function updateMetricsDisplay(metrics) {
    document.getElementById('overallAccuracy').textContent = `${Math.round(metrics.accuracy * 100)}%`;
    document.getElementById('overallEmpathy').textContent = `${Math.round(metrics.empathy * 100)}%`;
    document.getElementById('avgResponseTime').textContent = `${metrics.responseTime.toFixed(1)}s`;
    document.getElementById('successRate').textContent = `${Math.round(metrics.successRate * 100)}%`;
}

// Fetch reports from the API
async function fetchReports() {
    try {
        document.getElementById('loadingReports').style.display = 'block';
        
        // In a real implementation, this would fetch from the API
        const response = await fetch('/api/reports');
        let reports = [];
        
        if (response.ok) {
            reports = await response.json();
        } else {
            console.error('Error fetching reports:', response.statusText);
            // Use empty array for reports if API call fails
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
            const row = document.createElement('tr');

            let reportData;
            if (report.data) {
                reportData = report.data;
            } else {
                reportData = report;
            }
            
            // const reportData = report.data || {};
            const personaName = reportData.persona_name || 'Unknown';
            const behaviorName = reportData.behavior_name || 'Unknown';

            const metrics = reportData.overall_metrics || {};
            
            row.innerHTML = `
                <td><a href="/dashboard/reports/${report.report_id}">${report.report_id.substring(0, 8)}...</a></td>
                <td>${reportData.test_case_name || 'Unknown'}</td>
                <td>
                    <span class="persona-badge">${personaName}</span>
                    <span class="behavior-badge">${behaviorName}</span>
                </td>
                <td>${new Date(report.date).toLocaleString()}</td>
                <td>${Math.round((metrics.accuracy || 0) * 100)}%</td>
                <td>${Math.round((metrics.empathy || 0) * 100)}%</td>
                <td>${(metrics.response_time || 0).toFixed(1)}s</td>
                <td>
                    <div class="btn-group">
                        <a href="/dashboard/reports/${report.report_id}" class="btn btn-sm btn-outline-primary">View</a>
                        <button class="btn btn-sm btn-outline-secondary view-json" data-id="${report.report_id}">JSON</button>
                    </div>
                </td>
            `;
            
            tableBody.appendChild(row);
        });
    }
    
    document.getElementById('loadingReports').style.display = 'none';
}

// Fetch personas and behaviors
async function fetchPersonasAndBehaviors() {
    try {
        // In a real implementation, this would fetch from an API endpoint
        // For now, we'll use hardcoded values based on the provided JSON
        const personas = [
            { name: "Tech-Savvy", traits: ["knowledgeable", "efficient", "solution-oriented", "independent", "precise"] },
            { name: "Non-Native Speaker", traits: ["careful with language", "may need clarification", "persistent", "apologetic", "attentive"] },
            { name: "First Time Customer", traits: ["uncertain", "inquisitive", "careful", "detail-seeking", "needs reassurance"] },
            { name: "Accidental Customer", traits: ["confused", "potentially frustrated", "uncertain", "wanting clarification", "may be embarrassed"] }
        ];
        
        const behaviors = [
            { name: "frustrated", characteristics: ["shows impatience", "may use stronger language", "emphasizes urgency", "references previous attempts", "seeks immediate resolution"] },
            { name: "confused", characteristics: ["asks for clarification", "may repeat questions", "expresses uncertainty", "seeks confirmation", "may misunderstand instructions"] },
            { name: "urgent", characteristics: ["emphasizes time sensitivity", "seeks immediate solutions", "may interrupt", "focused on quick resolution", "may express consequences of delay"] }
        ];
        
        populateSelects(personas, behaviors);
        
    } catch (error) {
        console.error('Error fetching personas and behaviors:', error);
    }
}

// Populate select dropdowns
function populateSelects(personas, behaviors) {
    // Populate the persona select
    const personaSelect = document.getElementById('personaSelect');
    personaSelect.innerHTML = '<option value="">Select persona...</option>';
    
    personas.forEach(persona => {
        const option = document.createElement('option');
        option.value = persona.name;
        option.textContent = persona.name;
        option.setAttribute('data-traits', persona.traits.join(', '));
        personaSelect.appendChild(option);
    });
    
    // Populate the behavior select
    const behaviorSelect = document.getElementById('behaviorSelect');
    behaviorSelect.innerHTML = '<option value="">Select behavior...</option>';
    
    behaviors.forEach(behavior => {
        const option = document.createElement('option');
        option.value = behavior.name;
        option.textContent = behavior.name;
        option.setAttribute('data-characteristics', behavior.characteristics.join(', '));
        behaviorSelect.appendChild(option);
    });
}

// Fetch aggregate data and update charts
async function fetchAggregateData() {
    try {
        // In a real implementation, this would fetch from an API endpoint
        // For now, we're using sample data defined earlier
        
        // Update charts with data
        updatePersonaChart(sampleData.byPersona);
        updateBehaviorChart(sampleData.byBehavior);
    } catch (error) {
        console.error('Error fetching aggregate data:', error);
    }
}

// Update persona chart
function updatePersonaChart(data) {
    const personaLabels = Object.keys(data);
    const accuracyData = Object.values(data).map(p => p.accuracy * 100);
    const empathyData = Object.values(data).map(p => p.empathy * 100);
    
    personaChart.data.labels = personaLabels;
    personaChart.data.datasets[0].data = accuracyData;
    personaChart.data.datasets[1].data = empathyData;
    personaChart.update();
}

// Update behavior chart
function updateBehaviorChart(data) {
    const behaviorLabels = Object.keys(data);
    const accuracyData = Object.values(data).map(b => b.accuracy * 100);
    const empathyData = Object.values(data).map(b => b.empathy * 100);
    
    behaviorChart.data.labels = behaviorLabels;
    behaviorChart.data.datasets[0].data = accuracyData;
    behaviorChart.data.datasets[1].data = empathyData;
    behaviorChart.update();
}

// Create a new test
async function createNewTest() {
    const testName = document.getElementById('testName').value;
    const testDescription = document.getElementById('testDescription').value;
    const personaName = document.getElementById('personaSelect').value;
    const behaviorName = document.getElementById('behaviorSelect').value;
    const specialInstructions = document.getElementById('specialInstructions').value;
    
    // Get questions
    const questionInputs = document.querySelectorAll('.question-input');
    const questions = Array.from(questionInputs).map(input => ({
        text: input.value,
        follow_ups: [],
        expected_topic: null
    }));
    
    if (questions.length === 0) {
        alert('Please add at least one question');
        return;
    }
    
    // Validate inputs
    if (!testName) {
        alert('Please enter a test name');
        return;
    }
    
    if (!personaName) {
        alert('Please select a persona');
        return;
    }
    
    if (!behaviorName) {
        alert('Please select a behavior');
        return;
    }
    
    // Check if all questions have text
    const emptyQuestions = questions.some(q => !q.text);
    if (emptyQuestions) {
        alert('Please fill in all questions');
        return;
    }
    
    // Construct test case object
    const testCase = {
        name: testName,
        description: testDescription,
        config: {
            persona_name: personaName,
            behavior_name: behaviorName,
            questions: questions,
            special_instructions: specialInstructions || null,
            max_turns: 4
        }
    };
    
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
            alert(`Test created successfully! Test ID: ${data.test_id}`);
            
            // Fetch reports after a delay to allow test to complete
            setTimeout(fetchReports, 2000);
        } else {
            alert(`Error creating test: ${data.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error creating test:', error);
        alert('Error creating test. Please try again.');
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

// Set up event listeners
function setupEventListeners() {
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', function() {
        fetchReports();
        fetchAggregateData();
    });
    
    // New Test button
    document.getElementById('newTestBtn').addEventListener('click', function() {
        const modal = new bootstrap.Modal(document.getElementById('newTestModal'));
        modal.show();
    });
    
    // Submit Test button
    document.getElementById('submitTestBtn').addEventListener('click', createNewTest);
    
    // Export Reports button
    document.getElementById('exportReportsBtn').addEventListener('click', exportReportsAsCSV);
    
    // Add Question button
    document.getElementById('addQuestionBtn').addEventListener('click', function() {
        const questionsContainer = document.getElementById('questionsContainer');
        const newQuestion = document.createElement('div');
        newQuestion.className = 'question-item mb-2';
        newQuestion.innerHTML = `
            <div class="input-group">
                <input type="text" class="form-control question-input" placeholder="Enter question" required>
                <button type="button" class="btn btn-outline-secondary remove-question">
                    <i class="bi bi-trash"></i> Remove
                </button>
            </div>
        `;
        questionsContainer.appendChild(newQuestion);
    });
    
    // Add event delegation for remove question buttons
    document.getElementById('questionsContainer').addEventListener('click', function(e) {
        if (e.target.classList.contains('remove-question') || e.target.parentElement.classList.contains('remove-question')) {
            const questionItem = e.target.closest('.question-item');
            if (questionItem) {
                // Always keep at least one question
                const questionsCount = document.querySelectorAll('.question-item').length;
                if (questionsCount > 1) {
                    questionItem.parentElement.removeChild(questionItem);
                } else {
                    alert('You must have at least one question');
                }
            }
        }
    });
    
    // Add event delegation for view JSON buttons
    document.getElementById('reportsTableBody').addEventListener('click', function(e) {
        if (e.target.classList.contains('view-json') || e.target.parentElement.classList.contains('view-json')) {
            const button = e.target.closest('.view-json');
            if (button) {
                const reportId = button.getAttribute('data-id');
                window.open(`/api/reports/${reportId}`, '_blank');
            }
        }
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