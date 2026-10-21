/**
 * ClipHunter Frontend Application Controller
 * Handles SPA navigation, video playback with 206 seeking, SSE streaming,
 * candidate boundary fine-tuning (±0.1s), manual moment addition, and clip rendering.
 */

(() => {
  // Application State
  const state = {
    activeScreen: 'screen-new-job',
    currentJob: null,
    currentSourceId: null,
    candidates: [],
    clips: [],
    eventSource: null,
    profiles: [],
    activeCandidateId: null,
    filter: 'all',
  };

  // DOM Elements
  const el = {
    navTabs: document.querySelectorAll('.nav-tab'),
    navProgressTab: document.getElementById('nav-progress-tab'),
    navReviewTab: document.getElementById('nav-review-tab'),
    navResultsTab: document.getElementById('nav-results-tab'),
    progressIndicator: document.getElementById('progress-indicator'),
    reviewCountBadge: document.getElementById('review-count-badge'),
    resultsCountBadge: document.getElementById('results-count-badge'),
    headerCostPill: document.getElementById('header-cost-pill'),
    btnShowUsage: document.getElementById('btn-show-usage'),
    btnRecentJobs: document.getElementById('btn-recent-jobs'),

    // Screen 1: New Job
    tabBtnUrl: document.getElementById('tab-btn-url'),
    tabBtnFile: document.getElementById('tab-btn-file'),
    inputUrlGroup: document.getElementById('input-url-group'),
    inputFileGroup: document.getElementById('input-file-group'),
    inputYoutubeUrl: document.getElementById('input-youtube-url'),
    btnEstimateCost: document.getElementById('btn-estimate-cost'),
    fileDropzone: document.getElementById('file-dropzone'),
    fileInputElement: document.getElementById('file-input-element'),
    uploadProgressBox: document.getElementById('upload-progress-box'),
    uploadFileName: document.getElementById('upload-file-name'),
    uploadPercent: document.getElementById('upload-percent'),
    uploadProgressFill: document.getElementById('upload-progress-fill'),
    estimatePanel: document.getElementById('estimate-panel'),
    estTitle: document.getElementById('est-title'),
    estDuration: document.getElementById('est-duration'),
    estChannel: document.getElementById('est-channel'),
    estCost: document.getElementById('est-cost'),
    selectProfile: document.getElementById('select-profile'),
    profileAddonHint: document.getElementById('profile-addon-hint'),
    inputClipMin: document.getElementById('input-clip-min'),
    inputClipMax: document.getElementById('input-clip-max'),
    inputMaxCost: document.getElementById('input-max-cost'),
    btnStartJob: document.getElementById('btn-start-job'),

    // Screen 2: Progress
    jobStatusBadge: document.getElementById('job-status-badge'),
    progressJobTitle: document.getElementById('progress-job-title'),
    progressJobId: document.getElementById('progress-job-id'),
    progressCostVal: document.getElementById('progress-cost-val'),
    progressCandCount: document.getElementById('progress-cand-count'),
    stepperTimeline: document.getElementById('stepper-timeline'),
    currentStageName: document.getElementById('current-stage-name'),
    currentStageDesc: document.getElementById('current-stage-desc'),
    btnCancelJob: document.getElementById('btn-cancel-job'),
    btnRetryJob: document.getElementById('btn-retry-job'),
    btnGoToReview: document.getElementById('btn-go-to-review'),
    consoleStreamLog: document.getElementById('console-stream-log'),

    // Screen 3: Review
    videoPlayer: document.getElementById('review-video-player'),
    videoSource: document.getElementById('video-source'),
    scrubberBar: document.getElementById('scrubber-bar'),
    scrubberProgress: document.getElementById('scrubber-progress'),
    scrubberMarkersLayer: document.getElementById('scrubber-markers-layer'),
    playerCurrentTime: document.getElementById('player-current-time'),
    playerTotalDuration: document.getElementById('player-total-duration'),
    btnAddMomentModal: document.getElementById('btn-add-moment-modal'),
    acceptedClipsCount: document.getElementById('accepted-clips-count'),
    selectRenderLayout: document.getElementById('select-render-layout'),
    btnTriggerRender: document.getElementById('btn-trigger-render'),
    candidatesTotalBadge: document.getElementById('candidates-total-badge'),
    candidatesScrollList: document.getElementById('candidates-scroll-list'),
    filterChips: document.querySelectorAll('.filter-chips .chip'),

    // Screen 4: Results
    clipsGridContainer: document.getElementById('clips-grid-container'),
    btnViewUsageFromResults: document.getElementById('btn-view-usage-from-results'),
    btnStartAnotherJob: document.getElementById('btn-start-another-job'),

    // Modals
    modalAddMoment: document.getElementById('modal-add-moment'),
    btnCloseMomentModal: document.getElementById('btn-close-moment-modal'),
    btnCancelManualMoment: document.getElementById('btn-cancel-manual-moment'),
    btnSaveManualMoment: document.getElementById('btn-save-manual-moment'),
    manualTitle: document.getElementById('manual-title'),
    manualStart: document.getElementById('manual-start'),
    manualEnd: document.getElementById('manual-end'),
    manualCategory: document.getElementById('manual-category'),
    manualScore: document.getElementById('manual-score'),
    manualReason: document.getElementById('manual-reason'),
    btnSetStartNow: document.getElementById('btn-set-start-now'),
    btnSetEndNow: document.getElementById('btn-set-end-now'),

    modalUsage: document.getElementById('modal-usage'),
    btnCloseUsageModal: document.getElementById('btn-close-usage-modal'),
    usageTotalCost: document.getElementById('usage-total-cost'),
    usageTotalTokens: document.getElementById('usage-total-tokens'),
    usageTotalCalls: document.getElementById('usage-total-calls'),
    usageCacheRate: document.getElementById('usage-cache-rate'),
    usageStagesTbody: document.getElementById('usage-stages-tbody'),
    usageCallsTbody: document.getElementById('usage-calls-tbody'),

    modalRecentJobs: document.getElementById('modal-recent-jobs'),
    btnCloseJobsModal: document.getElementById('btn-close-jobs-modal'),
    recentJobsList: document.getElementById('recent-jobs-list'),

    toastContainer: document.getElementById('toast-container'),
  };

  // Helper Functions
  function formatSeconds(sec) {
    if (isNaN(sec) || sec === null || sec === undefined) return '00:00.0';
    const s = Math.max(0, sec);
    const m = Math.floor(s / 60);
    const rem = (s % 60).toFixed(1);
    const mm = m < 10 ? `0${m}` : `${m}`;
    const ss = rem < 10 ? `0${rem}` : `${rem}`;
    return `${mm}:${ss}`;
  }

  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    el.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      setTimeout(() => toast.remove(), 250);
    }, 4000);
  }

  function appendLog(text, cssClass = '') {
    const line = document.createElement('div');
    line.className = `log-line ${cssClass}`;
    const time = new Date().toLocaleTimeString();
    line.textContent = `[${time}] ${text}`;
    el.consoleStreamLog.appendChild(line);
    el.consoleStreamLog.scrollTop = el.consoleStreamLog.scrollHeight;
  }

  // Navigation Controller
  function switchScreen(screenId) {
    state.activeScreen = screenId;
    document.querySelectorAll('.view-screen').forEach((sc) => {
      sc.classList.toggle('active', sc.id === screenId);
    });
    el.navTabs.forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.screen === screenId);
    });
  }

  // API Requests Wrapper
  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
      ...options,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `Request failed with code ${res.status}`);
    }
    return res.json();
  }

  // Load Profiles
  async function loadProfiles() {
    try {
      const profiles = await api('/api/profiles');
      state.profiles = profiles;
      el.selectProfile.innerHTML = profiles
        .map((p) => `<option value="${p.name}">${p.name} (${p.game || p.language})</option>`)
        .join('');

      el.selectProfile.addEventListener('change', () => {
        const selected = state.profiles.find((p) => p.name === el.selectProfile.value);
        if (selected) {
          el.profileAddonHint.textContent = selected.prompt_addon || 'Без дополнительных указаний.';
          el.inputClipMin.value = selected.clip_min_s || 20;
          el.inputClipMax.value = selected.clip_max_s || 90;
        }
      });
      if (profiles.length > 0) {
        el.profileAddonHint.textContent = profiles[0].prompt_addon || '';
      }
    } catch (e) {
      console.warn('Could not load profiles:', e);
    }
  }

  // File Upload Handler
  function setupUploadHandlers() {
    el.tabBtnUrl.addEventListener('click', () => {
      el.tabBtnUrl.classList.add('active');
      el.tabBtnFile.classList.remove('active');
      el.inputUrlGroup.classList.add('active');
      el.inputFileGroup.classList.remove('active');
    });

    el.tabBtnFile.addEventListener('click', () => {
      el.tabBtnFile.classList.add('active');
      el.tabBtnUrl.classList.remove('active');
      el.inputFileGroup.classList.add('active');
      el.inputUrlGroup.classList.remove('active');
    });

    el.fileDropzone.addEventListener('click', () => el.fileInputElement.click());

    el.fileDropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      el.fileDropzone.classList.add('dragover');
    });
    el.fileDropzone.addEventListener('dragleave', () => {
      el.fileDropzone.classList.remove('dragover');
    });
    el.fileDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      el.fileDropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        uploadFile(e.dataTransfer.files[0]);
      }
    });

    el.fileInputElement.addEventListener('change', () => {
      if (el.fileInputElement.files.length > 0) {
        uploadFile(el.fileInputElement.files[0]);
      }
    });
  }

  function uploadFile(file) {
    el.uploadProgressBox.style.display = 'block';
    el.uploadFileName.textContent = file.name;
    el.uploadPercent.textContent = '0%';
    el.uploadProgressFill.style.width = '0%';

    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/uploads', true);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        el.uploadPercent.textContent = `${percent}%`;
        el.uploadProgressFill.style.width = `${percent}%`;
      }
    };

    xhr.onload = () => {
      if (xhr.status === 200) {
        const data = JSON.parse(xhr.responseText);
        state.currentSourceId = data.source_id;
        showToast('Файл успешно загружен и проверен!', 'success');
        el.uploadPercent.textContent = 'Готово';
        // Auto trigger estimate
        loadEstimate(data.source_id);
      } else {
        showToast(`Ошибка загрузки: ${xhr.statusText}`, 'error');
      }
    };

    xhr.onerror = () => showToast('Ошибка сети при загрузке файла', 'error');
    xhr.send(formData);
  }

  // Cost Estimate Handler
  async function loadEstimate(target) {
    if (!target) return;
    try {
      const data = await api('/api/jobs/estimate', {
        method: 'POST',
        body: JSON.stringify({ target }),
      });
      el.estimatePanel.style.display = 'block';
      el.estTitle.textContent = data.title || 'Видео';
      el.estDuration.textContent = `${data.duration_min} мин (${formatSeconds(data.duration_s)})`;
      el.estChannel.textContent = data.channel || 'Неизвестно';
      el.estCost.textContent = `~$${data.estimated_cost_usd} USD`;
    } catch (e) {
      showToast(`Не удалось оценить стоимость: ${e.message}`, 'error');
    }
  }

  el.btnEstimateCost.addEventListener('click', () => {
    const url = el.inputYoutubeUrl.value.trim();
    if (!url) {
      showToast('Введите ссылку на YouTube', 'error');
      return;
    }
    loadEstimate(url);
  });

  // Start Job Submission
  el.btnStartJob.addEventListener('click', async () => {
    const isUrlMode = el.tabBtnUrl.classList.contains('active');
    const url = el.inputYoutubeUrl.value.trim();
    const sourceId = state.currentSourceId;

    if (isUrlMode && !url) {
      showToast('Пожалуйста, укажите ссылку на YouTube', 'error');
      return;
    }
    if (!isUrlMode && !sourceId) {
      showToast('Пожалуйста, загрузите видеофайл', 'error');
      return;
    }

    const payload = {
      url: isUrlMode ? url : null,
      source_id: !isUrlMode ? sourceId : null,
      profile_id: el.selectProfile.value,
      clip_min_s: parseFloat(el.inputClipMin.value) || 20.0,
      clip_max_s: parseFloat(el.inputClipMax.value) || 90.0,
      max_cost_usd: parseFloat(el.inputMaxCost.value) || 5.0,
    };

    try {
      el.btnStartJob.disabled = true;
      el.btnStartJob.textContent = 'Запуск задачи...';
      const job = await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      showToast('Задача успешно запущена!', 'success');
      startJobMonitoring(job.id);
    } catch (e) {
      showToast(`Ошибка запуска: ${e.message}`, 'error');
    } finally {
      el.btnStartJob.disabled = false;
      el.btnStartJob.innerHTML = '<span class="icon">🚀</span><span>Запустить поиск клипов</span>';
    }
  });

  // Start Monitoring a Job (SSE & State sync)
  async function startJobMonitoring(jobId) {
    const job = await api(`/api/jobs/${jobId}`);
    state.currentJob = job;

    // Enable navigation tabs
    el.navProgressTab.disabled = false;
    el.btnShowUsage.style.display = 'inline-flex';
    updateJobHeaderAndStats(job);

    switchScreen('screen-progress');
    connectSSE(jobId);

    // If already waiting for review or completed, handle transition
    if (job.status === 'awaiting_review') {
      setupReviewScreen(jobId);
    } else if (job.status === 'completed') {
      setupResultsScreen(jobId);
    }
  }

  function updateJobHeaderAndStats(job) {
    el.progressJobTitle.textContent = job.source_title || 'Стрим';
    el.progressJobId.textContent = `ID: ${job.id}`;
    el.jobStatusBadge.textContent = job.status;
    el.jobStatusBadge.className = `badge ${job.status}`;
    el.progressCostVal.textContent = `$${(job.cost_usd || 0).toFixed(3)}`;
    el.headerCostPill.textContent = `$${(job.cost_usd || 0).toFixed(2)}`;
    el.progressCandCount.textContent = job.candidates_count || 0;
  }

  // SSE Real-time Broadcaster Connection
  function connectSSE(jobId) {
    if (state.eventSource) {
      state.eventSource.close();
    }

    appendLog(`Подключение к каналу событий задачи ${jobId}...`);
    state.eventSource = new EventSource(`/api/jobs/${jobId}/events`);

    state.eventSource.addEventListener('stage_start', (e) => {
      const data = JSON.parse(e.data);
      appendLog(`[Этап] ${data.stage}: ${data.message || ''}`, 'log-stage');
      updateStepperStage(data.stage);
    });

    state.eventSource.addEventListener('cost_update', (e) => {
      const data = JSON.parse(e.data);
      el.progressCostVal.textContent = `$${data.cost_usd.toFixed(3)}`;
      el.headerCostPill.textContent = `$${data.cost_usd.toFixed(2)}`;
      appendLog(`[Расход] +$${data.delta_cost_usd} (Всего: $${data.cost_usd.toFixed(3)})`, 'log-cost');
    });

    state.eventSource.addEventListener('candidates_found', (e) => {
      const data = JSON.parse(e.data);
      el.progressCandCount.textContent = data.count || 0;
      appendLog(`[Кандидаты] Найдено ${data.count} моментов`, 'log-cost');
    });

    state.eventSource.addEventListener('awaiting_review', (e) => {
      const data = JSON.parse(e.data);
      appendLog(`[Ревью] Пайплайн завершил поиск! Ожидание проверки нарезчиком.`, 'log-stage');
      el.btnGoToReview.style.display = 'inline-block';
      showToast('Кандидаты найдены! Перейдите к ревью клипов.', 'success');
      setupReviewScreen(jobId);
      switchScreen('screen-review');
    });

    state.eventSource.addEventListener('candidate_patched', (e) => {
      const data = JSON.parse(e.data);
      refreshCandidateInList(data.candidate);
    });

    state.eventSource.addEventListener('candidate_added', (e) => {
      const data = JSON.parse(e.data);
      state.candidates.push(data.candidate);
      renderCandidates();
      renderScrubberMarkers();
      showToast('Новый момент добавлен в список', 'info');
    });

    state.eventSource.addEventListener('completed', (e) => {
      appendLog(`[Завершено] Все клипы успешно смонтированы!`, 'log-stage');
      el.jobStatusBadge.textContent = 'completed';
      el.jobStatusBadge.className = 'badge completed';
      showToast('Монтаж клипов успешно завершён!', 'success');
      setupResultsScreen(jobId);
      switchScreen('screen-results');
    });

    state.eventSource.addEventListener('failed', (e) => {
      const data = JSON.parse(e.data);
      appendLog(`[Ошибка] ${data.error}`, 'log-warn');
      el.jobStatusBadge.textContent = 'failed';
      el.jobStatusBadge.className = 'badge failed';
      el.btnRetryJob.style.display = 'inline-block';
      showToast(`Ошибка задачи: ${data.error}`, 'error');
    });

    state.eventSource.onerror = () => {
      // Reconnection handled automatically by browser
    };
  }

  function updateStepperStage(stage) {
    const stages = [
      's0_ingest',
      's1_download_proxy',
      's2_chunk_plan',
      's3_scan',
      's4_merge',
      's5_refine',
      's6_judge',
      's7_cut',
    ];
    const currentIndex = stages.indexOf(stage);

    const nodes = el.stepperTimeline.querySelectorAll('.step-node');
    const connectors = el.stepperTimeline.querySelectorAll('.step-connector');

    nodes.forEach((n, idx) => {
      n.classList.remove('active', 'done');
      if (idx < currentIndex) {
        n.classList.add('done');
      } else if (idx === currentIndex) {
        n.classList.add('active');
      }
    });

    connectors.forEach((c, idx) => {
      c.classList.toggle('done', idx < currentIndex);
    });

    el.currentStageName.textContent = `Этап ${stage}`;
    el.currentStageDesc.textContent = `Выполняется обработка: ${stage}`;
  }

  // Cancel & Retry controls
  el.btnCancelJob.addEventListener('click', async () => {
    if (!state.currentJob) return;
    try {
      await api(`/api/jobs/${state.currentJob.id}/cancel`, { method: 'POST' });
      showToast('Задача отменена', 'info');
      el.jobStatusBadge.textContent = 'cancelled';
    } catch (e) {
      showToast(`Не удалось отменить: ${e.message}`, 'error');
    }
  });

  el.btnRetryJob.addEventListener('click', async () => {
    if (!state.currentJob) return;
    try {
      await api(`/api/jobs/${state.currentJob.id}/retry-stage`, { method: 'POST' });
      showToast('Задача возобновлена', 'info');
      el.btnRetryJob.style.display = 'none';
    } catch (e) {
      showToast(`Не удалось возобновить: ${e.message}`, 'error');
    }
  });

  el.btnGoToReview.addEventListener('click', () => {
    if (state.currentJob) {
      setupReviewScreen(state.currentJob.id);
      switchScreen('screen-review');
    }
  });

  // SCREEN 3: REVIEW LOGIC
  async function setupReviewScreen(jobId) {
    el.navReviewTab.disabled = false;
    const candidates = await api(`/api/jobs/${jobId}/candidates`);
    state.candidates = candidates;

    el.candidatesTotalBadge.textContent = candidates.length;
    el.reviewCountBadge.textContent = candidates.length;

    // Load seekable video proxy into player
    const proxyStreamUrl = `/api/media/proxy/${jobId}`;
    if (el.videoPlayer.src !== window.location.origin + proxyStreamUrl) {
      el.videoPlayer.src = proxyStreamUrl;
      el.videoPlayer.load();
    }

    renderCandidates();
    updateAcceptedCount();
  }

  // Player time updates & scrubber synchronization
  el.videoPlayer.addEventListener('timeupdate', () => {
    const cur = el.videoPlayer.currentTime;
    const dur = el.videoPlayer.duration || 1;
    el.playerCurrentTime.textContent = formatSeconds(cur);
    el.playerTotalDuration.textContent = formatSeconds(dur);

    const progressPct = (cur / dur) * 100;
    el.scrubberProgress.style.width = `${progressPct}%`;
  });

  el.videoPlayer.addEventListener('loadedmetadata', () => {
    el.playerTotalDuration.textContent = formatSeconds(el.videoPlayer.duration);
    renderScrubberMarkers();
  });

  // Clicking scrubber bar seeks player
  el.scrubberBar.addEventListener('click', (e) => {
    const rect = el.scrubberBar.getBoundingClientRect();
    const pos = (e.clientX - rect.left) / rect.width;
    const dur = el.videoPlayer.duration;
    if (dur && !isNaN(dur)) {
      el.videoPlayer.currentTime = pos * dur;
    }
  });

  // Scrubbing Candidate Markers on timeline
  function renderScrubberMarkers() {
    const dur = el.videoPlayer.duration;
    if (!dur || isNaN(dur)) return;

    el.scrubberMarkersLayer.innerHTML = '';
    state.candidates.forEach((cand) => {
      const left = (cand.start_s / dur) * 100;
      const width = ((cand.end_s - cand.start_s) / dur) * 100;

      const marker = document.createElement('div');
      marker.className = `scrubber-marker ${cand.status === 'rejected' ? 'rejected' : ''} ${
        state.activeCandidateId === cand.id ? 'active' : ''
      }`;
      marker.style.left = `${Math.max(0, left)}%`;
      marker.style.width = `${Math.min(100, Math.max(0.5, width))}%`;
      marker.title = `${cand.working_title} (${formatSeconds(cand.start_s)} - ${formatSeconds(cand.end_s)})`;

      marker.addEventListener('click', (e) => {
        e.stopPropagation();
        seekPlayerToCandidate(cand.id);
      });

      el.scrubberMarkersLayer.appendChild(marker);
    });
  }

  // Nudge controls in player subcontrols
  document.getElementById('btn-nudge-back-5s').onclick = () => (el.videoPlayer.currentTime -= 5);
  document.getElementById('btn-nudge-back-1s').onclick = () => (el.videoPlayer.currentTime -= 1);
  document.getElementById('btn-nudge-back-01s').onclick = () => (el.videoPlayer.currentTime -= 0.1);
  document.getElementById('btn-nudge-fwd-01s').onclick = () => (el.videoPlayer.currentTime += 0.1);
  document.getElementById('btn-nudge-fwd-1s').onclick = () => (el.videoPlayer.currentTime += 1);
  document.getElementById('btn-nudge-fwd-5s').onclick = () => (el.videoPlayer.currentTime += 5);

  // Filter chips (All / Accepted / Rejected)
  el.filterChips.forEach((chip) => {
    chip.addEventListener('click', () => {
      el.filterChips.forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      state.filter = chip.dataset.filter;
      renderCandidates();
    });
  });

  // Render Candidate Cards
  function renderCandidates() {
    const filtered = state.candidates.filter((c) => {
      if (state.filter === 'accepted') return c.status === 'accepted';
      if (state.filter === 'rejected') return c.status === 'rejected';
      return true;
    });

    if (filtered.length === 0) {
      el.candidatesScrollList.innerHTML = `
        <div class="empty-state">
          <p>Нет кандидатов, соответствующих фильтру.</p>
        </div>
      `;
      return;
    }

    el.candidatesScrollList.innerHTML = filtered
      .map(
        (c) => `
      <div class="candidate-card ${c.status === 'rejected' ? 'rejected' : ''} ${
          state.activeCandidateId === c.id ? 'active' : ''
        }" id="cand-card-${c.id}">
        <div class="candidate-card-header">
          <div style="display: flex; align-items: center; gap: 0.5rem;">
            <span class="score-badge ${c.score < 6 ? 'low' : ''}">★ ${c.score}/10</span>
            <span class="category-tag">${c.category}</span>
          </div>
          <span class="badge ${c.status}">${c.status}</span>
        </div>

        <input type="text" class="candidate-title-input" value="${escapeHtml(c.working_title)}" 
          data-cand-id="${c.id}" placeholder="Заголовок клипа...">

        <!-- Fine-tuning boundary handles (±0.1s and ±1s precision) -->
        <div class="boundaries-tuning-box">
          <div class="boundary-item">
            <span class="boundary-lbl">Начало:</span>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'start', -1.0)">-1s</button>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'start', -0.1)">-0.1s</button>
            <span class="boundary-val" id="cand-start-${c.id}">${formatSeconds(c.start_s)}</span>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'start', 0.1)">+0.1s</button>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'start', 1.0)">+1s</button>
          </div>

          <div class="boundary-item">
            <span class="boundary-lbl">Конец:</span>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'end', -1.0)">-1s</button>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'end', -0.1)">-0.1s</button>
            <span class="boundary-val" id="cand-end-${c.id}">${formatSeconds(c.end_s)}</span>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'end', 0.1)">+0.1s</button>
            <button class="btn-nudger" onclick="window.nudgeTime('${c.id}', 'end', 1.0)">+1s</button>
          </div>
        </div>

        <div class="candidate-details">
          <div class="detail-row">
            <span class="detail-k">Вирусность:</span>
            <span>${escapeHtml(c.why_viral || '—')}</span>
          </div>
          <div class="detail-row">
            <span class="detail-k">Визуал:</span>
            <span>${escapeHtml(c.visual_evidence || '—')}</span>
          </div>
          <div class="detail-row">
            <span class="detail-k">Звук:</span>
            <span>${escapeHtml(c.audio_evidence || '—')}</span>
          </div>
        </div>

        <div class="candidate-actions">
          <button class="btn btn-secondary btn-sm" onclick="window.seekTo('${c.id}')">
            <span>▶ В плеере</span>
          </button>
          <div style="display: flex; gap: 0.35rem;">
            <button class="btn btn-danger btn-sm" onclick="window.setCandidateStatus('${c.id}', 'rejected')">
              Отклонить
            </button>
            <button class="btn btn-success btn-sm" onclick="window.setCandidateStatus('${c.id}', 'accepted')">
              ✓ Принять
            </button>
          </div>
        </div>
      </div>
    `
      )
      .join('');

    // Attach title update listeners on blur
    document.querySelectorAll('.candidate-title-input').forEach((input) => {
      input.addEventListener('change', async (e) => {
        const id = e.target.dataset.candId;
        const newTitle = e.target.value.trim();
        await patchCandidate(id, { working_title: newTitle });
      });
    });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function updateAcceptedCount() {
    const accepted = state.candidates.filter((c) => c.status === 'accepted').length;
    el.acceptedClipsCount.textContent = accepted;
  }

  function seekPlayerToCandidate(candId) {
    state.activeCandidateId = candId;
    const cand = state.candidates.find((c) => c.id === candId);
    if (!cand) return;

    el.videoPlayer.currentTime = Math.max(0, cand.start_s);
    el.videoPlayer.play();

    renderCandidates();
    renderScrubberMarkers();

    const card = document.getElementById(`cand-card-${candId}`);
    if (card) {
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  // Global window functions for inline onclick handlers
  window.seekTo = (candId) => seekPlayerToCandidate(candId);

  window.nudgeTime = async (candId, boundary, delta) => {
    const cand = state.candidates.find((c) => c.id === candId);
    if (!cand) return;

    let newStart = cand.start_s;
    let newEnd = cand.end_s;

    if (boundary === 'start') {
      newStart = Math.max(0, Math.round((cand.start_s + delta) * 10) / 10);
      if (newStart >= newEnd) return;
      el.videoPlayer.currentTime = newStart;
    } else {
      newEnd = Math.round((cand.end_s + delta) * 10) / 10;
      if (newEnd <= newStart) return;
      el.videoPlayer.currentTime = newEnd;
    }

    cand.start_s = newStart;
    cand.end_s = newEnd;

    // Fast UI update
    const startLbl = document.getElementById(`cand-start-${candId}`);
    const endLbl = document.getElementById(`cand-end-${candId}`);
    if (startLbl) startLbl.textContent = formatSeconds(newStart);
    if (endLbl) endLbl.textContent = formatSeconds(newEnd);

    renderScrubberMarkers();
    await patchCandidate(candId, { start_s: newStart, end_s: newEnd });
  };

  window.setCandidateStatus = async (candId, newStatus) => {
    const cand = state.candidates.find((c) => c.id === candId);
    if (!cand) return;

    cand.status = newStatus;
    updateAcceptedCount();
    renderCandidates();
    renderScrubberMarkers();

    await patchCandidate(candId, { status: newStatus });
  };

  async function patchCandidate(candId, patch) {
    try {
      const updated = await api(`/api/candidates/${candId}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
      refreshCandidateInList(updated);
    } catch (e) {
      showToast(`Ошибка сохранения: ${e.message}`, 'error');
    }
  }

  function refreshCandidateInList(updated) {
    const idx = state.candidates.findIndex((c) => c.id === updated.id);
    if (idx !== -1) {
      state.candidates[idx] = updated;
      updateAcceptedCount();
      renderScrubberMarkers();
    }
  }

  // Trigger Render of Selected Candidates
  el.btnTriggerRender.addEventListener('click', async () => {
    if (!state.currentJob) return;

    const accepted = state.candidates.filter((c) => c.status === 'accepted');
    if (accepted.length === 0) {
      showToast('Нет принятых кандидатов. Нажмите "Принять" у нужных моментов.', 'error');
      return;
    }

    try {
      el.btnTriggerRender.disabled = true;
      el.btnTriggerRender.innerHTML = '<span>⏳ Запуск монтажа...</span>';
      showToast(`Запуск рендера ${accepted.length} клипов...`, 'info');

      const res = await api(`/api/jobs/${state.currentJob.id}/render`, {
        method: 'POST',
        body: JSON.stringify({
          selected_candidate_ids: accepted.map((c) => c.id),
          layout: el.selectRenderLayout ? el.selectRenderLayout.value : 'original',
        }),
      });

      showToast(`Успешно смонтировано ${res.rendered_count} клипов!`, 'success');
      setupResultsScreen(state.currentJob.id);
      switchScreen('screen-results');
    } catch (e) {
      showToast(`Ошибка рендера: ${e.message}`, 'error');
    } finally {
      el.btnTriggerRender.disabled = false;
      el.btnTriggerRender.innerHTML = '<span>⚡ Запустить рендер клипов</span>';
    }
  });

  // Manual Moment Addition Modal
  el.btnAddMomentModal.addEventListener('click', () => {
    const cur = el.videoPlayer.currentTime || 0;
    el.manualStart.value = Math.max(0, cur - 10).toFixed(1);
    el.manualEnd.value = (cur + 20).toFixed(1);
    el.modalAddMoment.style.display = 'flex';
  });

  el.btnCloseMomentModal.addEventListener('click', () => (el.modalAddMoment.style.display = 'none'));
  el.btnCancelManualMoment.addEventListener('click', () => (el.modalAddMoment.style.display = 'none'));

  el.btnSetStartNow.addEventListener('click', () => {
    el.manualStart.value = (el.videoPlayer.currentTime || 0).toFixed(1);
  });
  el.btnSetEndNow.addEventListener('click', () => {
    el.manualEnd.value = (el.videoPlayer.currentTime || 0).toFixed(1);
  });

  el.btnSaveManualMoment.addEventListener('click', async () => {
    if (!state.currentJob) return;

    const title = el.manualTitle.value.trim();
    const start_s = parseFloat(el.manualStart.value);
    const end_s = parseFloat(el.manualEnd.value);

    if (!title) {
      showToast('Введите заголовок момента', 'error');
      return;
    }
    if (isNaN(start_s) || isNaN(end_s) || end_s <= start_s) {
      showToast('Конец момента должен быть больше начала', 'error');
      return;
    }

    try {
      const newCand = await api(`/api/jobs/${state.currentJob.id}/candidates`, {
        method: 'POST',
        body: JSON.stringify({
          start_s,
          end_s,
          working_title: title,
          category: el.manualCategory.value,
          score: parseInt(el.manualScore.value, 10) || 8,
          why_viral: el.manualReason.value.trim() || 'Добавлено вручную',
        }),
      });

      state.candidates.push(newCand);
      updateAcceptedCount();
      renderCandidates();
      renderScrubberMarkers();
      el.modalAddMoment.style.display = 'none';
      showToast('Момент успешно добавлен!', 'success');
      seekPlayerToCandidate(newCand.id);
    } catch (e) {
      showToast(`Ошибка добавления: ${e.message}`, 'error');
    }
  });

  // SCREEN 4: RESULTS LOGIC
  async function setupResultsScreen(jobId) {
    el.navResultsTab.disabled = false;
    const clips = await api(`/api/jobs/${jobId}/clips`);
    state.clips = clips;

    el.resultsCountBadge.textContent = clips.length;

    if (clips.length === 0) {
      el.clipsGridContainer.innerHTML = `
        <div class="empty-state">
          <p>Клипы ещё не созданы.</p>
        </div>
      `;
      return;
    }

    el.clipsGridContainer.innerHTML = clips
      .map(
        (clip) => `
      <div class="clip-result-card">
        <div class="clip-video-box">
          <video controls preload="metadata" playsinline>
            <source src="${clip.stream_url}" type="video/mp4">
          </video>
        </div>
        <div class="clip-card-body">
          <div class="clip-card-header">
            <span class="category-tag">${clip.layout}</span>
            <span class="score-badge">★ ${clip.manifest.score || 9}/10</span>
          </div>
          <h4 class="clip-title">${escapeHtml(clip.manifest.title || 'Клип')}</h4>
          <div class="clip-meta-line">
            <span>⏱ ${clip.duration_s} сек</span>
            <span>📍 ${formatSeconds(clip.manifest.start_s)} - ${formatSeconds(clip.manifest.end_s)}</span>
          </div>
          <div class="clip-download-row">
            <a href="${clip.download_url}" class="btn btn-primary btn-sm" download>
              <span>⬇ Скачать MP4</span>
            </a>
            <button class="btn btn-secondary btn-sm" onclick="window.copyText('${escapeHtml(
              clip.manifest.title || ''
            )}')">
              <span>📋 Копировать заголовок</span>
            </button>
          </div>
        </div>
      </div>
    `
      )
      .join('');
  }

  window.copyText = (text) => {
    navigator.clipboard.writeText(text);
    showToast('Заголовок скопирован в буфер обмена!', 'success');
  };

  el.btnStartAnotherJob.addEventListener('click', () => {
    switchScreen('screen-new-job');
  });

  // USAGE BREAKDOWN MODAL
  async function showUsageModal() {
    if (!state.currentJob) return;
    try {
      const usage = await api(`/api/jobs/${state.currentJob.id}/usage`);

      el.usageTotalCost.textContent = `$${usage.total_cost_usd}`;
      el.usageTotalTokens.textContent = (usage.total_tokens || 0).toLocaleString();
      el.usageTotalCalls.textContent = usage.total_calls;
      el.usageCacheRate.textContent = `${Math.round(usage.cache_hit_rate * 100)}%`;

      // Stages breakdown table
      el.usageStagesTbody.innerHTML = Object.entries(usage.stages || {})
        .map(
          ([stage, data]) => `
        <tr>
          <td><strong>${stage}</strong></td>
          <td>${data.calls}</td>
          <td>${data.tokens_in.toLocaleString()}</td>
          <td>${data.tokens_out.toLocaleString()}</td>
          <td class="text-accent">$${data.cost_usd}</td>
        </tr>
      `
        )
        .join('');

      // API calls log
      el.usageCallsTbody.innerHTML = (usage.calls || [])
        .map(
          (c) => `
        <tr>
          <td>${c.created_at ? c.created_at.split('T')[1].slice(0, 8) : '—'}</td>
          <td>${c.stage}</td>
          <td>${c.model}</td>
          <td>${c.tokens_in}</td>
          <td>${c.tokens_out}</td>
          <td class="text-accent">$${c.cost_usd.toFixed(5)}</td>
          <td>${c.cache_hit ? '✓ hit' : 'miss'}</td>
        </tr>
      `
        )
        .join('');

      el.modalUsage.style.display = 'flex';
    } catch (e) {
      showToast(`Не удалось загрузить отчёт: ${e.message}`, 'error');
    }
  }

  el.btnShowUsage.addEventListener('click', showUsageModal);
  el.btnViewUsageFromResults.addEventListener('click', showUsageModal);
  el.btnCloseUsageModal.addEventListener('click', () => (el.modalUsage.style.display = 'none'));

  // RECENT JOBS MODAL
  el.btnRecentJobs.addEventListener('click', async () => {
    try {
      const jobs = await api('/api/jobs');
      el.recentJobsList.innerHTML = jobs
        .map(
          (j) => `
        <div class="card" style="margin-bottom: 0.75rem; padding: 1rem; cursor: pointer;" onclick="window.selectJob('${j.id}')">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
            <strong style="font-size: 0.95rem;">${escapeHtml(j.source_title || 'Задача')}</strong>
            <span class="badge ${j.status}">${j.status}</span>
          </div>
          <div style="display: flex; gap: 1rem; font-size: 0.8rem; color: #94a3b8;">
            <span>ID: ${j.id}</span>
            <span>Расход: $${(j.cost_usd || 0).toFixed(3)}</span>
            <span>Клипов: ${j.clips_count || 0}</span>
          </div>
        </div>
      `
        )
        .join('');
      el.modalRecentJobs.style.display = 'flex';
    } catch (e) {
      showToast(`Ошибка загрузки истории: ${e.message}`, 'error');
    }
  });

  window.selectJob = (jobId) => {
    el.modalRecentJobs.style.display = 'none';
    startJobMonitoring(jobId);
  };

  el.btnCloseJobsModal.addEventListener('click', () => (el.modalRecentJobs.style.display = 'none'));

  // Nav Tabs Click
  el.navTabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      if (!tab.disabled) {
        switchScreen(tab.dataset.screen);
      }
    });
  });

  // Initialization
  loadProfiles();
  setupUploadHandlers();
})();
