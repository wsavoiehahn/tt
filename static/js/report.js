// static/js/report.js

// Consolidated DOMContentLoaded handler
document.addEventListener('DOMContentLoaded', async function() {
  const reportId = getReportIdFromUrl();
  if (!reportId) {
      showError('Invalid report ID');
      return;
  }

  try {
      await fetchReportData(reportId);
      // Once report data is loaded, load audio for all conversation turns.
      loadAudioForConversation();
  } catch (error) {
      showError(error.message);
  }

  // Attach export button listener
  document.getElementById('exportReportBtn').addEventListener('click', function() {
      window.open(`/api/reports/${reportId}/html`, '_blank');
  });
});

// Extract report ID from URL
function getReportIdFromUrl() {
  const pathParts = window.location.pathname.split('/');
  return pathParts[pathParts.indexOf('reports') + 1];
}

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

  // If agent transcript is missing, check your backend data merging logic.
  if (!report.questions_evaluated || report.questions_evaluated.length === 0) {
      console.warn("No questions_evaluated in report; check if agent data is being saved.");
      // Create a default question using debug_conversation if available.
      report.questions_evaluated = [{
          question: "Default Question",
          conversation: report.debug_conversation || [],
          metrics: report.overall_metrics || {
              accuracy: 0,
              empathy: 0,
              response_time: 0,
              successful: false
          }
      }];
      console.log("Added default question with conversation:", report.questions_evaluated[0]);
  }

  populateReportDetails(report);
  document.getElementById('loadingReport').style.display = 'none';
  document.getElementById('reportContent').style.display = 'block';
}

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
    document.getElementById('reportTitle').textContent = `Test Case Report: ${report.test_case_name || 'Unknown Test'}`;
    
    // Set persona and behavior badges
    const personaBadge = document.getElementById('personaBadge');
    const behaviorBadge = document.getElementById('behaviorBadge');
    personaBadge.textContent = `Persona: ${report.persona_name || 'Unknown'}`;
    behaviorBadge.textContent = `Behavior: ${report.behavior_name || 'Unknown'}`;
    
    // Extract and display the test question, if available
    let mainQuestion = "No question specified";
    if (report.questions_evaluated && report.questions_evaluated.length > 0) {
        mainQuestion = report.questions_evaluated[0].question || "No question specified";
    } else if (report.test_case && report.test_case.config && report.test_case.config.questions) {
        // Try to get from test case config
        const questions = report.test_case.config.questions;
        if (questions.length > 0) {
            if (typeof questions[0] === 'string') {
                mainQuestion = questions[0];
            } else if (typeof questions[0] === 'object' && questions[0].text) {
                mainQuestion = questions[0].text;
            }
        }
    }
    
    // Create or update question display element
    let questionElement = document.getElementById('mainQuestion');
    if (!questionElement) {
        // Create the element if it doesn't exist
        questionElement = document.createElement('div');
        questionElement.id = 'mainQuestion';
        questionElement.className = 'main-question mt-2';
        
        // Insert after report metadata
        const reportMetadata = document.getElementById('reportMetadata');
        reportMetadata.parentNode.insertBefore(questionElement, reportMetadata.nextSibling);
    }
    // Set overall metrics
    const metrics = report.overall_metrics || {};
    document.getElementById('overallAccuracy').textContent = `${Math.round((metrics.accuracy || 0) * 100)}%`;
    document.getElementById('overallEmpathy').textContent = `${Math.round((metrics.empathy || 0) * 100)}%`;
    document.getElementById('avgResponseTime').textContent = `${(metrics.response_time || 0).toFixed(2)}s`;
    document.getElementById('executionTime').textContent = `${(report.execution_time || 0).toFixed(2)}s`;

    // Special instructions handling
    if (report.special_instructions) {
        document.getElementById('specialInstructions').textContent = report.special_instructions;
        document.getElementById('specialInstructionsCard').style.display = 'block';
    } else {
        document.getElementById('specialInstructionsCard').style.display = 'none';
    }

    // Full recording handling
    if (report.full_recording_url) {
        document.getElementById('fullRecordingCard').style.display = 'block';
        (async () => {
            try {
                const presignedUrl = await fetchPresignedUrl(report.full_recording_url);
                if (presignedUrl) {
                    const audioEl = document.getElementById('fullRecording');
                    audioEl.src = presignedUrl;
                    document.getElementById('fullRecordingLoading').style.display = 'none';
                } else {
                    document.getElementById('fullRecordingLoading').innerHTML = 
                        '<div class="audio-error">Audio unavailable</div>';
                }
            } catch (error) {
                console.error('Error loading full recording:', error);
                document.getElementById('fullRecordingLoading').innerHTML = 
                    '<div class="audio-error">Error loading audio</div>';
            }
        })();
    } else {
        document.getElementById('fullRecordingCard').style.display = 'none';
    }

    // Populate questions and their conversations
    populateQuestions(report.questions_evaluated);
}

// Populate each evaluated question and its conversation
function populateQuestions(questions) {
  const questionsContainer = document.getElementById('questionsContainer');
  questionsContainer.innerHTML = '';
  questions.forEach((questionEval, index) => {
      const questionTemplate = document.getElementById('questionTemplate').content.cloneNode(true);
      questionTemplate.querySelector('.question-text').textContent = `Q${index + 1}: ${questionEval.question}`;
      const qMetrics = questionEval.metrics;
      const accuracyPercent = Math.round(qMetrics.accuracy * 100);
      const empathyPercent = Math.round(qMetrics.empathy * 100);
      questionTemplate.querySelector('.accuracy-value').textContent = `${accuracyPercent}%`;
      questionTemplate.querySelector('.empathy-value').textContent = `${empathyPercent}%`;
      questionTemplate.querySelector('.response-time').textContent = `${qMetrics.response_time.toFixed(2)}s`;
      questionTemplate.querySelector('.meter-marker').style.left = `${accuracyPercent}%`;
      questionTemplate.querySelectorAll('.meter-marker')[1].style.left = `${empathyPercent}%`;
      populateConversation(questionEval.conversation, questionTemplate.querySelector('.conversation-container'));
      questionsContainer.appendChild(questionTemplate);
  });
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
