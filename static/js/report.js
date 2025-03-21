// static/js/report.js

// Consolidated DOMContentLoaded handler with proper error handling
document.addEventListener('DOMContentLoaded', async function() {
    try {
        console.log('DOM fully loaded and parsed');
        
        // Check for required templates
        const questionTemplate = document.getElementById('questionTemplate');
        const turnTemplate = document.getElementById('turnTemplate');
        
        if (!questionTemplate) {
            console.error('Error: questionTemplate element is missing from the DOM');
            showError('Template error: Question template is missing');
            return;
        }
        
        if (!turnTemplate) {
            console.error('Error: turnTemplate element is missing from the DOM');
            showError('Template error: Turn template is missing');
            return;
        }
        
        // Get report ID from URL
        const reportId = getReportIdFromUrl();
        if (!reportId) {
            showError('Invalid report ID in URL');
            return;
        }
        
        console.log(`Loading report ID: ${reportId}`);
        
        // Fetch report data
        await fetchReportData(reportId);
        
        // Once report data is loaded, load audio for all conversation turns
        loadAudioForConversation();
        
        // Set up export buttons - using more robust selector approach
        setupExportButtons(reportId);
    
    } catch (error) {
    console.error('Error in DOMContentLoaded:', error);
    showError(`Failed to load report: ${error.message}`);
    }
});
// Function to set up export buttons with proper error handling
function setupExportButtons(reportId) {
    console.log('Setting up export buttons');
    
    // Get the dropdown menu items using more general selectors
    const exportHtmlBtn = document.querySelector('.dropdown-menu a[id="exportHtmlBtn"]');
    if (exportHtmlBtn) {
    console.log('Found HTML export button, attaching event listener');
    exportHtmlBtn.addEventListener('click', function(e) {
        e.preventDefault();
        window.open(`/api/reports/${reportId}/html`, '_blank');
    });
    } else {
    console.warn('HTML export button not found in DOM');
    }
    
    const exportWithAudioBtn = document.querySelector('.dropdown-menu a[id="exportWithAudioBtn"]');
    if (exportWithAudioBtn) {
    console.log('Found audio export button, attaching event listener');
    exportWithAudioBtn.addEventListener('click', function(e) {
        e.preventDefault();
        exportReportWithAudio();
    });
    } else {
    console.warn('Audio export button not found in DOM');
    
    // Fallback - try to add listener to the main export button if the dropdown isn't there
    const mainExportBtn = document.getElementById('exportReportBtn');
    if (mainExportBtn) {
        console.log('Found main export button, adding audio export capability');
        // Replace single button with new functionality
        mainExportBtn.addEventListener('click', function(e) {
        e.preventDefault();
        exportReportWithAudio();
        });
    } else {
        console.warn('No export buttons found in the DOM');
    }
    }
}
// Extract report ID from URL
function getReportIdFromUrl() {
  const pathParts = window.location.pathname.split('/');
  return pathParts[pathParts.indexOf('reports') + 1];
}

// Make report data globally accessible
window.currentReport = null;

// Fetch report data from API
async function fetchReportData(reportId) {
  document.getElementById('loadingReport').style.display = 'flex';
  document.getElementById('reportContent').style.display = 'none';
  console.log(`Fetching report data for ID: ${reportId}`);

  const response = await fetch(`/api/reports/${reportId}`);
  if (!response.ok) {
      const errorText = await response.text();
      console.error("Error response body:", errorText);
      throw new Error(`Error fetching report: ${response.statusText}`);
  }
  const report = await response.json();
  console.log('Report data received:', report);
  
  // Store report data globally for access by other functions
  window.currentReport = report;

  populateReportDetails(report);
  document.getElementById('loadingReport').style.display = 'none';
  document.getElementById('reportContent').style.display = 'block';
}

document.getElementById('refreshReportBtn').addEventListener('click', async function() {
    const reportId = getReportIdFromUrl();
    if (!reportId) return;
    
    // Show loading spinner
    this.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Refreshing...';
    this.disabled = true;
    
    try {
      // Force fetch fresh data
      const response = await fetch(`/api/reports/${reportId}?_=${Date.now()}`);
      if (response.ok) {
        const report = await response.json();
        window.currentReport = report;
        populateReportDetails(report);
        console.log("Report data refreshed");
      }
    } catch (error) {
      console.error("Error refreshing report:", error);
    } finally {
      // Restore button state
      this.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Refresh Conversation';
      this.disabled = false;
    }
  });

// Display error message in the UI
function showError(message) {
  document.getElementById('reportContent').innerHTML = `
      <div class="alert alert-danger">
          <h4>Error Loading Report</h4>
          <p>${message}</p>
          <a href="/dashboard" class="btn btn-primary">Return to Dashboard</a>
      </div>
  `;
  document.getElementById('reportContent').style.display = 'block';
}

function populateReportDetails(report) {
    // Set the title
    const reportTitleEl = document.getElementById('reportTitle');
    if (reportTitleEl) {
        reportTitleEl.textContent = `Test Case Report: ${report.test_case_name || 'Unknown Test'}`;
    }
    
    // Set persona and behavior badges
    const personaBadge = document.getElementById('personaBadge');
    const behaviorBadge = document.getElementById('behaviorBadge');
    if (personaBadge) personaBadge.textContent = `Persona: ${report.persona_name || 'Unknown'}`;
    if (behaviorBadge) behaviorBadge.textContent = `Behavior: ${report.behavior_name || 'Unknown'}`;
    
    // Get and display the question
    const mainQuestion = report.question || "No question specified";
    
    // Create or update question display element
    let questionElement = document.getElementById('mainQuestion');
    if (!questionElement) {
        // Create the element if it doesn't exist
        questionElement = document.createElement('div');
        questionElement.id = 'mainQuestion';
        questionElement.className = 'main-question mt-2';
        
        // Insert after report metadata
        const reportMetadata = document.getElementById('reportMetadata');
        if (reportMetadata && reportMetadata.parentNode) {
            reportMetadata.parentNode.insertBefore(questionElement, reportMetadata.nextSibling);
        }
    }
    
    if (questionElement) {
        questionElement.textContent = mainQuestion;
    }
    
    // Set metrics
    const metrics = report.metrics || {};
    
    const accuracyEl = document.getElementById('overallAccuracy');
    const empathyEl = document.getElementById('overallEmpathy');
    const responseTimeEl = document.getElementById('avgResponseTime');
    const executionTimeEl = document.getElementById('executionTime');
    
    if (accuracyEl) accuracyEl.textContent = `${Math.round((metrics.accuracy || 0) * 100)}%`;
    if (empathyEl) empathyEl.textContent = `${Math.round((metrics.empathy || 0) * 100)}%`;
    if (responseTimeEl) responseTimeEl.textContent = `${(metrics.response_time || 0).toFixed(2)}s`;
    if (executionTimeEl) executionTimeEl.textContent = `${(report.execution_time || 0).toFixed(2)}s`;

    // Special instructions handling
    const specialInstructionsCard = document.getElementById('specialInstructionsCard');
    const specialInstructionsEl = document.getElementById('specialInstructions');
    
    if (specialInstructionsCard && specialInstructionsEl) {
        if (report.special_instructions) {
            specialInstructionsEl.textContent = report.special_instructions;
            specialInstructionsCard.style.display = 'block';
        } else {
            specialInstructionsCard.style.display = 'none';
        }
    }
    
    // FAQ evaluation handling
    const faqEvaluationCard = document.getElementById('faqEvaluationCard');
    const faqQuestionEl = document.getElementById('faqQuestion');
    const expectedAnswerEl = document.getElementById('expectedAnswer');
    
    if (faqEvaluationCard && faqQuestionEl && expectedAnswerEl) {
        const config = report.config || (report.test_case ? report.test_case.config : null) || {};
        const faqQuestion = config.faq_question || null;
        const expectedAnswer = config.expected_answer || null;
        
        if (faqQuestion && expectedAnswer) {
            faqQuestionEl.textContent = faqQuestion;
            expectedAnswerEl.textContent = expectedAnswer;
            faqEvaluationCard.style.display = 'block';
        } else {
            faqEvaluationCard.style.display = 'none';
        }
    }

    // Full recording handling
    const fullRecordingCard = document.getElementById('fullRecordingCard');
    if (fullRecordingCard) {
        if (report.full_recording_url) {
            fullRecordingCard.style.display = 'block';
            (async () => {
                try {
                    const audioEl = document.getElementById('fullRecording');
                    const loadingEl = document.getElementById('fullRecordingLoading');
                    
                    if (audioEl && loadingEl) {
                        const presignedUrl = await fetchPresignedUrl(report.full_recording_url);
                        if (presignedUrl) {
                            audioEl.src = presignedUrl;
                            loadingEl.style.display = 'none';
                        } else {
                            loadingEl.innerHTML = '<div class="audio-error">Audio unavailable</div>';
                        }
                    }
                } catch (error) {
                    console.error('Error loading full recording:', error);
                    const loadingEl = document.getElementById('fullRecordingLoading');
                    if (loadingEl) {
                        loadingEl.innerHTML = '<div class="audio-error">Error loading audio</div>';
                    }
                }
            })();
        } else {
            fullRecordingCard.style.display = 'none';
        }
    }

    // Display the conversation
    const questionsContainer = document.getElementById('questionsContainer');
    if (questionsContainer) {
        displayConversation(report);
    }
}
// Display the conversation from the report
function displayConversation(report) {
    const questionsContainer = document.getElementById('questionsContainer');
    if (!questionsContainer) {
      console.error('Error: questionsContainer element not found in the DOM');
      return;
    }
    questionsContainer.innerHTML = '';
    
    // Create conversation section using the question template
    // Get template
    const questionTemplateEl = document.getElementById('questionTemplate');
    if (!questionTemplateEl) {
        console.error('Error: questionTemplate element not found in the DOM');
        questionsContainer.innerHTML = '<div class="alert alert-danger">Template error: Could not load question template</div>';
        return;
    }
  
    // Create conversation section using the question template
    const questionTemplate = questionTemplateEl.content.cloneNode(true);
    
    // Set the question text
    questionTemplate.querySelector('.question-text').textContent = report.question || "Question";
    
    // Set the metrics
    const metrics = report.metrics || {};
    const accuracyPercent = Math.round((metrics.accuracy || 0) * 100);
    const empathyPercent = Math.round((metrics.empathy || 0) * 100);
    const responseTime = metrics.response_time || 0;
    
    questionTemplate.querySelector('.accuracy-value').textContent = `${accuracyPercent}%`;
    questionTemplate.querySelector('.empathy-value').textContent = `${empathyPercent}%`;
    questionTemplate.querySelector('.response-time').textContent = `${responseTime.toFixed(2)}s`;
    questionTemplate.querySelector('.meter-marker').style.left = `${accuracyPercent}%`;
    questionTemplate.querySelectorAll('.meter-marker')[1].style.left = `${empathyPercent}%`;
    
    // Check if we have conversation data
    const conversationContainer = questionTemplate.querySelector('.conversation-container');
    if (!conversationContainer) {
        console.error('Error: conversation-container element not found in the template');
        questionsContainer.innerHTML = '<div class="alert alert-danger">Template error: Missing conversation container</div>';
        return;
    }
    
    if (report.conversation && report.conversation.length > 0) {
        // Populate conversation turns
        populateConversation(report.conversation, conversationContainer);
    } else {
        // Show empty state
        conversationContainer.innerHTML = '<div class="alert alert-warning">No conversation data available</div>';
    }
        
    // Add to the page
    questionsContainer.appendChild(questionTemplate);
  }
  
// Populate conversation turns
function populateConversation(conversation, container) {
    container.innerHTML = '';
    
    if (!conversation || conversation.length === 0) {
    container.innerHTML = '<div class="alert alert-warning">No conversation data available</div>';
    return;
    }
    
    conversation.forEach((turn, index) => {
    const turnTemplate = document.getElementById('turnTemplate').content.cloneNode(true);
    const turnElement = turnTemplate.querySelector('.conversation-turn');
    turnElement.querySelector('.speaker-label').textContent = capitalizeFirstLetter(turn.speaker);
    turnElement.querySelector('.turn-text').textContent = turn.text;
    turnElement.classList.add(`turn-${turn.speaker}`);

    const audioPlayer = turnElement.querySelector('.audio-player');
    if (turn.audio_url) {
        // Mark the audio element with its S3 URL for later processing
        audioPlayer.setAttribute('data-s3-url', turn.audio_url);
        const loadingIndicator = document.createElement('div');
        loadingIndicator.className = 'audio-loading';
        loadingIndicator.innerHTML = '<div class="spinner"></div><span>Loading audio...</span>';
        turnElement.appendChild(loadingIndicator);

        (async () => {
        try {
            let audioUrl = turn.audio_url;
            if (audioUrl.startsWith('s3://')) {
            console.log(`Getting presigned URL for: ${audioUrl}`);
            const presignedUrl = await fetchPresignedUrl(audioUrl);
            console.log(`Got presigned URL: ${presignedUrl ? presignedUrl.substring(0, 100) + '...' : 'null'}`);
            if (presignedUrl) {
                audioPlayer.src = presignedUrl;
                audioPlayer.style.display = 'block';
                audioPlayer.addEventListener('error', (e) => {
                console.error('Audio player error:', e);
                loadingIndicator.innerHTML = `<div class="audio-error">Error: ${audioPlayer.error?.message || 'Could not play audio'}</div>`;
                });
                audioPlayer.addEventListener('loadeddata', () => {
                console.log('Audio loaded successfully');
                loadingIndicator.remove();
                });
            } else {
                audioPlayer.style.display = 'none';
                loadingIndicator.innerHTML = '<div class="audio-error">Could not retrieve audio URL</div>';
            }
            } else {
            // For non-S3 URLs, load directly
            audioPlayer.src = audioUrl;
            audioPlayer.style.display = 'block';
            audioPlayer.addEventListener('loadeddata', () => {
                loadingIndicator.remove();
            });
            audioPlayer.addEventListener('error', () => {
                loadingIndicator.innerHTML = '<div class="audio-error">Could not load audio</div>';
            });
            }
        } catch (error) {
            console.error('Error loading audio:', error);
            loadingIndicator.innerHTML = `<div class="audio-error">Error: ${error.message}</div>`;
            audioPlayer.style.display = 'none';
        }
        })();
    } else {
        audioPlayer.style.display = 'none';
    }
    
    container.appendChild(turnElement);
    });
}
  
  // Consolidated fetchPresignedUrl function
  async function fetchPresignedUrl(s3Url) {
    try {
      const encodedUrl = encodeURIComponent(s3Url);
      const response = await fetch(`/api/reports/presigned-audio-url?s3_url=${encodedUrl}`);
      if (!response.ok) {
        throw new Error(`Failed to get presigned URL: ${response.statusText}`);
      }
      const data = await response.json();
      return data.url;
    } catch (error) {
      console.error('Error fetching presigned URL:', error);
      return null;
    }
  }
  
  // Helper to capitalize the first letter of a string
  function capitalizeFirstLetter(string) {
    if (!string) return '';
    return string.charAt(0).toUpperCase() + string.slice(1);
  }
  
  // Load audio for all conversation turns marked with data-s3-url
  async function loadAudioForConversation() {
    const audioElements = document.querySelectorAll('audio[data-s3-url]');
    if (audioElements.length === 0) {
      console.log('No S3 audio URLs found in report');
      return;
    }
    console.log(`Found ${audioElements.length} audio elements with S3 URLs`);
    for (const audioEl of audioElements) {
      const s3Url = audioEl.getAttribute('data-s3-url');
      if (!s3Url) continue;
      try {
        const presignedUrl = await fetchPresignedUrl(s3Url);
        if (presignedUrl) {
          audioEl.src = presignedUrl;
          audioEl.style.display = 'block';
        }
      } catch (error) {
        console.error(`Error loading audio: ${error.message}`);
      }
    }
  }
  
// Populate each evaluated question and its conversation
function populateQuestions(questions) {
    const questionsContainer = document.getElementById('questionsContainer');
    questionsContainer.innerHTML = '';
    
    // Check if we're using the new format (direct conversation on report) or old format (questions_evaluated array)
    if (window.currentReport && window.currentReport.conversation) {
      const questionTemplate = document.getElementById('questionTemplate').content.cloneNode(true);
      
      // Get the main question
      const questionText = window.currentReport.question || "Question";
      questionTemplate.querySelector('.question-text').textContent = questionText;
      
      // Get metrics
      const metrics = window.currentReport.metrics || {};
      const accuracyPercent = Math.round((metrics.accuracy || 0) * 100);
      const empathyPercent = Math.round((metrics.empathy || 0) * 100);
      const responseTime = metrics.response_time || 0;
      
      questionTemplate.querySelector('.accuracy-value').textContent = `${accuracyPercent}%`;
      questionTemplate.querySelector('.empathy-value').textContent = `${empathyPercent}%`;
      questionTemplate.querySelector('.response-time').textContent = `${responseTime.toFixed(2)}s`;
      questionTemplate.querySelector('.meter-marker').style.left = `${accuracyPercent}%`;
      questionTemplate.querySelectorAll('.meter-marker')[1].style.left = `${empathyPercent}%`;
      
      // Populate conversation
      populateConversation(window.currentReport.conversation, questionTemplate.querySelector('.conversation-container'));
      questionsContainer.appendChild(questionTemplate);
    }
}
// Consolidated fetchPresignedUrl function (removes duplicate definitions)
async function fetchPresignedUrl(s3Url) {
  try {
      const encodedUrl = encodeURIComponent(s3Url);
      const response = await fetch(`/api/reports/presigned-audio-url?s3_url=${encodedUrl}`);
      if (!response.ok) {
          throw new Error(`Failed to get presigned URL: ${response.statusText}`);
      }
      const data = await response.json();
      return data.url;
  } catch (error) {
      console.error('Error fetching presigned URL:', error);
      return null;
  }
}

// Populate conversation turns in each question
function populateConversation(conversation, container) {
  container.innerHTML = '';
  console.log("Populating conversation:", conversation);
  if (!conversation || conversation.length === 0) {
      container.innerHTML = '<div class="alert alert-warning">No conversation data available</div>';
      return;
  }
  conversation.forEach((turn, index) => {
      console.log(`Turn ${index}:`, turn);
      const turnTemplate = document.getElementById('turnTemplate').content.cloneNode(true);
      const turnElement = turnTemplate.querySelector('.conversation-turn');
      turnElement.querySelector('.speaker-label').textContent = capitalizeFirstLetter(turn.speaker);
      turnElement.querySelector('.turn-text').textContent = turn.text;
      turnElement.classList.add(`turn-${turn.speaker}`);

      const audioPlayer = turnElement.querySelector('.audio-player');
      if (turn.audio_url) {
          // Mark the audio element with its S3 URL for later processing.
          audioPlayer.setAttribute('data-s3-url', turn.audio_url);
          const loadingIndicator = document.createElement('div');
          loadingIndicator.className = 'audio-loading';
          loadingIndicator.innerHTML = '<div class="spinner"></div><span>Loading audio...</span>';
          turnElement.appendChild(loadingIndicator);

          (async () => {
              try {
                  let audioUrl = turn.audio_url;
                  if (audioUrl.startsWith('s3://')) {
                      console.log(`Getting presigned URL for: ${audioUrl}`);
                      const presignedUrl = await fetchPresignedUrl(audioUrl);
                      console.log(`Got presigned URL: ${presignedUrl ? presignedUrl.substring(0, 100) + '...' : 'null'}`);
                      if (presignedUrl) {
                          audioPlayer.src = presignedUrl;
                          audioPlayer.style.display = 'block';
                          audioPlayer.addEventListener('error', (e) => {
                              console.error('Audio player error:', e);
                              loadingIndicator.innerHTML = `<div class="audio-error">Error: ${audioPlayer.error?.message || 'Could not play audio'}</div>`;
                          });
                          audioPlayer.addEventListener('loadeddata', () => {
                              console.log('Audio loaded successfully');
                              loadingIndicator.remove();
                          });
                      } else {
                          audioPlayer.style.display = 'none';
                          loadingIndicator.innerHTML = '<div class="audio-error">Could not retrieve audio URL</div>';
                      }
                  } else {
                      // For non-S3 URLs, load directly.
                      audioPlayer.src = audioUrl;
                      audioPlayer.style.display = 'block';
                      audioPlayer.addEventListener('loadeddata', () => {
                          loadingIndicator.remove();
                      });
                      audioPlayer.addEventListener('error', () => {
                          loadingIndicator.innerHTML = '<div class="audio-error">Could not load audio</div>';
                      });
                  }
              } catch (error) {
                  console.error('Error loading audio:', error);
                  loadingIndicator.innerHTML = `<div class="audio-error">Error: ${error.message}</div>`;
                  audioPlayer.style.display = 'none';
              }
          })();
      } else {
          console.log(`Turn ${index} has no audio URL`);
          audioPlayer.style.display = 'none';
      }
      container.appendChild(turnElement);
  });
}

// Helper to capitalize the first letter of a string
function capitalizeFirstLetter(string) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

// Load audio for all conversation turns marked with data-s3-url
async function loadAudioForConversation() {
  const audioElements = document.querySelectorAll('audio[data-s3-url]');
  if (audioElements.length === 0) {
      console.log('No S3 audio URLs found in report');
      return;
  }
  console.log(`Found ${audioElements.length} audio elements with S3 URLs`);
  for (const audioEl of audioElements) {
      const s3Url = audioEl.getAttribute('data-s3-url');
      if (!s3Url) continue;
      try {
          const presignedUrl = await fetchPresignedUrl(s3Url);
          if (presignedUrl) {
              audioEl.src = presignedUrl;
              audioEl.style.display = 'block';
          }
      } catch (error) {
          console.error(`Error loading audio: ${error.message}`);
      }
  }
}

// Fixed exportReportWithAudio function
async function exportReportWithAudio() {
    try {
    // Find the export button dropdown - this approach is more flexible
    const exportBtn = document.querySelector('.btn-group .dropdown-toggle');
    let originalBtnText = 'Export Report';
    
    // Store original text and update with spinner only if we found the button
    if (exportBtn) {
        originalBtnText = exportBtn.innerHTML;
        exportBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Preparing export...';
        exportBtn.disabled = true;
    }
    
    // Get the report ID
    const reportId = getReportIdFromUrl();
    if (!reportId) {
        throw new Error('Could not determine report ID');
    }
    
    // Get all audio URLs from the conversation
    const audioElements = document.querySelectorAll('audio[data-s3-url]');
    if (audioElements.length === 0) {
        showToast('No audio files found in this report', 'warning');
        // Restore button state if we found the button
        if (exportBtn) {
        exportBtn.innerHTML = originalBtnText;
        exportBtn.disabled = false;
        }
        return;
    }
    
    // Show progress message
    showToast(`Preparing export with ${audioElements.length} audio files...`, 'info');
    
    // Build a request object with report ID and S3 URLs
    const exportRequest = {
        reportId: reportId,
        audioUrls: Array.from(audioElements).map(el => el.getAttribute('data-s3-url')).filter(Boolean)
    };
    
    // Request the export from server
    const response = await fetch('/api/reports/export-with-audio', {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(exportRequest)
    });
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'Failed to create export');
    }
    
    const data = await response.json();
    
    if (data.exportUrl) {
        // Success - provide download link
        showToast('Export completed successfully!', 'success');
        
        // Open the download in a new tab or directly download
        window.open(data.exportUrl, '_blank');
    } else {
        throw new Error('No export URL returned');
    }
    } catch (error) {
    console.error('Error exporting report with audio:', error);
    showToast(`Export failed: ${error.message}`, 'error');
    } finally {
    // Restore button state - using the more flexible approach
    const exportBtn = document.querySelector('.btn-group .dropdown-toggle');
    if (exportBtn) {
        exportBtn.innerHTML = '<i class="bi bi-download"></i> Export Report';
        exportBtn.disabled = false;
    }
    }
}
  
  // Add toast notification for user feedback
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
    toastEl.className = `toast align-items-center text-white ${type === 'error' ? 'bg-danger' : type === 'success' ? 'bg-success' : type === 'warning' ? 'bg-warning' : 'bg-primary'}`;
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
      delay: 5000
    });
    toast.show();
  }
  
// Add dropdown menu for export options
function updateExportButton() {
    const exportButton = document.getElementById('exportReportBtn');
    if (!exportButton) return;
    
    // Replace the single button with a dropdown
    const exportDropdown = document.createElement('div');
    exportDropdown.className = 'btn-group';
    exportDropdown.innerHTML = `
    <button type="button" class="btn btn-primary dropdown-toggle" data-bs-toggle="dropdown" aria-expanded="false">
        <i class="bi bi-download"></i> Export Report
    </button>
    <ul class="dropdown-menu dropdown-menu-end">
        <li><a class="dropdown-item" href="#" id="exportHtmlBtn">Export as HTML</a></li>
        <li><a class="dropdown-item" href="#" id="exportWithAudioBtn">Export with Audio</a></li>
    </ul>
    `;
    
    exportButton.parentNode.replaceChild(exportDropdown, exportButton);
    
    // Add event listeners to the new buttons
    document.getElementById('exportHtmlBtn').addEventListener('click', function(e) {
    e.preventDefault();
    const reportId = getReportIdFromUrl();
    window.open(`/api/reports/${reportId}/html`, '_blank');
    });
    
    document.getElementById('exportWithAudioBtn').addEventListener('click', function(e) {
    e.preventDefault();
    exportReportWithAudio();
    });
}

