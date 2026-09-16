// RAG界面模块
import { getDocumentCount, listViews } from './apiService.js?v=4';

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
            // #37 命名迁移：ragMode 为 canonical；回退读取旧键 kbRagMode。
            const savedState = localStorage.getItem('ragMode') ?? localStorage.getItem('kbRagMode');
            if (savedState !== null) {
                ragToggle.checked = savedState === 'true';
            } else {
                ragToggle.checked = true;
            }
            ragToggle.addEventListener('change', (e) => {
                const ragMode = e.target.checked;
                localStorage.setItem('ragMode', ragMode.toString());
            });
        }
    }

    initViewSelector();
}

export function getRagMode() {
    const ragToggle = document.querySelector('#rag-controls input[type="checkbox"]');
    if (ragToggle) return ragToggle.checked;
    return true;
}

export function getSelectedView() {
    const select = document.getElementById('view-select');
    if (select) return select.value;
    return 'documents';
}

async function initViewSelector() {
    const select = document.getElementById('view-select');
    if (!select) return;

    // canonical = selectedView；回退读取旧键 kbSelectedKB。
    const savedView = localStorage.getItem('selectedView') ?? localStorage.getItem('kbSelectedKB');

    try {
        const viewList = await listViews();
        select.innerHTML = '';

        const autoOpt = document.createElement('option');
        autoOpt.value = 'auto';
        autoOpt.textContent = '🤖 自动选择';
        select.appendChild(autoOpt);

        viewList.forEach(view => {
            const opt = document.createElement('option');
            opt.value = view.name;
            opt.textContent = `📚 ${view.label}`;
            select.appendChild(opt);
        });

        if (savedView && select.querySelector(`option[value="${savedView}"]`)) {
            select.value = savedView;
        } else if (!savedView) {
            select.value = 'auto';
        }
    } catch (e) {
        console.error('加载视图列表失败:', e);
    }

    select.addEventListener('change', async () => {
        localStorage.setItem('selectedView', select.value);
        await refreshViewCount();
    });

    await refreshViewCount();
}

export async function refreshViewCount() {
    const viewName = getSelectedView();
    try {
        const count = await getDocumentCount(viewName);
        const info = document.getElementById('view-info');
        if (info) info.textContent = `共 ${count} 个文本片段`;
    } catch (e) {
        console.error('获取文档数量失败:', e);
    }
}
