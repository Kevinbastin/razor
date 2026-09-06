/* ==========================================================================
   ATRL Console — Application Logic
   ========================================================================== */

const API_BASE = '';
let state = { transactions: [], value: {}, policy: {}, system: {} };
let selectedId = null;
let a6Running = false;

/* --- Refs -------------------------------------------------------------- */
const $status = document.getElementById('api-status');
const $statusText = $status.querySelector('.status-text');
const $offlineBanner = document.getElementById('offline-banner');
const $feedBody = document.getElementById('feed-body');
const $detail = document.getElementById('detail');
const $stepper = document.getElementById('a6-stepper');
const $refreshDot = document.getElementById('refresh-indicator');

/* --- Helpers ----------------------------------------------------------- */
const esc = v => String(v ?? '').replace(/[&<>'"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[c]);

function parseCart(raw) {
    if (Array.isArray(raw)) return raw;
    if (typeof raw === 'string') {
        try { const p = JSON.parse(raw); if (Array.isArray(p)) return p; } catch {}
        return [raw];
    }
    return [];
}

function verdict(value) {
    const slug = String(value ?? '').replace(/\s+/g, '-').toLowerCase();
    return `<span class="v v-${esc(slug)}">${esc(value)}</span>`;
}

const money = v => `₹${Number(v || 0).toLocaleString('en-IN')}`;
const prettyJson = v => esc(JSON.stringify(v ?? {}, null, 2));

let _healthFails = 0;

async function checkHealth() {
    try {
        const r = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(3000) });
        if (!r.ok) throw Error();
        const d = await r.json();
        _healthFails = 0;
        $status.className = 'status-chip connected';
        $statusText.textContent = `v${d.version || '0.1.0'}`;
        $offlineBanner.hidden = true;
    } catch {
        _healthFails++;
        // Only show offline after 2 consecutive failures to avoid race on first load
        if (_healthFails >= 2) {
            $status.className = 'status-chip disconnected';
            $statusText.textContent = 'offline';
            $offlineBanner.hidden = false;
        }
    }
}

/* --- Load console data ------------------------------------------------- */
function filteredTransactions() {
    const q = document.getElementById('search').value.trim().toLowerCase();
    const l3 = document.getElementById('l3-filter').value;
    const st = document.getElementById('status-filter').value;
    return state.transactions.filter(t => {
        const text = `${t.id} ${parseCart(t.cart).join(' ')} ${t.packet?.mandate_snapshot?.mandate_id || ''}`.toLowerCase();
        if (q && !text.includes(q)) return false;
        if (l3 !== 'all' && t.l3 !== l3) return false;
        if (st !== 'all' && t.status !== st) return false;
        return true;
    });
}

async function loadConsole() {
    try {
        const r = await fetch(`${API_BASE}/api/console`);
        if (!r.ok) throw Error();
        state = await r.json();
        render();
        // Pulse the refresh indicator
        $refreshDot.classList.remove('active');
        void $refreshDot.offsetWidth; // reflow
        $refreshDot.classList.add('active');
    } catch (e) {
        console.warn('Console data load failed:', e);
    }
}

/* --- Render ------------------------------------------------------------ */
function render() {
    // Value counters
    const v = state.value || {};
    document.getElementById('auto-resolved').textContent = v.auto_resolved ?? '—';
    document.getElementById('fp-avoided').textContent = v.false_positives_avoided ?? '—';
    document.getElementById('defended').textContent = money(v.rupees_defended);

    // Transaction feed table
    const txns = filteredTransactions();
    $feedBody.innerHTML = txns.length
        ? txns.map(t => {
            const cart = parseCart(t.cart);
            const mandateId = t.packet?.mandate_snapshot?.mandate_id || '—';
            return `<tr data-id="${esc(t.id)}" class="${t.id === selectedId ? 'selected' : ''}" tabindex="0" aria-label="Inspect ${esc(t.id)}">
                <td class="cell-time">${esc(t.timestamp)}</td>
                <td class="cell-id" title="${esc(mandateId)}">${esc(mandateId.slice(-10))}</td>
                <td class="cell-amount">${money(t.amount)}</td>
                <td>${verdict(t.l1)}</td>
                <td>${verdict(t.l2)}</td>
                <td>${verdict(t.l3)}</td>
                <td>${verdict(t.status)}</td>
            </tr>`
        }).join('')
        : '<tr><td colspan="7" class="panel-empty">No transactions match these filters.</td></tr>';

    // Row click handlers
    $feedBody.querySelectorAll('tr[data-id]').forEach(row => {
        row.onclick = () => {
            const t = state.transactions.find(x => x.id === row.dataset.id);
            if (t) showDetail(t);
        };
        row.onkeydown = e => { if (e.key === 'Enter') row.click(); };
    });

    // Dispute queue
    const disputes = state.disputes || state.transactions.filter(t => t.l3 === 'flagged');
    document.getElementById('dispute-count').textContent = disputes.length;
    document.getElementById('dispute-queue').innerHTML = disputes.length
        ? disputes.map(t => `<div class="queue-item">
            <div>
                <div class="queue-item-id">${esc(t.id)}</div>
                <div class="queue-item-liability">${esc(t.packet?.liability_determination || '—')}</div>
            </div>
            <div class="queue-actions">
                <button class="btn btn-ghost btn-sm" data-packet="${esc(t.id)}">View</button>
                <button class="btn btn-ghost btn-sm" data-review="${esc(t.id)}">Review</button>
                <button class="btn btn-danger btn-sm" data-contest="${esc(t.id)}">Contest</button>
            </div>
        </div>`).join('')
        : '<p class="muted">No evidence-ready disputes.</p>';

    // Dispute button handlers
    document.querySelectorAll('[data-packet]').forEach(b =>
        b.onclick = () => showDetail(state.transactions.find(t => t.id === b.dataset.packet)));
    document.querySelectorAll('[data-review]').forEach(b =>
        b.onclick = () => review(b.dataset.review, 'request_more_evidence'));
    document.querySelectorAll('[data-contest]').forEach(b =>
        b.onclick = () => demoAction('transaction', b.dataset.contest, 'contest'));

    // Mandate list
    const mandates = state.mandates || [];
    document.getElementById('mandate-count').textContent = mandates.length;
    document.getElementById('mandate-list').innerHTML = mandates.length
        ? mandates.map(m => `<div class="mandate-item">
            <div class="mandate-info">
                <div class="mandate-id">${esc(m.mandate_id)}</div>
                <div class="mandate-purpose" title="${esc(m.purpose)}">${esc(m.purpose)}</div>
            </div>
            <div class="mandate-actions">
                ${verdict(m.state)}
                <button class="btn btn-ghost btn-sm" data-pause="${esc(m.mandate_id)}">Pause</button>
                <button class="btn btn-ghost btn-sm" data-resume="${esc(m.mandate_id)}">Resume</button>
                <button class="btn btn-danger btn-sm" data-revoke="${esc(m.mandate_id)}">Revoke</button>
            </div>
        </div>`).join('')
        : '<p class="muted">No mandates loaded.</p>';

    document.querySelectorAll('[data-pause]').forEach(b =>
        b.onclick = () => demoAction('mandate', b.dataset.pause, 'pause'));
    document.querySelectorAll('[data-resume]').forEach(b =>
        b.onclick = () => demoAction('mandate', b.dataset.resume, 'resume'));
    document.querySelectorAll('[data-revoke]').forEach(b =>
        b.onclick = () => demoAction('mandate', b.dataset.revoke, 'revoke'));

    renderPolicy();
    renderSystem();

    // Re-select the previously selected row
    if (selectedId) {
        const t = state.transactions.find(x => x.id === selectedId);
        if (t) showDetail(t);
    }
}

/* --- Detail / evidence panel ------------------------------------------- */
function showDetail(t) {
    if (!t) return;
    selectedId = t.id;

    // Update selected row styling
    $feedBody.querySelectorAll('tr').forEach(r =>
        r.classList.toggle('selected', r.dataset.id === t.id));

    const p = t.packet;
    const l1 = p.layer1 || {};
    const l2 = p.layer2 || {};
    const l3 = p.layer3 || {};
    const mandate = p.mandate_snapshot || {};
    const i1 = l3.evidence?.I1 || {};
    const i4 = l3.evidence?.I4 || {};
    const cart = parseCart(p.transaction?.cart_items || t.cart);

    const liabilityBorderColor = p.liability_determination === 'merchant-defensible'
        ? 'var(--accent-pass)' : 'var(--accent-amber)';

    $detail.innerHTML = `
        <div class="panel-header">
            <h2>Evidence trail</h2>
            <div class="detail-actions">
                <button class="btn btn-ghost btn-sm" id="copy-summary">Copy</button>
                <button class="btn btn-ghost btn-sm" id="export-json">JSON</button>
                <button class="btn btn-ghost btn-sm" id="print-packet">Print</button>
            </div>
        </div>

        <!-- Liability callout -->
        <div class="liability-callout" style="border-left-color:${liabilityBorderColor}">
            <strong>${esc(p.liability_determination)}</strong>
            <p>${esc(p.liability_reason || '')}</p>
        </div>

        <!-- Mandate context -->
        <div class="detail-section">
            <h3>Mandate context</h3>
            <dl class="kv-grid">
                <dt>Mandate</dt>    <dd>${esc(mandate.mandate_id || '—')}</dd>
                <dt>Purpose</dt>    <dd>${esc(mandate.purpose || i1.purpose || '—')}</dd>
                <dt>Ceiling</dt>    <dd>${money(mandate.amount_ceiling)} (${esc(mandate.amount_rule || '—')})</dd>
                <dt>State</dt>      <dd>${verdict(mandate.lifecycle_state || '—')}</dd>
                <dt>Cadence</dt>    <dd>${esc(mandate.cadence || '—')}</dd>
                <dt>Window</dt>     <dd>${esc(mandate.time_window_start_hour || '—')}:00 – ${esc(mandate.time_window_end_hour || '—')}:00</dd>
            </dl>
        </div>

        <!-- Transaction -->
        <div class="detail-section">
            <h3>Transaction</h3>
            <dl class="kv-grid">
                <dt>ID</dt>         <dd>${esc(t.id)}</dd>
                <dt>Amount</dt>     <dd>${money(t.amount)}</dd>
                <dt>Cart</dt>       <dd>${esc(cart.join(', '))}</dd>
                <dt>Category</dt>   <dd>${esc(p.transaction?.cart_category || '—')}</dd>
                <dt>Agent</dt>      <dd>${esc(p.transaction?.agent_type || '—')}</dd>
                <dt>Beneficiary</dt><dd>${esc(p.transaction?.beneficiary_id || '—')}</dd>
            </dl>
        </div>

        <!-- Layer 1 -->
        <div class="detail-section">
            <h3>Layer 1 — Mandate verifier ${verdict(l1.verdict)}</h3>
            ${l1.failed_checks?.length
                ? `<p class="muted" style="margin-bottom:4px">Failed: ${l1.failed_checks.map(c => `<span class="mono">${esc(c)}</span>`).join(', ')}</p>`
                : '<p class="muted">All 8 checks passed (V1–V8)</p>'}
            ${Object.keys(l1.evidence || {}).length ? `<pre class="evidence-pre">${prettyJson(l1.evidence)}</pre>` : ''}
        </div>

        <!-- Layer 2 -->
        <div class="detail-section">
            <h3>Layer 2 — Behavioural detector ${verdict(l2.verdict)}
                <span class="mono muted" style="margin-left:auto">${l2.risk_score != null ? Number(l2.risk_score).toFixed(4) : '—'}</span>
            </h3>
            ${(l2.top_risk_factors || []).length
                ? `<ul class="risk-factors">${(l2.top_risk_factors || []).map(f =>
                    `<li><strong>${esc(f.feature)}</strong>: ${esc(f.value ?? f.reason ?? '')}</li>`).join('')}</ul>`
                : '<p class="muted">No elevated risk factors</p>'}
        </div>

        <!-- Layer 3 -->
        <div class="detail-section">
            <h3>Layer 3 — Intent integrity ${verdict(l3.verdict)}</h3>
            ${l3.signals_triggered?.length
                ? `<p style="margin-bottom:6px">${l3.signals_triggered.map(verdict).join(' ')}</p>` : ''}
            ${i1.purpose ? `<dl class="kv-grid">
                <dt>Purpose</dt>        <dd>${esc(i1.purpose)}</dd>
                <dt>Cart</dt>           <dd>${esc(i1.cart_description || cart.join(', '))}</dd>
                <dt>Similarity</dt>     <dd>${i1.similarity_score != null ? Number(i1.similarity_score).toFixed(4) : '—'}</dd>
                <dt>Threshold</dt>      <dd>${i1.threshold != null ? Number(i1.threshold).toFixed(4) : '—'}</dd>
                <dt>Divergence</dt>     <dd>${i1.distance != null ? Number(i1.distance).toFixed(4) : '—'}</dd>
            </dl>` : ''}
            ${i4.triggered != null ? `<dl class="kv-grid" style="margin-top:6px">
                <dt>I4 — Escalation triple</dt> <dd>${i4.triggered ? verdict('flagged') : verdict('clear')}</dd>
                <dt>First-time beneficiary</dt> <dd>${esc(i4.first_time_beneficiary)}</dd>
                <dt>High value</dt>             <dd>${esc(i4.upper_quartile_value)}</dd>
                <dt>Off-pattern timing</dt>     <dd>${esc(i4.timing_deviates_from_pattern)}</dd>
            </dl>` : ''}
            ${renderProvenance(l3.evidence)}
        </div>

        <!-- Session timeline -->
        <div class="detail-section">
            <h3>Session timeline</h3>
            ${renderTimeline(p.session_timeline)}
        </div>

        <!-- Narrative -->
        <div class="detail-section">
            <h3>Narrative</h3>
            <p class="muted">${esc(p.narrative?.narrative || 'Not generated — structured evidence and audited prompt are retained.')}</p>
        </div>

        <!-- Escalation action -->
        ${p.liability_determination === 'escalate-to-provider' ? `
        <div class="escalate-action">
            <button class="btn btn-primary" onclick="demoAction('transaction','${esc(t.id)}','escalate')">Escalate to AI provider</button>
        </div>` : ''}
    `;

    // Wire up action buttons
    document.getElementById('copy-summary').onclick = () =>
        navigator.clipboard.writeText(`${t.id}: ${p.liability_determination}. ${p.liability_reason || ''}`)
            .then(() => { const b = document.getElementById('copy-summary'); b.textContent = 'Copied'; setTimeout(() => b.textContent = 'Copy', 1200); });
    document.getElementById('export-json').onclick = () => exportPacket(p, t.id);
    document.getElementById('print-packet').onclick = () => window.print();
}

function renderTimeline(events) {
    if (!events?.length) return '<p class="muted">No session events in this packet.</p>';
    return `<ol class="timeline">${events.map(e =>
        `<li><strong>${esc(e.step_name || e.event_type || 'event')}</strong><small>${esc(e.timestamp || e.occurred_at || '')}</small></li>`
    ).join('')}</ol>`;
}

function renderProvenance(evidence) {
    const i2 = evidence?.I2;
    if (!i2) return '';
    const exposures = i2.exposures || i2.suspicious_exposures || i2.immediate_exposures || [];
    return `
        <h3 style="margin-top:8px">Source provenance ${i2.triggered ? verdict('flagged') : verdict('clear')}</h3>
        ${exposures.length ? `<div class="provenance-box">${exposures.map(x =>
            `<p><strong>${esc(x.domain || x.url || 'source')}</strong><br>
            <small>Newly seen: ${esc(x.newly_seen)} — Reputation: ${esc(x.reputation)} — ${esc(x.seconds_before_transaction ?? '')}s before payment</small></p>`
        ).join('')}</div>` : '<p class="muted">No suspicious source exposure recorded.</p>'}`;
}

function exportPacket(packet, id) {
    const blob = new Blob([JSON.stringify(packet, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${id}-evidence-packet.json`;
    link.click();
    URL.revokeObjectURL(url);
}

/* --- Policy ------------------------------------------------------------ */
function renderPolicy() {
    const p = state.policy || {};
    document.getElementById('i1-threshold').value = p.i1_threshold ?? '';
    document.getElementById('l2-suspicious').value = p.layer2_suspicious_threshold ?? '';
    document.getElementById('l2-attack').value = p.layer2_attack_threshold ?? '';
    document.getElementById('policy-version').textContent = p.version != null
        ? `Policy v${p.version}` : '';
}

/* --- System health ----------------------------------------------------- */
function renderSystem() {
    const s = state.system || {};
    const labels = {
        model_version: 'Model version',
        data_snapshot_version: 'Data snapshot',
        webhook_configured: 'Webhook health',
        razorpay_circuit: 'Razorpay circuit',
        audit_ledger: 'Audit chain',
        console_mode: 'Console mode',
    };
    const el = document.getElementById('system-health');
    if (!Object.keys(s).length) {
        el.innerHTML = '<p class="muted">No system data available.</p>';
        return;
    }
    el.innerHTML = Object.entries(s).map(([key, value]) => {
        const label = labels[key] || key.replace(/_/g, ' ');
        const isOk = value === true || value === 'keyed' || value === 'healthy';
        const isBad = value === false || value === 'not-instantiated';
        let valClass = '';
        if (isOk) valClass = ' style="color:var(--accent-pass)"';
        else if (isBad) valClass = ' style="color:var(--text-muted)"';
        return `<div class="health-row">
            <span class="health-key">${esc(label)}</span>
            <span class="health-val"${valClass}>${esc(value)}</span>
        </div>`;
    }).join('');
}

/* --- A6 stepper animation ---------------------------------------------- */
async function runA6() {
    if (a6Running) return;
    a6Running = true;

    const txn = state.transactions.find(t => t.l3 === 'flagged');
    if (!txn) { a6Running = false; return; }

    // Select the flagged row
    showDetail(txn);

    const row = $feedBody.querySelector(`tr[data-id="${txn.id}"]`);

    // Reset all steps
    document.querySelectorAll('.step').forEach(s => {
        s.className = 'step';
        s.querySelector('.step-result').textContent = '—';
    });

    const steps = [
        { n: 1, result: 'pass — authority intact',   cls: 'done-pass' },
        { n: 2, result: 'pass — behaviour normal',   cls: 'done-pass' },
        { n: 3, result: 'flagged — intent diverges',  cls: 'done-flag' },
        { n: 4, result: 'escalate to provider',       cls: 'done-flag' },
    ];

    for (const step of steps) {
        const el = document.querySelector(`.step[data-step="${step.n}"]`);
        el.classList.add('active');
        if (row) row.classList.add('scenario-active');

        await new Promise(r => setTimeout(r, 1100));

        el.classList.remove('active');
        el.classList.add(step.cls);
        el.querySelector('.step-result').textContent = step.result;
        if (row) row.classList.remove('scenario-active');
    }

    // Highlight the row as selected after the animation
    if (row) row.classList.add('selected');
    a6Running = false;
}

/* --- Demo actions ------------------------------------------------------ */
async function demoAction(targetType, targetId, action) {
    if (!confirm(`Apply "${action}" to ${targetId}? This is a local demo action — no Razorpay call will be made.`))
        return;
    try {
        const r = await fetch('/api/demo/actions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_type: targetType, target_id: targetId, action })
        });
        if (!r.ok) throw new Error((await r.json()).detail);
        await loadConsole();
    } catch (e) { alert(e.message); }
}

async function review(id, decision) {
    const owner = prompt('Reviewer name:', 'risk-analyst');
    if (owner == null) return;
    const note = prompt('Note (recorded in audit log):') ?? '';
    try {
        const r = await fetch(`/api/reviews/${encodeURIComponent(id)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ decision, note: `owner=${owner}; ${note}` })
        });
        if (!r.ok) throw new Error((await r.json()).detail);
    } catch (e) { alert(e.message); }
}

async function savePolicy(event) {
    event.preventDefault();
    const body = {
        i1_threshold: Number(document.getElementById('i1-threshold').value),
        layer2_suspicious_threshold: Number(document.getElementById('l2-suspicious').value),
        layer2_attack_threshold: Number(document.getElementById('l2-attack').value),
    };
    if (body.layer2_suspicious_threshold >= body.layer2_attack_threshold) {
        return alert('Suspicious threshold must be below attack threshold.');
    }
    const reason = prompt('Reason for this policy change:');
    if (reason === null) return;
    try {
        const r = await fetch('/v1/policies/current', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error((await r.json()).detail);
        state.policy = await r.json();
        renderPolicy();
    } catch (e) { alert(e.message); }
}

async function resetDemo() {
    if (!confirm('Reset the local demo state? Audit records are preserved.')) return;
    const r = await fetch('/api/demo/reset', { method: 'POST' });
    if (!r.ok) return alert((await r.json()).detail);
    selectedId = null;
    // Reset stepper to initial state
    document.querySelectorAll('.step').forEach(s => {
        s.className = 'step';
        s.querySelector('.step-result').textContent = '—';
    });
    await loadConsole();
}

/* --- Wiring ------------------------------------------------------------ */
document.getElementById('run-a6').onclick = runA6;
document.getElementById('refresh').onclick = loadConsole;
document.getElementById('reset-demo').onclick = resetDemo;
document.getElementById('policy-form').onsubmit = savePolicy;

['search', 'l3-filter', 'status-filter'].forEach(id =>
    document.getElementById(id).addEventListener(id === 'search' ? 'input' : 'change', render));

/* --- Bootstrap --------------------------------------------------------- */
checkHealth();
loadConsole();
setInterval(checkHealth, 10000);
setInterval(loadConsole, 5000);
