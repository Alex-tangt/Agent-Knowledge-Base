// API服务模块
import { config } from './config.js?v=4';
import { addMessage, updateMessage, completeMessageRender } from './messageHandler.js?v=4';
import { renderReferences } from './ragUI.js?v=4';

// 发送消息到后端API
export async function sendMessageToAPI(messages, loadingMessageId, useRag = true, viewName = 'documents', sessionId = '') {
    try {
        console.log('开始发送消息到API:', config.API_URL);
        console.log('消息内容:', messages);
        console.log('RAG模式:', useRag);
        console.log('View:', viewName);
        
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000);
        
        const response = await fetch(config.API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ messages: messages, use_rag: useRag, view_name: viewName, session_id: sessionId }),
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        console.log('API响应状态:', response.status);
        console.log('API响应头:', response.headers);

        if (!response.ok) {
            throw new Error(`API请求失败: ${response.status} ${response.statusText}`);
        }
        
        if (!response.body) {
            throw new Error('API响应没有body');
        }
        
        const reader = response.body.getReader();
        let aiResponse = '';
        let metadata = null;
        let retrievedDocs = null;
        
        // 处理流式响应
        async function processStream() {
            try {
                const { done, value } = await reader.read();
                console.log('读取流数据:', { done, value: value ? value.length : 0 });
                
                if (done) {
                    // 流结束
                    console.log('流结束，完整响应:', aiResponse);
                    // 完成消息渲染（使用markdown解析）
                    completeMessageRender(loadingMessageId, aiResponse);
                    
                    if (metadata) {
                        // 添加元数据信息
                        addMessage('system', `模型思考时间: ${metadata.thinking_time}秒 | 输入Token: ${metadata.input_tokens} | 输出Token: ${metadata.output_tokens} | 总Token: ${metadata.total_tokens}`);
                    }
                    
                    // 显示检索结果与来源引用（折叠卡片，挂在对应 AI 消息下）
                    if (metadata && metadata.sources) {
                        retrievedDocs = metadata.sources;
                    }
                    // ragUsed：本次是否走了知识库检索（metadata 含 sources 字段即视为 RAG 模式）
                    const ragUsed = !!(metadata && ('sources' in metadata));
                    if (ragUsed) {
                        const srcs = (retrievedDocs && retrievedDocs.length > 0)
                            ? retrievedDocs
                            : (metadata.sources || []);
                        renderReferences(loadingMessageId, srcs, true);
                    }
                    
                    return true;
                }
                
                // 处理收到的数据
                const chunk = new TextDecoder('utf-8').decode(value);
                console.log('收到的chunk:', chunk);
                const lines = chunk.split('\n').filter(line => line.trim());
                
                lines.forEach(line => {
                    try {
                        const data = JSON.parse(line);
                        console.log('解析的数据:', data);
                        
                        if (data.type === 'content') {
                            // 处理内容数据
                            aiResponse += data.content;
                            // 更新AI响应
                            updateMessage(loadingMessageId, aiResponse);
                            // 强制浏览器渲染
                            loadingMessageId.scrollIntoView({ behavior: 'smooth', block: 'end' });
                            
                            // 保存检索结果（结构化来源：文件名/相关度/片段）
                            if (data.sources) {
                                retrievedDocs = data.sources;
                            }
                        } else if (data.type === 'metadata') {
                            // 处理元数据
                            metadata = data;
                        } else if (data.type === 'error') {
                            // 处理错误
                            throw new Error(data.message);
                        }
                    } catch (error) {
                        console.error('解析流式数据失败:', error);
                    }
                });
                
                // 继续处理流
                return processStream();
            } catch (error) {
                console.error('处理流时出错:', error);
                throw error;
            }
        }
        
        // 开始处理流
        return await processStream();
    } catch (error) {
        console.error('发送消息到API时出错:', error);
        // 处理错误
        updateMessage(loadingMessageId, `错误: ${error.message}`);
        throw error;
    }
}

export async function uploadDocument(file, viewName = 'documents') {
    try {
        const formData = new FormData();
        formData.append('file', file);

        const url = new URL(config.API_URL.replace('/chat/stream', '/documents/upload'));
        url.searchParams.set('view_name', viewName);

        const response = await fetch(url.toString(), {
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

export async function getDocumentCount(viewName = 'documents') {
    try {
        const url = new URL(config.API_URL.replace('/chat/stream', '/documents/count'));
        url.searchParams.set('view_name', viewName);
        const response = await fetch(url.toString());

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

export async function clearDocuments(viewName = 'documents') {
    try {
        const url = new URL(config.API_URL.replace('/chat/stream', '/documents/clear'));
        url.searchParams.set('view_name', viewName);
        const response = await fetch(url.toString(), {
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

export async function listViews() {
    try {
        const url = config.API_URL.replace('/chat/stream', '/view/list');
        const response = await fetch(url);
        if (!response.ok) throw new Error('获取视图列表失败');
        return await response.json();
    } catch (error) {
        console.error('获取视图列表出错:', error);
        return [];
    }
}
