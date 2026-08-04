// 主脚本文件
import { initTheme, toggleTheme } from './js/themeManager.js?v=3';
import { initEmojiWindow, showEmojis, initEmojiEvents } from './js/emojiManager.js?v=3';
import { addMessage, updateMessage, clearHistory } from './js/messageHandler.js?v=3';
import { sendMessageToAPI, uploadDocument as apiUploadDocument, clearDocuments as apiClearDocuments } from './js/apiService.js?v=3';
import { initDocumentManager } from './js/documentManager.js?v=3';
import { initRagUI, getRagMode, getSelectedKB, refreshKBCount } from './js/ragUI.js?v=3';
import { config } from './js/config.js?v=3';

let messages = [];
let modelsReady = false;
let currentSessionId = 'session_' + Date.now();

function init() {
    const safe = (name, fn) => {
        try { fn(); } catch (e) { console.error(`初始化 ${name} 失败:`, e); }
    };
    safe('主题', initTheme);
    safe('表情窗口', initEmojiWindow);
    safe('表情事件', initEmojiEvents);
    safe('文档管理', initDocumentManager);
    safe('RAG界面', initRagUI);
    safe('侧边抽屉', initSidebar);
    safe('模型状态', checkModelStatus);
}

function openSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    if (sidebar) sidebar.classList.add('open');
    if (overlay) overlay.classList.add('open');
}
function closeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('open');
}
function initSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('overlay');
    if (!sidebar || !overlay) return;
    const menuBtn = document.getElementById('menu-btn');
    const closeBtn = document.getElementById('close-btn');
    if (menuBtn) menuBtn.addEventListener('click', openSidebar);
    if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
    overlay.addEventListener('click', closeSidebar);
}

function updateStep(id, status) {
    const el = document.getElementById(id);
    if (!el) return;
    if (status === 'ready') {
        el.classList.add('done');
        el.classList.remove('loading');
        el.querySelector('.step-icon').textContent = '✅';
    } else if (status === 'loading') {
        el.classList.add('loading');
        el.classList.remove('done');
        el.querySelector('.step-icon').textContent = '⏳';
    }
}

async function checkModelStatus() {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return;

    const tryPoll = async () => {
        try {
            const resp = await fetch(config.STATUS_URL);
            const data = await resp.json();
            updateStep('step-embedding', data.embedding);
            updateStep('step-reranker', data.reranker);
            if (data.ready) {
                modelsReady = true;
                setTimeout(() => overlay.classList.add('hidden'), 500);
                document.getElementById('input-entry').disabled = false;
                document.querySelector('.send-button').disabled = false;
                document.querySelector('.emoji-button').disabled = false;
                addMessage('system', '模型加载完成，知识库问答助手已就绪');
                return;
            }
        } catch (e) {
            console.error('检查模型状态失败:', e);
        }
        setTimeout(tryPoll, 1000);
    };

    tryPoll();
}

async function sendMessage() {
    if (!modelsReady) return;
    const inputEntry = document.getElementById('input-entry');
    const message = inputEntry.value.trim();

    if (!message) return;

    if (message === 'exit') {
        if (confirm('确定要退出吗？')) {
            window.close();
        }
        return;
    }

    inputEntry.value = '';
    addMessage('user', message);
    messages.push({ role: 'user', content: message });

    inputEntry.disabled = true;
    document.querySelector('.send-button').disabled = true;
    document.querySelector('.emoji-button').disabled = true;

    const loadingMessageId = addMessage('assistant', '<div class="loading"></div>');

    try {
        const useRag = getRagMode();
        const kbName = getSelectedKB();
        await sendMessageToAPI(messages, loadingMessageId, useRag, kbName, currentSessionId);
    } catch (error) {
        console.error('发送消息失败:', error);
    } finally {
        inputEntry.disabled = false;
        document.querySelector('.send-button').disabled = false;
        document.querySelector('.emoji-button').disabled = false;
        inputEntry.focus();
    }
}

// 上传文档
async function uploadDocument() {
    const fileInput = document.getElementById('file-input');
    const file = fileInput.files[0];
    
    if (!file) {
        alert('请选择一个文件');
        return;
    }
    
    const statusElement = document.querySelector('.upload-status');
    if (statusElement) {
        statusElement.textContent = '正在上传...';
        statusElement.style.color = 'blue';
    }
    
    try {
        const kbName = getSelectedKB();
        const result = await apiUploadDocument(file, kbName);
        if (statusElement) {
            statusElement.textContent = `上传成功！文档分块数: ${result.chunks}`;
            statusElement.style.color = 'green';
        }
        
        fileInput.value = '';
        await refreshKBCount();
    } catch (error) {
        if (statusElement) {
            statusElement.textContent = `上传失败: ${error.message}`;
            statusElement.style.color = 'red';
        }
    }
}

async function clearDocuments() {
    if (!confirm('确定要清空所有文档吗？')) {
        return;
    }
    
    const statusElement = document.querySelector('.upload-status');
    if (statusElement) {
        statusElement.textContent = '正在清空...';
        statusElement.style.color = 'blue';
    }
    
    try {
        const kbName = getSelectedKB();
        await apiClearDocuments(kbName);
        if (statusElement) {
            statusElement.textContent = '清空成功！';
            statusElement.style.color = 'green';
        }
        
        await refreshKBCount();
    } catch (error) {
        if (statusElement) {
            statusElement.textContent = `清空失败: ${error.message}`;
            statusElement.style.color = 'red';
        }
    }
}

// 暴露全局函数
window.sendMessage = sendMessage;
window.toggleTheme = toggleTheme;
window.openSidebar = openSidebar;
window.closeSidebar = closeSidebar;
window.showEmojis = showEmojis;
window.clearHistory = clearHistory;
window.uploadDocument = uploadDocument;
window.clearDocuments = clearDocuments;

// 初始化应用
init();