// static/js/report.js

// Initialize the report page
document.addEventListener('DOMContentLoaded', function() {
    // Get report ID from URL
    const reportId = getReportIdFromUrl();
    
    if (reportId) {
        // Fetch report data
        fetchReportData(reportId);
        
        // Add event listener for export button
        document.getElementById('exportReportBtn').addEventListener('click', function() {
            window.open(`/api/reports/${reportId}/html`, '_blank');
        });
    } else {
        showError('Invalid report ID');
    }
});

// Extract report ID from URL
function getReportIdFromUrl() {
    const pathParts = window.location.pathname.split('/');
    return pathParts[pathParts.indexOf('reports') + 1];
}

// Fetch report data from API
async function fetchReportData(reportId) {
    try {
        document.getElementById('loadingReport').style.display = 'flex';
        document.getElementById('reportContent').style.display = 'none';
        
        console.log(`Fetching report data for ID: ${reportId}`);

        // Fetch report data from API
        const response = await fetch(`/api/reports/${reportId}`);
        
        if (!response.ok) {
          console.error(`Error response: ${response.status} ${response.statusText}`);
          const errorText = await response.text();
          console.error("Error response body:", errorText);
          throw new Error(`Error fetching report: ${response.statusText}`);
        }
        
        const report = await response.json();
        
        console.log('Report data received:', report);

        // Check if we have questions_evaluated
        if (!report.questions_evaluated || report.questions_evaluated.length === 0) {
          console.warn("No questions_evaluated in report");
          
          // Create a default question if needed
          if (!report.questions_evaluated) {
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
        }
        // Populate report details
        populateReportDetails(report);
        
        document.getElementById('loadingReport').style.display = 'none';
        document.getElementById('reportContent').style.display = 'block';
    } catch (error) {
        console.error('Error fetching report data:', error);
        document.getElementById('loadingReport').style.display = 'none';
        showError(error.message);
    }
}

// Show error message
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

// Populate report details
// Check for full recording in populateReportDetails function
function populateReportDetails(report) {
  // ... (existing code)
  
  // Set special instructions if any
  if (report.special_instructions) {
    document.getElementById('specialInstructions').textContent = report.special_instructions;
    document.getElementById('specialInstructionsCard').style.display = 'block';
  } else {
    document.getElementById('specialInstructionsCard').style.display = 'none';
  }
  
  // Handle full recording if available
  if (report.full_recording_url) {
    document.getElementById('fullRecordingCard').style.display = 'block';
    
    // Process the S3 URL to get a presigned URL
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
  
  // Populate questions
  populateQuestions(report.questions_evaluated);
}

// Populate questions
function populateQuestions(questions) {
    const questionsContainer = document.getElementById('questionsContainer');
    questionsContainer.innerHTML = '';
    
    questions.forEach((questionEval, index) => {
        const questionTemplate = document.getElementById('questionTemplate').content.cloneNode(true);
        
        // Set question text
        questionTemplate.querySelector('.question-text').textContent = `Q${index + 1}: ${questionEval.question}`;
        
        // Set metrics
        const qMetrics = questionEval.metrics;
        const accuracyPercent = Math.round(qMetrics.accuracy * 100);
        const empathyPercent = Math.round(qMetrics.empathy * 100);
        
        questionTemplate.querySelector('.accuracy-value').textContent = `${accuracyPercent}%`;
        questionTemplate.querySelector('.empathy-value').textContent = `${empathyPercent}%`;
        questionTemplate.querySelector('.response-time').textContent = `${qMetrics.response_time.toFixed(2)}s`;
        
        // Set meter markers
        questionTemplate.querySelector('.meter-marker').style.left = `${accuracyPercent}%`;
        questionTemplate.querySelectorAll('.meter-marker')[1].style.left = `${empathyPercent}%`;
        
        // Populate conversation turns
        populateConversation(questionEval.conversation, questionTemplate.querySelector('.conversation-container'));
        
        questionsContainer.appendChild(questionTemplate);
    });
}

async function fetchPresignedUrl(s3Url) {
  try {
    // Encode the S3 URL properly
    const encodedUrl = encodeURIComponent(s3Url);
    
    // Request presigned URL from server
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

// Fetch a presigned URL for S3 objects
async function fetchPresignedUrl(s3Url, audioPlayer) {
    try {
        // Extract bucket and key from S3 URL
        const urlParts = s3Url.replace('s3://', '').split('/');
        const bucket = urlParts.shift();
        const key = urlParts.join('/');
        
        // Request presigned URL from server
        const response = await fetch(`/api/s3-presigned-url?bucket=${bucket}&key=${key}`);
        
        if (response.ok) {
            const data = await response.json();
            audioPlayer.src = data.url;
        } else {
            console.error('Failed to get presigned URL');
            audioPlayer.style.display = 'none';
        }
    } catch (error) {
        console.error('Error fetching presigned URL:', error);
        audioPlayer.style.display = 'none';
    }
}

// Helper function to capitalize first letter
function capitalizeFirstLetter(string) {
    return string.charAt(0).toUpperCase() + string.slice(1);
}

async function loadAudioForConversation() {
    // Find all audio elements with S3 URLs
    const audioElements = document.querySelectorAll('audio[data-s3-url]');
    
    if (audioElements.length === 0) {
      console.log('No S3 audio URLs found in report');
      return;
    }
    
    console.log(`Found ${audioElements.length} audio elements with S3 URLs`);
    
    // Process each audio element
    for (const audioEl of audioElements) {
      const s3Url = audioEl.getAttribute('data-s3-url');
      if (!s3Url) continue;
      
      try {
        // Show loading indicator
        const loadingIndicator = document.createElement('div');
        loadingIndicator.className = 'audio-loading';
        loadingIndicator.innerHTML = '<div class="spinner"></div><span>Loading audio...</span>';
        audioEl.parentNode.insertBefore(loadingIndicator, audioEl.nextSibling);
        
        // Fetch presigned URL
        const response = await fetch(`/api/reports/presigned-audio-url?s3_url=${encodeURIComponent(s3Url)}`);
        
        if (!response.ok) {
          throw new Error(`Failed to get presigned URL: ${response.statusText}`);
        }
        
        const data = await response.json();
        
        // Set the audio source and show the player
        audioEl.src = data.url;
        audioEl.style.display = 'block';
        
        // Remove loading indicator
        loadingIndicator.remove();
        
        // Add waveform visualization
        const containerId = `waveform-${Math.random().toString(36).substring(2, 9)}`;
        const waveformContainer = document.createElement('div');
        waveformContainer.id = containerId;
        waveformContainer.className = 'waveform-container';
        audioEl.parentNode.insertBefore(waveformContainer, audioEl.nextSibling);
        
        // Initialize waveform (if WaveSurfer is available)
        if (window.WaveSurfer) {
          const wavesurfer = WaveSurfer.create({
            container: `#${containerId}`,
            waveColor: '#4f93d1',
            progressColor: '#2980b9',
            height: 80,
            responsive: true,
            barWidth: 3,
            barGap: 1,
            cursorWidth: 1,
            normalize: true
          });
          
          wavesurfer.load(data.url);
          
          // Connect audio element to wavesurfer
          wavesurfer.on('ready', () => {
            // Hide the original audio player
            audioEl.style.display = 'none';
            
            // Add play/pause button
            const playButton = document.createElement('button');
            playButton.className = 'waveform-play-button';
            playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
            waveformContainer.parentNode.insertBefore(playButton, waveformContainer);
            
            playButton.addEventListener('click', () => {
              wavesurfer.playPause();
              if (wavesurfer.isPlaying()) {
                playButton.innerHTML = '<i class="bi bi-pause-fill"></i>';
              } else {
                playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
              }
            });
            
            // Reset button when playback ends
            wavesurfer.on('finish', () => {
              playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
            });
          });
        }
        
      } catch (error) {
        console.error(`Error loading audio: ${error.message}`);
        const errorMsg = document.createElement('div');
        errorMsg.className = 'audio-error';
        errorMsg.textContent = 'Error loading audio';
        audioEl.parentNode.insertBefore(errorMsg, audioEl.nextSibling);
      }
    }
  }
  
  // Call this function when the report page loads
  document.addEventListener('DOMContentLoaded', function() {
    // Get report ID from URL
    const reportId = getReportIdFromUrl();
    
    if (reportId) {
      // Fetch report data
      fetchReportData(reportId).then(() => {
        // After report data is loaded, load audio
        loadAudioForConversation();
      });
    }
  });
  
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
      
      // Set speaker and text
      turnElement.querySelector('.speaker-label').textContent = capitalizeFirstLetter(turn.speaker);
      turnElement.querySelector('.turn-text').textContent = turn.text;
      
      // Add appropriate class based on speaker
      turnElement.classList.add(`turn-${turn.speaker}`);
      
      // Set audio if available
      const audioPlayer = turnElement.querySelector('.audio-player');
      if (turn.audio_url) {
        // Add more visible loading indicator with retry option
        const loadingIndicator = document.createElement('div');
        loadingIndicator.className = 'audio-loading';
        loadingIndicator.innerHTML = '<div class="spinner"></div><span>Loading audio...</span>';
        turnElement.appendChild(loadingIndicator);
        
        // Retry logic for audio loading
        (async () => {
          try {
            let audioUrl = turn.audio_url;
            
            // Convert S3 URL to presigned URL if needed
            if (audioUrl.startsWith('s3://')) {
              console.log(`Getting presigned URL for: ${audioUrl}`);
              const presignedUrl = await fetchPresignedUrl(audioUrl);
              console.log(`Got presigned URL: ${presignedUrl ? presignedUrl.substring(0, 100) + '...' : 'null'}`);
              
              if (presignedUrl) {
                audioPlayer.src = presignedUrl;
                audioPlayer.style.display = 'block';
                
                // Add event listeners for debugging
                audioPlayer.addEventListener('error', (e) => {
                  console.error('Audio player error:', e);
                  console.error('Error code:', audioPlayer.error?.code);
                  console.error('Error message:', audioPlayer.error?.message);
                  
                  // Show error message in UI
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
              // Direct URL
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

  async function loadConversationAudio() {
    const audioElements = document.querySelectorAll('audio[data-s3-url]');
    
    if (audioElements.length === 0) {
      console.log('No S3 audio URLs found in report');
      return;
    }
    
    console.log(`Found ${audioElements.length} audio elements with S3 URLs`);
    
    // Process each audio element
    for (const audioEl of audioElements) {
      const s3Url = audioEl.getAttribute('data-s3-url');
      if (!s3Url) continue;
      
      try {
        // Use the presigned URL function
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
  
  // Call this after populating the report
  document.addEventListener('DOMContentLoaded', function() {
    // Get report ID from URL
    const reportId = getReportIdFromUrl();
    
    if (reportId) {
      // Fetch report data
      fetchReportData(reportId).then(() => {
        // After report is loaded, ensure audio is loaded too
        setTimeout(loadConversationAudio, 500);
      });
    }
  });