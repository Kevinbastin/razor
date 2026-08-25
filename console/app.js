/**
 * ATRL Console — Application Logic
 *
 * Connects to the FastAPI backend, polls health status,
 * and will later stream transaction decisions.
 */

const API_BASE = 'http://localhost:8000';

// DOM references
const apiStatus = document.getElementById('api-status');
const statusText = apiStatus.querySelector('.status-text');

/**
 * Check API health and update status badge.
 */
async function checkHealth() {
    try {
        const response = await fetch(`${API_BASE}/health`, {
            signal: AbortSignal.timeout(3000),
        });
        if (response.ok) {
            const data = await response.json();
            apiStatus.className = 'status-badge connected';
            statusText.textContent = `API ${data.version || 'Connected'}`;
        } else {
            setDisconnected();
        }
    } catch {
        setDisconnected();
    }
}

function setDisconnected() {
    apiStatus.className = 'status-badge disconnected';
    statusText.textContent = 'API Offline';
}

// Poll health every 10 seconds
checkHealth();
setInterval(checkHealth, 10000);

console.log(
    '%c◆ ATRL Console v0.1.0',
    'color: #6385ff; font-size: 14px; font-weight: bold;'
);
