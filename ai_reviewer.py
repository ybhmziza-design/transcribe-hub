"""
AI Reviewer module for TranscribeHub.
Uses local Whisper (or fast fallback) to transcribe task audio
and compute similarity/accuracy score against the transcriber's submission.
"""
import os
import re
import difflib
import logging
import threading
import subprocess
from datetime import datetime
import requests

import database

logger = logging.getLogger('ai_reviewer')
logger.setLevel(logging.INFO)

# Global lock to avoid loading multiple Whisper models concurrently in low-memory environments
_whisper_lock = threading.Lock()
_cached_model = None
_cached_model_name = None


def normalize_text(text: str) -> str:
    """Normalize text for consistent comparison (lowercase, remove punctuation, normalize spaces)."""
    if not text:
        return ""
    text = text.lower()
    # Remove punctuation
    text = re.sub(r'[^\w\s]', ' ', text)
    # Collapse multiple whitespaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def compute_accuracy(submitted_text: str, reference_text: str) -> float:
    """
    Computes an accuracy percentage (0.0 to 100.0) between submitted text
    and reference text using Word Error Rate / SequenceMatcher token ratio.
    """
    norm_sub = normalize_text(submitted_text)
    norm_ref = normalize_text(reference_text)

    if not norm_ref and not norm_sub:
        return 100.0
    if not norm_ref or not norm_sub:
        return 0.0

    sub_words = norm_sub.split()
    ref_words = norm_ref.split()

    matcher = difflib.SequenceMatcher(None, ref_words, sub_words)
    similarity = matcher.ratio()

    return round(similarity * 100.0, 1)


def get_audio_from_source(video_source: str, temp_dir: str = "/tmp") -> str:
    """
    Ensures a local audio/video file path is available for Whisper.
    If video_source is a URL, downloads it to temp_dir.
    """
    if os.path.exists(video_source):
        return video_source

    # Check if it's a relative path in uploads
    if not video_source.startswith("http://") and not video_source.startswith("https://"):
        local_candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), video_source.lstrip("/"))
        if os.path.exists(local_candidate):
            return local_candidate
        uploads_candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', os.path.basename(video_source))
        if os.path.exists(uploads_candidate):
            return uploads_candidate

    # It's an HTTP/HTTPS URL
    ext = os.path.splitext(video_source.split("?")[0])[1] or ".mp4"
    temp_file = os.path.join(temp_dir, f"ai_eval_{os.getpid()}_{int(datetime.now().timestamp())}{ext}")
    resp = requests.get(video_source, stream=True, timeout=60)
    resp.raise_for_status()
    with open(temp_file, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    return temp_file


def transcribe_with_local_whisper(media_path: str, model_name: str = "tiny") -> tuple[bool, str]:
    """
    Loads local Whisper model and transcribes audio.
    Returns (True, transcript_text) or (False, error_message).
    """
    global _cached_model, _cached_model_name

    with _whisper_lock:
        try:
            import whisper
        except ImportError:
            return False, "Whisper package is not installed."

        try:
            if _cached_model is None or _cached_model_name != model_name:
                logger.info(f"Loading Whisper model '{model_name}'...")
                _cached_model = whisper.load_model(model_name)
                _cached_model_name = model_name

            result = _cached_model.transcribe(media_path, fp16=False)
            text = result.get("text", "").strip()
            return True, text
        except Exception as e:
            logger.error(f"Error during Whisper transcription: {e}")
            return False, str(e)


def run_ai_review(task_id: int):
    """
    Background worker function that performs AI evaluation on a submitted task.
    Updates the database with the result and releases payout if score >= threshold.
    """
    conn = database.get_db_connection()
    try:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            return

        transcription = conn.execute(
            "SELECT * FROM transcriptions WHERE task_id = ?", (task_id,)
        ).fetchone()

        if not transcription or transcription['status'] != 'submitted':
            return

        # Check settings
        auto_enabled = database.get_setting('ai_auto_review_enabled', '1') == '1'
        if not auto_enabled:
            return

        threshold = float(database.get_setting('ai_pass_threshold', '88'))
        whisper_model = database.get_setting('ai_whisper_model', 'tiny')

        media_source = task['video_source']
        if not media_source:
            return

        temp_download = None
        try:
            media_path = get_audio_from_source(media_source)
            if media_path != media_source and media_path.startswith("/tmp"):
                temp_download = media_path
        except Exception as e:
            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (task_id, 'AI Reviewer', 'ai_review_skipped', f'AI Review skipped (media unreachable: {e})')
            )
            conn.commit()
            return

        success, ai_transcript_or_err = transcribe_with_local_whisper(media_path, model_name=whisper_model)

        # Cleanup temp file if downloaded
        if temp_download and os.path.exists(temp_download):
            try:
                os.remove(temp_download)
            except Exception:
                pass

        if not success:
            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (task_id, 'AI Reviewer', 'ai_review_error', f'AI Review could not run: {ai_transcript_or_err}. Pending manual review.')
            )
            conn.commit()
            return

        # Calculate accuracy score
        submitted_text = transcription['content_text'] or ""
        score = compute_accuracy(submitted_text, ai_transcript_or_err)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        feedback_note = (
            f"AI Accuracy Score: {score}%. "
            f"(Evaluated against local Whisper {whisper_model.upper()} model; pass mark: {threshold}%)."
        )

        if score >= threshold:
            # AUTO-APPROVE
            conn.execute(
                "UPDATE tasks SET status='completed', updated_at=? WHERE id=?",
                (now, task_id)
            )
            conn.execute('''
                UPDATE transcriptions
                SET status='approved', reviewer_feedback=?, reviewed_at=?, updated_at=?
                WHERE task_id=?
            ''', (feedback_note, now, now, task_id))

            if task['claimed_by']:
                conn.execute(
                    "UPDATE users SET balance = balance + ? WHERE id = ?",
                    (task['reward'], task['claimed_by'])
                )

            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (
                    task_id,
                    'AI Reviewer',
                    'ai_approved',
                    f'✅ AI Auto-Approved ({score}% accuracy >= {threshold}% pass mark). Released ${task["reward"]:.2f} earnings.'
                )
            )
        else:
            # Below threshold: Request revision or leave for manual admin review
            feedback_note += f" Did not meet {threshold}% pass mark threshold."
            conn.execute('''
                UPDATE transcriptions
                SET reviewer_feedback=?, updated_at=?
                WHERE task_id=?
            ''', (feedback_note, now, task_id))

            conn.execute(
                "INSERT INTO task_activities (task_id, actor, action, message) VALUES (?,?,?,?)",
                (
                    task_id,
                    'AI Reviewer',
                    'ai_flagged',
                    f'⚠️ AI Scored {score}% (Pass mark is {threshold}%). Flagged for Admin Review.'
                )
            )

        conn.commit()

    except Exception as exc:
        logger.exception(f"Unexpected error in run_ai_review for task {task_id}: {exc}")
    finally:
        conn.close()


def queue_ai_review(task_id: int):
    """Dispatches AI review into a non-blocking background thread."""
    thread = threading.Thread(target=run_ai_review, args=(task_id,), daemon=True)
    thread.start()
