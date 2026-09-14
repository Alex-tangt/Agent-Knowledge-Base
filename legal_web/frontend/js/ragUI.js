// RAG界面模块
import { getDocumentCount, listKnowledgeBases } from './apiService.js?v=3';

function _scoreClass(dist) {
    if (dist === null || dist === undefined) return '';
    if (dist < 0.35) return 'green';
    if (dist < 0.60) return 'yellow';
    return 'red';
}

function _scoreLabel(dist) {
    if (dist === null || dist === undefined) return '—';
    return Number(dist).toFixed(3);
}

export function renderReferences(messageElement, sources, ragUsed = false) {
    if (!messageElement) return;
    const container = messageElement.querySelector('.references');
    if (!container) return;

    container.innerHTML = '';

    if (!ragUsed) return;

    if (!sources || sources.length === 0) {
        container.innerHTML = '<div class="ref-empty">📎 参考来源：本次回答未检索到相关资料</div>';
        return;
    }

    const details = document.createElement('details');
    details.open = true;

    const summary = document.createElement('summary');
    summary.textContent = `📎 参考来源 (${sources.length})`;
    details.appendChild(summary);

    const list = document.createElement('div');
    list.className = 'ref-list';

    sources.forEach((s, index) => {
        const sourceName = s.source || ('文档 ' + (s.chunk_id || (index + 1)));
        const score = _scoreLabel(s.score);
        const cls = _scoreClass(s.score);
        const content = (s.content || '').trim();

        const card = document.createElement('div');
        card.className = 'ref-card';
        card.innerHTML = `
            <div class="ref-head">
                <div class="ref-title">📄 <span>${sourceName}</span></div>
                <div class="ref-score ${cls}">相关度 ${score}</div>
            </div>
            <div class="ref-body"><p>${content}</p></div>
        `;
        card.querySelector('.ref-head').addEventListener('click', () => {
            card.classList.toggle('open');
        });
        list.appendChild(card);
    });

    details.appendChild(list);
    container.appendChild(details);
}

export function hideRetrievedDocs() {
    const ragResults = document.getElementById('rag-results');
    if (ragResults) {
        ragResults.innerHTML = '';
    }
}

export function initRagUI() {
    const ragControls = document.getElementById('rag-controls');
    if (ragControls) {
        const ragToggle = ragControls.querySelector('input[type="checkbox"]');
        if (ragToggle) {
            const savedState = localStorage.getItem('kbRagMode');
            if (savedState !== null) {
                ragToggle.checked = savedState === 'true';
            } else {
                ragToggle.checked = true;
            }
            ragToggle.addEventListener('change', (e) => {
                const ragMode = e.target.checked;
                localStorage.setItem('kbRagMode', ragMode.toString());
            });
        }
    }

    initKBSelector();
}

export function getRagMode() {
    const ragToggle = document.querySelector('#rag-controls input[type="checkbox"]');
    if (ragToggle) return ragToggle.checked;
    return true;
}

export function getSelectedKB() {
    const select = document.getElementById('kb-select');
    if (select) return select.value;
    return 'documents';
}

async function initKBSelector() {
    const select = document.getElementById('kb-select');
    if (!select) return;

    const savedKB = localStorage.getItem('kbSelectedKB');

    try {
        const kbList = await listKnowledgeBases();
        select.innerHTML = '';

        const autoOpt = document.createElement('option');
        autoOpt.value = 'auto';
        autoOpt.textContent = '🤖 自动选择';
        select.appendChild(autoOpt);

        kbList.forEach(kb => {
            const opt = document.createElement('option');
            opt.value = kb.name;
            opt.textContent = `📚 ${kb.label}`;
            select.appendChild(opt);
        });

        if (savedKB && select.querySelector(`option[value="${savedKB}"]`)) {
            select.value = savedKB;
        } else if (!savedKB) {
            select.value = 'auto';
        }
    } catch (e) {
        console.error('加载知识库列表失败:', e);
    }

    select.addEventListener('change', async () => {
        localStorage.setItem('kbSelectedKB', select.value);
        await refreshKBCount();
    });

    await refreshKBCount();
}

export async function refreshKBCount() {
    const kbName = getSelectedKB();
    try {
        const count = await getDocumentCount(kbName);
        const info = document.getElementById('kb-info');
        if (info) info.textContent = `共 ${count} 个文本片段`;
    } catch (e) {
        console.error('获取文档数量失败:', e);
    }
}
