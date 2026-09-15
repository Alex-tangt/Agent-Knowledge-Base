import os
import functools
import time
from typing import Optional, Dict, Any, Callable
from ragcore.utils.logger import logger

class LangSmithService:
    """LangSmith监控服务类。

    配置**惰性读取**（#22 / ADR-0016）：不再在 import 期读 env——否则 `memory_agent`
    一 import `vector_store_service`（它挂了本服务的 trace 装饰器）就会连带读
    `legal_web/.env`。改为首次 `is_enabled` / `client` 访问时才读。
    """
    
    _instance = None
    _client = None
    _initialized = False
    _tracing = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LangSmithService, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        # 不在这里初始化 client：配置读取延到首次使用（见类 docstring）。
        pass

    def _ensure_initialized(self):
        if self._initialized:
            return
        self._initialized = True
        from ragcore.config.llm import langsmith_settings
        settings = langsmith_settings()
        self._tracing = settings["tracing"]
        self._initialize_client(settings)

    def _initialize_client(self, settings):
        """初始化LangSmith客户端"""
        try:
            if not settings["tracing"]:
                logger.info("LangSmith tracing is disabled")
                return
            
            api_key = settings["api_key"]
            if not api_key:
                logger.warning("LANGSMITH_API_KEY not set, LangSmith tracing will be disabled")
                return
            
            # 设置环境变量供langsmith库使用
            os.environ["LANGCHAIN_API_KEY"] = api_key
            os.environ["LANGCHAIN_PROJECT"] = settings["project"]
            os.environ["LANGCHAIN_ENDPOINT"] = settings["endpoint"]
            os.environ["LANGCHAIN_TRACING"] = "true"
            
            # 延迟导入，避免在没有安装langsmith时出错
            import langsmith
            from langsmith import Client
            
            self._client = Client()
            logger.info(f"LangSmith client initialized successfully for project: {settings['project']}")
            
        except ImportError:
            logger.warning("langsmith package not installed, tracing disabled")
        except Exception as e:
            logger.error(f"Failed to initialize LangSmith client: {e}")
    
    @property
    def client(self):
        """获取LangSmith客户端"""
        self._ensure_initialized()
        return self._client
    
    @property
    def is_enabled(self):
        """检查LangSmith追踪是否启用"""
        self._ensure_initialized()
        return self._tracing and self._client is not None
    
    def trace(self, name: str, metadata: Optional[Dict[str, Any]] = None):
        """
        追踪装饰器，用于记录函数执行
        
        Args:
            name: 追踪名称
            metadata: 附加元数据
        """
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self.is_enabled:
                    return func(*args, **kwargs)
                
                start_time = time.time()
                try:
                    # 记录输入参数（敏感信息需过滤）
                    input_data = {
                        "args": str(args),
                        "kwargs": str(kwargs),
                        "function_name": func.__name__,
                        "module": func.__module__
                    }
                    
                    # 合并元数据
                    trace_metadata = metadata or {}
                    trace_metadata.update(input_data)
                    
                    # 开始追踪
                    with self._client.trace(
                        name=name,
                        run_type="tool",
                        metadata=trace_metadata
                    ) as trace:
                        # 执行函数
                        result = func(*args, **kwargs)
                        
                        # 记录执行时间
                        execution_time = time.time() - start_time
                        trace.metadata["execution_time"] = execution_time
                        trace.metadata["success"] = True
                        
                        # 记录输出（敏感信息需过滤）
                        trace.outputs = {"result": str(result)}
                        
                        logger.debug(f"LangSmith trace '{name}' completed in {execution_time:.2f}s")
                        return result
                        
                except Exception as e:
                    # 记录错误
                    if self.is_enabled:
                        error_metadata = {
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "execution_time": time.time() - start_time,
                            "success": False
                        }
                        self._client.trace(
                            name=f"{name}_error",
                            run_type="tool",
                            metadata=error_metadata
                        )
                    logger.error(f"Error in traced function '{name}': {e}")
                    raise
            
            return wrapper
        return decorator
    
    def trace_context(self, name: str, metadata: Optional[Dict[str, Any]] = None):
        """
        追踪上下文管理器
        
        Args:
            name: 追踪名称
            metadata: 附加元数据
        """
        class TraceContext:
            def __init__(self, service, name, metadata):
                self.service = service
                self.name = name
                self.metadata = metadata or {}
                self.start_time = None
                self.trace = None
            
            def __enter__(self):
                if not self.service.is_enabled:
                    return self
                
                self.start_time = time.time()
                self.metadata["start_time"] = self.start_time
                
                # 创建追踪
                self.trace = self.service._client.trace(
                    name=self.name,
                    run_type="chain",
                    metadata=self.metadata
                )
                self.trace.__enter__()
                
                return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                if not self.service.is_enabled:
                    return False
                
                execution_time = time.time() - self.start_time
                self.trace.metadata["execution_time"] = execution_time
                
                if exc_type is None:
                    self.trace.metadata["success"] = True
                    self.trace.outputs = {"status": "completed"}
                else:
                    self.trace.metadata["success"] = False
                    self.trace.metadata["error"] = str(exc_val)
                    self.trace.metadata["error_type"] = exc_type.__name__
                
                self.trace.__exit__(exc_type, exc_val, exc_tb)
                
                logger.debug(f"LangSmith trace context '{self.name}' completed in {execution_time:.2f}s")
                return False
        
        return TraceContext(self, name, metadata)
    
    def log_event(self, event_type: str, data: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None):
        """
        记录自定义事件
        
        Args:
            event_type: 事件类型
            data: 事件数据
            metadata: 附加元数据
        """
        if not self.is_enabled:
            return
        
        try:
            event_metadata = metadata or {}
            event_metadata.update({
                "event_type": event_type,
                "timestamp": time.time()
            })
            
            self._client.trace(
                name=f"event_{event_type}",
                run_type="event",
                metadata=event_metadata,
                outputs=data
            )
            
            logger.debug(f"Logged LangSmith event: {event_type}")
        except Exception as e:
            logger.error(f"Failed to log LangSmith event: {e}")
    
    def log_metric(self, metric_name: str, value: float, metadata: Optional[Dict[str, Any]] = None):
        """
        记录指标数据
        
        Args:
            metric_name: 指标名称
            value: 指标值
            metadata: 附加元数据
        """
        if not self.is_enabled:
            return
        
        try:
            metric_metadata = metadata or {}
            metric_metadata.update({
                "metric_name": metric_name,
                "value": value,
                "timestamp": time.time()
            })
            
            self._client.trace(
                name=f"metric_{metric_name}",
                run_type="metric",
                metadata=metric_metadata
            )
            
            logger.debug(f"Logged LangSmith metric: {metric_name}={value}")
        except Exception as e:
            logger.error(f"Failed to log LangSmith metric: {e}")

# 创建全局实例
langsmith_service = LangSmithService()