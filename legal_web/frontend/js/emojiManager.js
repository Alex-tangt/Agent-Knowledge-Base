// 表情管理模块

// 初始化表情选择窗口
export function initEmojiWindow() {
    const emojiGrid = document.getElementById('emoji-grid');
    const emojis = ["😊", "😂", "😍", "😢", "😡", "👍", "👎", "🎉", "🔥", "🤔", "🤣", "😎"];
    
    emojis.forEach(emoji => {
        const button = document.createElement('button');
        button.textContent = emoji;
        button.onclick = () => {
            document.getElementById('input-entry').value += emoji;
            document.getElementById('emoji-window').style.display = 'none';
        };
        emojiGrid.appendChild(button);
    });
}

// 显示表情选择窗口
export function showEmojis() {
    const emojiWindow = document.getElementById('emoji-window');
    emojiWindow.style.display = emojiWindow.style.display === 'none' ? 'block' : 'none';
}

// 初始化表情窗口事件
export function initEmojiEvents() {
    // 点击页面其他地方关闭表情窗口
    document.addEventListener('click', function(event) {
        const emojiWindow = document.getElementById('emoji-window');
        const emojiButton = document.querySelector('.emoji-button');
        
        if (!emojiWindow.contains(event.target) && event.target !== emojiButton) {
            emojiWindow.style.display = 'none';
        }
    });
}
