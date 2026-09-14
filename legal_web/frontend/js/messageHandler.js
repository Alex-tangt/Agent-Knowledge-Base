// 消息处理模块

// 添加消息到聊天历史
export function addMessage(role, content) {
    const chatHistory = document.getElementById('chat-history');
    const messageDiv = document.createElement('div');
    
    if (role === 'user') {
        messageDiv.className = 'message user-message';
        messageDiv.innerHTML = `
            <div class="sender">You</div>
            <div class="content">${content}</div>
        `;
    } else if (role === 'assistant') {
        messageDiv.className = 'message ai-message';
        // 初始内容（不解析markdown，用于流式输出）；预留参考来源容器
        messageDiv.innerHTML = `
            <div class="sender">AI</div>
            <div class="content">${content}</div>
            <div class="references"></div>
        `;
    } else {
        messageDiv.className = 'message system-message';
        messageDiv.innerHTML = `
            <div class="sender">系统</div>
            <div class="content">${content}</div>
        `;
    }
    
    chatHistory.appendChild(messageDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;
    
    // 返回消息元素的引用，用于后续更新
    return messageDiv;
}

// 更新消息内容
export function updateMessage(messageElement, content) {
    if (messageElement && messageElement.classList.contains('ai-message')) {
        const contentDiv = messageElement.querySelector('.content');
        if (contentDiv) {
            // 直接更新内容，不使用markdown解析（用于流式输出）
            contentDiv.textContent = content;
            const chatHistory = document.getElementById('chat-history');
            chatHistory.scrollTop = chatHistory.scrollHeight;
            // 强制重排
            contentDiv.offsetHeight;
        }
    }
}

// 完成消息渲染（使用markdown解析）
export function completeMessageRender(messageElement, content) {
    if (messageElement && messageElement.classList.contains('ai-message')) {
        const contentDiv = messageElement.querySelector('.content');
        if (contentDiv) {
            // 使用marked库解析markdown
            const parsedContent = marked.parse(content);
            contentDiv.innerHTML = parsedContent;
            const chatHistory = document.getElementById('chat-history');
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }
    }
}

// 清除聊天历史
export function clearHistory() {
    if (confirm('确定要清除聊天历史吗？')) {
        const chatHistory = document.getElementById('chat-history');
        chatHistory.innerHTML = '';
        // 注意：messages变量在主脚本中定义
        window.messages = [];
        addMessage('system', '聊天历史已清除');
    }
}
