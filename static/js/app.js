/**
 * TranscribeHub Global UI Scripts
 */

// Toast notification trigger
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    const colorClass = type === 'success' 
        ? 'bg-emerald-600 text-white' 
        : type === 'error' 
        ? 'bg-rose-600 text-white' 
        : type === 'warning'
        ? 'bg-amber-600 text-white'
        : 'bg-slate-800 text-slate-100 border border-slate-700';

    toast.className = `toast px-4 py-3 rounded-xl shadow-xl flex items-center gap-3 text-sm font-medium ${colorClass}`;
    
    let icon = '🔔';
    if (type === 'success') icon = '✓';
    if (type === 'error') icon = '✕';
    if (type === 'warning') icon = '⚠️';

    toast.innerHTML = `
        <span class="text-base">${icon}</span>
        <span>${message}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// Copy text to clipboard helper
function copyToClipboard(text, successMsg = 'Copied to clipboard!') {
    navigator.clipboard.writeText(text).then(() => {
        showToast(successMsg, 'success');
    }).catch(err => {
        showToast('Failed to copy', 'error');
    });
}

// User switcher dropdown handler
document.addEventListener('DOMContentLoaded', () => {
    const userMenuBtn = document.getElementById('user-menu-btn');
    const userDropdown = document.getElementById('user-dropdown');

    if (userMenuBtn && userDropdown) {
        userMenuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            userDropdown.classList.toggle('hidden');
        });

        document.addEventListener('click', () => {
            if (!userDropdown.classList.contains('hidden')) {
                userDropdown.classList.add('hidden');
            }
        });
    }

    // Auto-dismiss flask alerts after 5 seconds
    const alerts = document.querySelectorAll('.flash-alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.5s ease';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });
});

// Password visibility toggle helper
function togglePasswordVisibility(inputId, buttonEl) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';

    if (buttonEl) {
        const eyeOpen = buttonEl.querySelector('.eye-open');
        const eyeClosed = buttonEl.querySelector('.eye-closed');
        if (eyeOpen && eyeClosed) {
            if (isPassword) {
                eyeOpen.classList.add('hidden');
                eyeClosed.classList.remove('hidden');
            } else {
                eyeOpen.classList.remove('hidden');
                eyeClosed.classList.add('hidden');
            }
        }
    }
}

