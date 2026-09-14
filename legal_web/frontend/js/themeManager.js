// 主题管理模块
import { config } from './config.js';

let theme = config.defaultTheme;

// 初始化主题
export function initTheme() {
    // 检查本地存储中的主题设置
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) {
        theme = savedTheme;
    }
    applyTheme();
}

// 应用主题
function applyTheme() {
    document.body.className = theme;
    document.getElementById('theme-button').textContent = theme === 'light' ? '🌙' : '☀️';
    // 保存主题设置到本地存储
    localStorage.setItem('theme', theme);
}

// 切换主题
export function toggleTheme() {
    theme = theme === 'light' ? 'dark' : 'light';
    applyTheme();
}

// 获取当前主题
export function getCurrentTheme() {
    return theme;
}
