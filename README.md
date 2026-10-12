# 🎙️ TranscribeHub - Video Task & Transcription Platform

A crowdsourced video transcription and task marketplace. You (the Admin) are the sole publisher of video tasks and pricing. Workers sign up and pay an entry fee (KES 2,500) to activate their account before they can claim tasks and transcribe them.

---

## 🌟 How the Platform Works

### 1. 👑 Admin Role (Only You Can Post & Price)
- **Login**: Go to `/admin/login` (Default username: `admin`, password: `transcribehub2024`).
- **Post Video Tasks (`/tasks/new`)**:
  - Upload video/audio files (`.mp4`, `.webm`, `.mov`, `.mp3`, `.wav`) or provide a direct video/YouTube URL.
  - Set your custom **bounty reward ($ USD)**, turnaround deadline, audio difficulty, and instructions.
- **Admin Dashboard (`/admin`)**:
  - Live overview of all tasks (Open, Claimed, Needs Review, Completed).
  - Track **Registered Workers** and their activation status (Paid / Unpaid).
  - Track total **Entry Fee Payments** collected (KES 2,500 each).
- **Quality Review (`/tasks/<id>/review`)**:
  - Synchronized side-by-side review with interactive clickable timestamps.
  - Approve & payout, request revisions, or reject.
  - Export completed subtitles in **.SRT**, **.VTT**, **.TXT**, and **.JSON**.

---

### 2. 👥 Transcriber / Worker Flow (Free to Browse, Pay to Work)
- **Free Browsing**: Anyone can visit the site (`/`), explore video tasks, watch media previews, and read guidelines without paying.
- **Sign Up & Log In**: Workers create an account with their name, email, phone number, and password.
- **Entry Fee Gate (KES 2,500 via Paystack)**:
  - Workers must pay the **KES 2,500** entry fee to activate their account before they can claim tasks.
  - Payments are processed through **Paystack**, supporting **M-Pesa**, **Credit/Debit Card (Visa & Mastercard)**, and **Bank Transfer**.
  - Includes real-time Paystack popup checkout, automated callback & reference verification.
  - Admin can update Paystack Public & Secret keys directly from `/admin`.
- **Pro Transcription Studio (`/tasks/<id>/studio`)**:
  - Synchronized video playback with speed controls, 3s jumps, and auto-pause while typing.
  - Dual modes: Subtitle Segments mode (timed cards) or Continuous Verbatim text.
  - Auto-saving draft system and submission for client review.

---

## 🚀 Running the Application

```bash
cd /home/alphakipkorir/Desktop/transcribers
./run.sh
```
*(Or `python3 app.py`)*

Open your browser at **`http://127.0.0.1:5000`**.

### 🔑 Admin Credentials
- **URL**: `http://127.0.0.1:5000/admin/login`
- **Username**: `admin`
- **Password**: `transcribehub2024` *(Can be changed anytime from the Admin Dashboard)*
