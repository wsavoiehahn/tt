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
        
        // Fetch report data from API
        const response = await fetch(`/api/reports/${reportId}`);
        
        if (!response.ok) {
            throw new Error(`Error fetching report: ${response.statusText}`);
        }
        
        const report = await response.json();
        
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
function populateReportDetails(report) {
    // Set title and metadata
    document.getElementById('reportTitle').textContent = report.test_case_name;
    document.getElementById('personaBadge').textContent = report.persona_name;
    document.getElementById('behaviorBadge').textContent = report.behavior_name;
    
    // Set overall metrics
    const metrics = report.overall_metrics;
    document.getElementById('overallAccuracy').textContent = `${Math.round(metrics.accuracy * 100)}%`;
    document.getElementById('overallEmpathy').textContent = `${Math.round(metrics.empathy * 100)}%`;
    document.getElementById('avgResponseTime').textContent = `${metrics.response_time.toFixed(2)}s`;
    document.getElementById('executionTime').textContent = `${report.execution_time.toFixed(2)}s`;
    
    // Set special instructions if any
    if (report.special_instructions) {
        document.getElementById('specialInstructions').textContent = report.special_instructions;
        document.getElementById('specialInstructionsCard').style.display = 'block';
    } else {
        document.getElementById('specialInstructionsCard').style.display = 'none';
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

// Populate conversation turns
function populateConversation(conversation, container) {
    container.innerHTML = '';
    
    conversation.forEach((turn, index) => {
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
            // Convert S3 URL to public URL if needed
            let audioUrl = turn.audio_url;
            if (audioUrl.startsWith('s3://')) {
                // Request a presigned URL from the server
                fetchPresignedUrl(audioUrl, audioPlayer);
            } else {
                audioPlayer.src = audioUrl;
            }
        } else {
            audioPlayer.style.display = 'none';
        }
        
        container.appendChild(turnElement);
    });
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
  
  // Modify your populateConversation function to include S3 URL data attributes
  function populateConversation(conversation, container) {
    container.innerHTML = '';
    
    conversation.forEach((turn, index) => {
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
        // Store the S3 URL as a data attribute
        audioPlayer.setAttribute('data-s3-url', turn.audio_url);
        
        // Don't set src yet - we'll fetch presigned URLs later
        audioPlayer.style.display = 'none'; // Hide until loaded
      } else {
        audioPlayer.style.display = 'none';
      }
      
      container.appendChild(turnElement);
    });
  }
