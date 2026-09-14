// 文档管理模块
import { config } from './config.js';

// 文档上传函数
export async function uploadDocument(file) {
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch(`${config.API_URL.replace('/chat/stream', '')}/documents/upload`, {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            throw new Error(`上传失败: ${response.status} ${response.statusText}`);
        }
        
        const result = await response.json();
        console.log('文档上传成功:', result);
        return result;
    } catch (error) {
        console.error('上传文档时出错:', error);
        throw error;
    }
}

// 获取文档数量
export async function getDocumentCount() {
    try {
        const response = await fetch(`${config.API_URL.replace('/chat/stream', '')}/documents/count`);
        
        if (!response.ok) {
            throw new Error(`获取文档数量失败: ${response.status} ${response.statusText}`);
        }
        
        const result = await response.json();
        console.log('文档数量:', result);
        return result.count;
    } catch (error) {
        console.error('获取文档数量时出错:', error);
        throw error;
    }
}

// 清空所有文档
export async function clearDocuments() {
    try {
        const response = await fetch(`${config.API_URL.replace('/chat/stream', '')}/documents/clear`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            throw new Error(`清空文档失败: ${response.status} ${response.statusText}`);
        }
        
        const result = await response.json();
        console.log('清空文档成功:', result);
        return result;
    } catch (error) {
        console.error('清空文档时出错:', error);
        throw error;
    }
}

// 初始化文档管理界面
export function initDocumentManager() {
    // 获取文档上传区域
    const uploadArea = document.getElementById('document-upload');
    if (!uploadArea) return;
    
    // 获取上传按钮
    const uploadButton = uploadArea.querySelector('button');
    const fileInput = uploadArea.querySelector('input[type="file"]');
    const statusElement = uploadArea.querySelector('.upload-status');
    
    // 上传按钮点击事件
    uploadButton.addEventListener('click', async () => {
        if (!fileInput.files || fileInput.files.length === 0) {
            if (statusElement) {
                statusElement.textContent = '请选择一个文件';
                statusElement.style.color = 'orange';
            }
            return;
        }
        
        const file = fileInput.files[0];
        
        if (statusElement) {
            statusElement.textContent = '正在上传...';
            statusElement.style.color = 'blue';
        }
        
        try {
            const result = await uploadDocument(file);
            if (statusElement) {
                statusElement.textContent = `上传成功！文档分块数: ${result.chunks}`;
                statusElement.style.color = 'green';
            }
            
            // 清空文件输入
            fileInput.value = '';
            
            // 更新文档数量
            updateDocumentCount();
        } catch (error) {
            if (statusElement) {
                statusElement.textContent = `上传失败: ${error.message}`;
                statusElement.style.color = 'red';
            }
        }
    });
    
    // 清空文档按钮点击事件
    const clearButton = uploadArea.querySelector('.clear-documents');
    if (clearButton) {
        clearButton.addEventListener('click', async () => {
            if (!confirm('确定要清空所有文档吗？')) {
                return;
            }
            
            if (statusElement) {
                statusElement.textContent = '正在清空...';
                statusElement.style.color = 'blue';
            }
            
            try {
                await clearDocuments();
                if (statusElement) {
                    statusElement.textContent = '清空成功！';
                    statusElement.style.color = 'green';
                }
                
                // 更新文档数量
                updateDocumentCount();
            } catch (error) {
                if (statusElement) {
                    statusElement.textContent = `清空失败: ${error.message}`;
                    statusElement.style.color = 'red';
                }
            }
        });
    }
    
    // 初始化文档数量
    updateDocumentCount();
}

// 更新文档数量
export async function updateDocumentCount() {
    try {
        const count = await getDocumentCount();
        const countElement = document.getElementById('document-count');
        if (countElement) {
            countElement.textContent = `当前文档数量: ${count}`;
        }
    } catch (error) {
        console.error('更新文档数量时出错:', error);
    }
}