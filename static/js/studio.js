/**
 * TranscribeHub Pro Studio Engine
 * Synchronized Video Playback, Segment Subtitle Editor & Keyboard Controller
 */

class TranscribeStudio {
    constructor(config) {
        this.taskId = config.taskId;
        this.mediaElement = document.getElementById(config.mediaElementId);
        this.segmentsContainer = document.getElementById('segments-list');
        this.continuousTextarea = document.getElementById('continuous-text');
        this.saveStatusEl = document.getElementById('save-status');
        this.currentTimeEl = document.getElementById('current-time-display');
        this.totalDurationEl = document.getElementById('total-duration-display');
        this.playbackProgressEl = document.getElementById('playback-progress');

        this.segments = [];
        this.activeSegmentId = null;
        this.mode = 'segments'; // 'segments' or 'text'
        this.autoPauseOnType = true;
        this.typingTimer = null;
        this.isTyping = false;

        // Loop section state
        this.isLooping = false;
        this.loopStart = 0;
        this.loopEnd = 5;

        // Auto-save intervals
        this.lastSavedJson = '';
        this.saveInterval = null;

        // Speech Recognition
        this.recognition = null;
        this.isRecognizing = false;

        // Anti-AI / Human Typing Telemetry
        this.startTime = Date.now();
        this.keystrokes = 0;
        this.pasteEvents = 0;
        this.pastedChars = 0;

        this.init(config.initialSegments, config.initialText);
    }

    init(initialSegments, initialText) {
        // Load segments
        if (Array.isArray(initialSegments) && initialSegments.length > 0) {
            this.segments = initialSegments.map((s, idx) => ({
                id: s.id || idx + 1,
                start: parseFloat(s.start) || 0.0,
                end: parseFloat(s.end) || 3.0,
                speaker: s.speaker || 'Speaker 1',
                text: s.text || ''
            }));
        } else {
            // Check local storage backup
            const cached = localStorage.getItem(`transcribe_backup_${this.taskId}`);
            if (cached) {
                try {
                    const parsed = JSON.parse(cached);
                    if (Array.isArray(parsed) && parsed.length > 0) {
                        this.segments = parsed;
                        console.log('Restored segments from localStorage backup');
                    }
                } catch (e) {}
            }
            if (this.segments.length === 0) {
                this.segments = [
                    { id: 1, start: 0.0, end: 4.5, speaker: 'Speaker 1', text: '' }
                ];
            }
        }

        if (this.continuousTextarea && initialText) {
            this.continuousTextarea.value = initialText;
        }

        if (this.continuousTextarea) {
            this.continuousTextarea.addEventListener('keydown', () => {
                this.keystrokes++;
            });
            this.continuousTextarea.addEventListener('paste', (e) => {
                this.pasteEvents++;
                const text = (e.clipboardData || window.clipboardData).getData('text') || '';
                this.pastedChars += text.length;
            });
        }

        this.renderSegments();
        this.bindMediaEvents();
        this.bindKeyboardShortcuts();
        this.bindControlButtons();
        this.initSpeechRecognition();
        this.startAutoSave();
        this.updateWordStats();
    }

    formatTime(seconds, includeMillis = false) {
        if (isNaN(seconds) || seconds < 0) seconds = 0;
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        const millis = Math.floor((seconds - Math.floor(seconds)) * 10);
        
        const minStr = String(mins).padStart(2, '0');
        const secStr = String(secs).padStart(2, '0');
        
        if (includeMillis) {
            return `${minStr}:${secStr}.${millis}`;
        }
        return `${minStr}:${secStr}`;
    }

    parseTimeString(str) {
        const parts = str.trim().split(':');
        if (parts.length === 2) {
            return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
        } else if (parts.length === 3) {
            return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
        }
        return parseFloat(str) || 0;
    }

    bindMediaEvents() {
        if (!this.mediaElement) return;

        this.mediaElement.addEventListener('loadedmetadata', () => {
            if (this.totalDurationEl) {
                this.totalDurationEl.textContent = this.formatTime(this.mediaElement.duration);
            }
            if (this.playbackProgressEl) {
                this.playbackProgressEl.max = this.mediaElement.duration || 100;
            }
        });

        this.mediaElement.addEventListener('timeupdate', () => {
            const current = this.mediaElement.currentTime;
            if (this.currentTimeEl) {
                this.currentTimeEl.textContent = this.formatTime(current, true);
            }
            if (this.playbackProgressEl && !this.playbackProgressEl.dragging) {
                this.playbackProgressEl.value = current;
            }

            // Loop checking
            if (this.isLooping && current >= this.loopEnd) {
                this.mediaElement.currentTime = this.loopStart;
                return;
            }

            // Highlight active segment
            this.highlightActiveSegment(current);
        });

        this.mediaElement.addEventListener('play', () => {
            const playBtn = document.getElementById('btn-play-pause');
            if (playBtn) playBtn.innerHTML = '⏸ <span class="hidden sm:inline">Pause</span>';
        });

        this.mediaElement.addEventListener('pause', () => {
            const playBtn = document.getElementById('btn-play-pause');
            if (playBtn) playBtn.innerHTML = '▶ <span class="hidden sm:inline">Play</span>';
        });
    }

    highlightActiveSegment(currentTime) {
        let foundActive = false;
        const segmentCards = document.querySelectorAll('.segment-card');
        
        segmentCards.forEach(card => {
            const segId = parseInt(card.dataset.id);
            const seg = this.segments.find(s => s.id === segId);
            if (seg && currentTime >= seg.start && currentTime <= seg.end) {
                card.classList.add('active-playback');
                foundActive = true;
                this.activeSegmentId = segId;
            } else {
                card.classList.remove('active-playback');
            }
        });
    }

    renderSegments() {
        if (!this.segmentsContainer) return;
        this.segmentsContainer.innerHTML = '';

        this.segments.sort((a, b) => a.start - b.start);

        this.segments.forEach((seg, idx) => {
            const card = document.createElement('div');
            card.className = 'segment-card p-4 rounded-xl bg-slate-900 border border-slate-800 transition-all text-slate-200';
            card.dataset.id = seg.id;

            card.innerHTML = `
                <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div class="flex items-center gap-2">
                        <button type="button" class="btn-play-seg px-2.5 py-1 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white flex items-center gap-1.5 transition" title="Play Segment">
                            ▶ ${this.formatTime(seg.start)} - ${this.formatTime(seg.end)}
                        </button>
                        <input type="text" class="speaker-input bg-slate-800 text-indigo-300 font-medium text-xs px-2.5 py-1 rounded-lg border border-slate-700 focus:outline-none focus:border-indigo-500 w-28" value="${this.escapeHtml(seg.speaker)}" placeholder="Speaker...">
                    </div>
                    
                    <div class="flex items-center gap-1.5">
                        <button type="button" class="btn-set-start px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700" title="Set start to current video playhead">
                            ⏱ In: [${this.formatTime(seg.start)}]
                        </button>
                        <button type="button" class="btn-set-end px-2 py-1 text-xs rounded bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700" title="Set end to current video playhead">
                            ⏱ Out: [${this.formatTime(seg.end)}]
                        </button>
                        <button type="button" class="btn-delete-seg p-1 text-slate-400 hover:text-rose-400 transition ml-1" title="Delete segment">
                            ✕
                        </button>
                    </div>
                </div>

                <div class="relative">
                    <textarea class="seg-text w-full bg-slate-950/80 text-slate-100 text-sm p-3 rounded-lg border border-slate-800 focus:outline-none focus:border-indigo-500 resize-y min-h-[64px]" placeholder="Transcribe this section...">${this.escapeHtml(seg.text)}</textarea>
                </div>

                <div class="flex items-center justify-between mt-2 pt-2 border-t border-slate-800/60 text-xs text-slate-500">
                    <span class="seg-duration">${(seg.end - seg.start).toFixed(1)}s duration</span>
                    <button type="button" class="btn-add-after hover:text-indigo-400 text-slate-400 flex items-center gap-1 transition">
                        + Add Segment After
                    </button>
                </div>
            `;

            // Bind card event listeners
            const playSegBtn = card.querySelector('.btn-play-seg');
            playSegBtn.addEventListener('click', () => this.playSegment(seg));

            const setStartBtn = card.querySelector('.btn-set-start');
            setStartBtn.addEventListener('click', () => {
                if (this.mediaElement) {
                    seg.start = parseFloat(this.mediaElement.currentTime.toFixed(2));
                    if (seg.end <= seg.start) seg.end = seg.start + 3.0;
                    this.renderSegments();
                }
            });

            const setEndBtn = card.querySelector('.btn-set-end');
            setEndBtn.addEventListener('click', () => {
                if (this.mediaElement) {
                    seg.end = parseFloat(this.mediaElement.currentTime.toFixed(2));
                    if (seg.end <= seg.start) seg.start = Math.max(0, seg.end - 1.0);
                    this.renderSegments();
                }
            });

            const deleteBtn = card.querySelector('.btn-delete-seg');
            deleteBtn.addEventListener('click', () => {
                if (this.segments.length <= 1) {
                    showToast('At least one segment must remain.', 'warning');
                    return;
                }
                this.segments = this.segments.filter(s => s.id !== seg.id);
                this.renderSegments();
            });

            const addAfterBtn = card.querySelector('.btn-add-after');
            addAfterBtn.addEventListener('click', () => this.addSegmentAfter(seg));

            const speakerInput = card.querySelector('.speaker-input');
            speakerInput.addEventListener('change', (e) => {
                seg.speaker = e.target.value.trim();
                this.syncToContinuousText();
            });

            const textarea = card.querySelector('.seg-text');
            textarea.addEventListener('keydown', () => {
                this.keystrokes++;
            });
            textarea.addEventListener('paste', (e) => {
                this.pasteEvents++;
                const pastedText = (e.clipboardData || window.clipboardData).getData('text') || '';
                this.pastedChars += pastedText.length;
            });
            textarea.addEventListener('input', (e) => {
                seg.text = e.target.value;
                this.handleTypingAutoPause();
                this.syncToContinuousText();
                this.updateWordStats();
            });

            this.segmentsContainer.appendChild(card);
        });

        this.syncToContinuousText();
    }

    playSegment(seg) {
        if (!this.mediaElement) return;
        this.mediaElement.currentTime = seg.start;
        this.mediaElement.play();

        // Loop or stop at segment end if needed
        const checkEnd = () => {
            if (this.mediaElement.currentTime >= seg.end) {
                this.mediaElement.pause();
                this.mediaElement.removeEventListener('timeupdate', checkEnd);
            }
        };
        this.mediaElement.addEventListener('timeupdate', checkEnd);
    }

    addSegmentAfter(prevSeg) {
        const newStart = prevSeg ? prevSeg.end : (this.mediaElement ? this.mediaElement.currentTime : 0);
        const newEnd = newStart + 3.5;
        const newId = Date.now();
        const prevSpeaker = prevSeg ? prevSeg.speaker : 'Speaker 1';

        this.segments.push({
            id: newId,
            start: parseFloat(newStart.toFixed(2)),
            end: parseFloat(newEnd.toFixed(2)),
            speaker: prevSpeaker,
            text: ''
        });

        this.renderSegments();

        // Focus new textarea
        setTimeout(() => {
            const newCard = document.querySelector(`.segment-card[data-id="${newId}"] textarea`);
            if (newCard) newCard.focus();
        }, 50);
    }

    addSegmentAtPlayhead() {
        const cur = this.mediaElement ? this.mediaElement.currentTime : 0;
        const newId = Date.now();
        const lastSeg = this.segments[this.segments.length - 1];
        const speaker = lastSeg ? lastSeg.speaker : 'Speaker 1';

        this.segments.push({
            id: newId,
            start: parseFloat(cur.toFixed(2)),
            end: parseFloat((cur + 3.5).toFixed(2)),
            speaker: speaker,
            text: ''
        });

        this.renderSegments();
        showToast(`New segment added at ${this.formatTime(cur)}`, 'info');
    }

    handleTypingAutoPause() {
        if (!this.autoPauseOnType || !this.mediaElement) return;

        if (!this.mediaElement.paused) {
            this.mediaElement.pause();
            this.isTyping = true;
        }

        clearTimeout(this.typingTimer);
        this.typingTimer = setTimeout(() => {
            this.isTyping = false;
        }, 1500);
    }

    syncToContinuousText() {
        if (!this.continuousTextarea) return;
        const lines = this.segments.map(seg => {
            const ts = `[${this.formatTime(seg.start)}]`;
            const spk = seg.speaker ? `${seg.speaker}: ` : '';
            return `${ts} ${spk}${seg.text}`;
        });
        this.continuousTextarea.value = lines.join('\n\n');
    }

    syncFromContinuousText() {
        if (!this.continuousTextarea) return;
        const text = this.continuousTextarea.value;
        const blocks = text.split(/\n\s*\n/).filter(b => b.trim());
        
        if (blocks.length === 0) return;

        const newSegments = [];
        let idCounter = 1;

        blocks.forEach((block, idx) => {
            // Regex to parse [MM:SS] Speaker: text
            const match = block.match(/^\[([0-9:]+)\]\s*(?:([^:]+):\s*)?(.*)$/s);
            if (match) {
                const timeStr = match[1];
                const speaker = match[2] ? match[2].trim() : 'Speaker 1';
                const body = match[3] ? match[3].trim() : '';
                const startSec = this.parseTimeString(timeStr);
                newSegments.push({
                    id: idCounter++,
                    start: startSec,
                    end: startSec + 4.0,
                    speaker: speaker,
                    text: body
                });
            } else {
                newSegments.push({
                    id: idCounter++,
                    start: idx * 4.0,
                    end: (idx + 1) * 4.0,
                    speaker: 'Speaker 1',
                    text: block.trim()
                });
            }
        });

        if (newSegments.length > 0) {
            this.segments = newSegments;
            this.renderSegments();
        }
    }

    updateWordStats() {
        const fullText = this.segments.map(s => s.text).join(' ');
        const words = fullText.trim().split(/\s+/).filter(w => w.length > 0).length;
        const chars = fullText.length;

        const countEl = document.getElementById('word-count-display');
        if (countEl) {
            countEl.textContent = `${words} words (${chars} chars)`;
        }
    }

    bindControlButtons() {
        // Play/Pause
        const playBtn = document.getElementById('btn-play-pause');
        if (playBtn) {
            playBtn.addEventListener('click', () => this.togglePlay());
        }

        // Jump back 3s
        const back3Btn = document.getElementById('btn-back-3s');
        if (back3Btn) {
            back3Btn.addEventListener('click', () => this.seekBy(-3));
        }

        // Jump forward 3s
        const fwd3Btn = document.getElementById('btn-fwd-3s');
        if (fwd3Btn) {
            fwd3Btn.addEventListener('click', () => this.seekBy(3));
        }

        // Speed buttons
        document.querySelectorAll('.btn-speed').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const speed = parseFloat(e.target.dataset.speed);
                if (this.mediaElement) {
                    this.mediaElement.playbackRate = speed;
                }
                document.querySelectorAll('.btn-speed').forEach(b => b.classList.remove('bg-indigo-600', 'text-white'));
                e.target.classList.add('bg-indigo-600', 'text-white');
                showToast(`Playback speed: ${speed}x`, 'info');
            });
        });

        // Loop toggle
        const loopBtn = document.getElementById('btn-loop-segment');
        if (loopBtn) {
            loopBtn.addEventListener('click', () => {
                this.isLooping = !this.isLooping;
                if (this.isLooping && this.mediaElement) {
                    this.loopStart = Math.max(0, this.mediaElement.currentTime - 2.5);
                    this.loopEnd = this.loopStart + 5.0;
                    this.mediaElement.currentTime = this.loopStart;
                    loopBtn.classList.add('bg-amber-600', 'text-white');
                    showToast(`Looping 5s section [${this.formatTime(this.loopStart)} - ${this.formatTime(this.loopEnd)}]`, 'info');
                } else {
                    loopBtn.classList.remove('bg-amber-600', 'text-white');
                    showToast('Loop disabled', 'info');
                }
            });
        }

        // Add segment button
        const addSegBtn = document.getElementById('btn-add-segment');
        if (addSegBtn) {
            addSegBtn.addEventListener('click', () => this.addSegmentAtPlayhead());
        }

        // Progress slider scrub
        if (this.playbackProgressEl && this.mediaElement) {
            this.playbackProgressEl.addEventListener('input', (e) => {
                this.playbackProgressEl.dragging = true;
                this.mediaElement.currentTime = parseFloat(e.target.value);
            });
            this.playbackProgressEl.addEventListener('change', () => {
                this.playbackProgressEl.dragging = false;
            });
        }

        // Auto-pause toggle checkbox
        const autoPauseCheckbox = document.getElementById('toggle-autopause');
        if (autoPauseCheckbox) {
            autoPauseCheckbox.addEventListener('change', (e) => {
                this.autoPauseOnType = e.target.checked;
            });
        }

        // Save Draft button
        const saveDraftBtn = document.getElementById('btn-save-draft');
        if (saveDraftBtn) {
            saveDraftBtn.addEventListener('click', () => this.saveDraft(true));
        }

        // Submit button
        const submitBtn = document.getElementById('btn-submit-task');
        if (submitBtn) {
            submitBtn.addEventListener('click', () => this.openSubmitModal());
        }

        // Mode switch tabs (Segments vs Continuous Text)
        const tabSegments = document.getElementById('tab-segments');
        const tabText = document.getElementById('tab-text');
        const panelSegments = document.getElementById('panel-segments');
        const panelText = document.getElementById('panel-text');

        if (tabSegments && tabText && panelSegments && panelText) {
            tabSegments.addEventListener('click', () => {
                this.mode = 'segments';
                tabSegments.classList.add('border-indigo-500', 'text-indigo-400');
                tabSegments.classList.remove('border-transparent', 'text-slate-400');
                tabText.classList.remove('border-indigo-500', 'text-indigo-400');
                tabText.classList.add('border-transparent', 'text-slate-400');
                panelSegments.classList.remove('hidden');
                panelText.classList.add('hidden');
                this.syncFromContinuousText();
            });

            tabText.addEventListener('click', () => {
                this.mode = 'text';
                tabText.classList.add('border-indigo-500', 'text-indigo-400');
                tabText.classList.remove('border-transparent', 'text-slate-400');
                tabSegments.classList.remove('border-indigo-500', 'text-indigo-400');
                tabSegments.classList.add('border-transparent', 'text-slate-400');
                panelText.classList.remove('hidden');
                panelSegments.classList.add('hidden');
                this.syncToContinuousText();
            });
        }

        // Text mode stamp buttons
        const stampTimeBtn = document.getElementById('btn-stamp-time');
        if (stampTimeBtn && this.continuousTextarea) {
            stampTimeBtn.addEventListener('click', () => {
                const cur = this.mediaElement ? this.mediaElement.currentTime : 0;
                this.insertAtCursor(this.continuousTextarea, `[${this.formatTime(cur)}] `);
            });
        }

        document.querySelectorAll('.btn-stamp-tag').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const tag = e.currentTarget.dataset.tag;
                if (this.continuousTextarea) {
                    this.insertAtCursor(this.continuousTextarea, `${tag} `);
                }
            });
        });
    }

    insertAtCursor(input, textToInsert) {
        const start = input.selectionStart || 0;
        const end = input.selectionEnd || 0;
        const text = input.value;
        input.value = text.substring(0, start) + textToInsert + text.substring(end);
        input.selectionStart = input.selectionEnd = start + textToInsert.length;
        input.focus();
        this.updateWordStats();
    }

    bindKeyboardShortcuts() {
        window.addEventListener('keydown', (e) => {
            // Esc or Ctrl+Space: Play / Pause
            if (e.key === 'Escape' || (e.ctrlKey && e.code === 'Space') || (e.altKey && e.code === 'Space')) {
                e.preventDefault();
                this.togglePlay();
                return;
            }

            // Alt+Left or Shift+Left: Seek back 3s
            if (e.altKey && e.key === 'ArrowLeft') {
                e.preventDefault();
                this.seekBy(-3);
                return;
            }

            // Alt+Right or Shift+Right: Seek forward 3s
            if (e.altKey && e.key === 'ArrowRight') {
                e.preventDefault();
                this.seekBy(3);
                return;
            }

            // Ctrl+Enter: Insert new segment
            if (e.ctrlKey && e.key === 'Enter') {
                e.preventDefault();
                this.addSegmentAtPlayhead();
                return;
            }

            // Ctrl+S: Save draft
            if (e.ctrlKey && e.key === 's') {
                e.preventDefault();
                this.saveDraft(true);
                return;
            }

            // Ctrl+T: Insert timestamp in continuous text mode
            if (e.ctrlKey && e.key === 't' && this.mode === 'text' && this.continuousTextarea) {
                e.preventDefault();
                const cur = this.mediaElement ? this.mediaElement.currentTime : 0;
                this.insertAtCursor(this.continuousTextarea, `[${this.formatTime(cur)}] `);
                return;
            }
        });
    }

    togglePlay() {
        if (!this.mediaElement) return;
        if (this.mediaElement.paused) {
            this.mediaElement.play();
        } else {
            this.mediaElement.pause();
        }
    }

    seekBy(deltaSeconds) {
        if (!this.mediaElement) return;
        this.mediaElement.currentTime = Math.max(0, this.mediaElement.currentTime + deltaSeconds);
        showToast(deltaSeconds < 0 ? `⏪ ${Math.abs(deltaSeconds)}s` : `⏩ ${deltaSeconds}s`, 'info');
    }

    initSpeechRecognition() {
        const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
        const voiceBtn = document.getElementById('btn-voice-assist');

        if (!SpeechRec) {
            if (voiceBtn) {
                voiceBtn.title = 'Web Speech API is not supported in this browser';
                voiceBtn.classList.add('opacity-50', 'cursor-not-allowed');
            }
            return;
        }

        this.recognition = new SpeechRec();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-US';

        this.recognition.onresult = (event) => {
            let transcriptText = '';
            for (let i = event.resultIndex; i < event.results.length; ++i) {
                transcriptText += event.results[i][0].transcript;
            }

            // Insert into active segment
            const activeCard = document.querySelector('.segment-card.active-playback textarea');
            if (activeCard) {
                activeCard.value = (activeCard.value + ' ' + transcriptText).trim();
                activeCard.dispatchEvent(new Event('input'));
            } else if (this.continuousTextarea) {
                this.continuousTextarea.value = (this.continuousTextarea.value + ' ' + transcriptText).trim();
                this.updateWordStats();
            }
        };

        this.recognition.onerror = (e) => {
            console.warn('Speech recognition notice:', e.error);
            this.isRecognizing = false;
            if (voiceBtn) voiceBtn.classList.remove('bg-rose-600', 'text-white');
        };

        if (voiceBtn) {
            voiceBtn.addEventListener('click', () => {
                if (this.isRecognizing) {
                    this.recognition.stop();
                    this.isRecognizing = false;
                    voiceBtn.classList.remove('bg-rose-600', 'text-white');
                    showToast('Speech dictation stopped', 'info');
                } else {
                    try {
                        this.recognition.start();
                        this.isRecognizing = true;
                        voiceBtn.classList.add('bg-rose-600', 'text-white');
                        showToast('🎙️ Listening... speak clearly', 'success');
                    } catch (err) {
                        console.error(err);
                    }
                }
            });
        }
    }

    startAutoSave() {
        // Save draft every 15 seconds
        this.saveInterval = setInterval(() => {
            this.saveDraft(false);
        }, 15000);
    }

    async saveDraft(isManual = false) {
        if (this.mode === 'text') {
            this.syncFromContinuousText();
        }

        const jsonString = JSON.stringify(this.segments);
        const textContent = this.segments.map(s => `[${this.formatTime(s.start)}] ${s.speaker}: ${s.text}`).join('\n\n');

        // Backup locally
        try {
            localStorage.setItem(`transcribe_backup_${this.taskId}`, jsonString);
        } catch (e) {}

        if (this.saveStatusEl) {
            this.saveStatusEl.textContent = 'Saving...';
        }

        try {
            const res = await fetch(`/api/tasks/${this.taskId}/save-draft`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content_json: jsonString,
                    content_text: textContent
                })
            });

            const data = await res.json();
            if (data.success) {
                const timeString = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                if (this.saveStatusEl) {
                    this.saveStatusEl.textContent = `Draft saved at ${timeString}`;
                }
                if (isManual) {
                    showToast('Draft successfully saved to server', 'success');
                }
            } else {
                if (this.saveStatusEl) this.saveStatusEl.textContent = 'Save failed';
            }
        } catch (err) {
            if (this.saveStatusEl) this.saveStatusEl.textContent = 'Offline (Saved locally)';
        }
    }

    openSubmitModal() {
        const modal = document.getElementById('submit-modal');
        if (modal) {
            modal.classList.remove('hidden');
        }
    }

    escapeHtml(text) {
        if (!text) return '';
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
        return String(text).replace(/[&<>"']/g, m => map[m]);
    }
}

// Global submit confirm handler
async function confirmSubmitTranscription(taskId) {
    const notes = document.getElementById('transcriber-notes-input') ? document.getElementById('transcriber-notes-input').value : '';
    const studio = window.studioInstance;
    
    if (studio && studio.mode === 'text') {
        studio.syncFromContinuousText();
    }

    const jsonString = studio ? JSON.stringify(studio.segments) : '[]';
    const textContent = studio ? studio.segments.map(s => `[${studio.formatTime(s.start)}] ${s.speaker}: ${s.text}`).join('\n\n') : '';

    // Calculate typing & time telemetry
    const elapsedSecs = studio && studio.startTime ? Math.round((Date.now() - studio.startTime) / 1000) : 0;
    const telemetry = {
        elapsed_seconds: elapsedSecs,
        keystrokes: studio ? studio.keystrokes : 0,
        paste_events: studio ? studio.pasteEvents : 0,
        pasted_chars: studio ? studio.pastedChars : 0
    };

    try {
        const res = await fetch(`/api/tasks/${taskId}/submit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content_json: jsonString,
                content_text: textContent,
                notes: notes,
                telemetry: telemetry
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast('Transcription submitted for review!', 'success');
            setTimeout(() => {
                window.location.href = data.redirect_url;
            }, 800);
        } else {
            showToast('Submission error: ' + (data.error || 'Unknown'), 'error');
        }
    } catch (err) {
        showToast('Network error while submitting', 'error');
    }
}
