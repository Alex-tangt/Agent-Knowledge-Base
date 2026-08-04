#!/usr/bin/env python3
"""测试LangSmith集成功能"""

import sys
import os

def test_imports():
    """测试所有必要的导入"""
    print("测试模块导入...")
    
    try:
        from services.langsmith_service import langsmith_service
        print("✓ langsmith_service 导入成功")
        print(f"  LangSmith 启用状态: {langsmith_service.is_enabled}")
    except ImportError as e:
        print(f"✗ langsmith_service 导入失败: {e}")
        return False
    
    try:
        from config.config import LANGSMITH_API_KEY, LANGSMITH_PROJECT, LANGSMITH_TRACING
        print("✓ LangSmith 配置导入成功")
        print(f"  项目: {LANGSMITH_PROJECT}")
        print(f"  追踪开关: {LANGSMITH_TRACING}")
    except ImportError as e:
        print(f"✗ LangSmith 配置导入失败: {e}")
        return False
    
    # 测试服务模块导入
    modules = [
        "services.document_service",
        "services.vector_store_service", 
        "services.chat_service",
        "services.rag_service"
    ]
    
    for module_name in modules:
        try:
            __import__(module_name)
            print(f"✓ {module_name} 导入成功")
        except ImportError as e:
            print(f"✗ {module_name} 导入失败: {e}")
            return False
    
    return True

def test_config_values():
    """测试配置值"""
    print("\n测试配置值...")
    
    from config.config import LANGSMITH_API_KEY, LANGSMITH_PROJECT, LANGSMITH_TRACING
    
    if LANGSMITH_API_KEY:
        print(f"✓ LANGSMITH_API_KEY 已设置 (长度: {len(LANGSMITH_API_KEY)})")
    else:
        print("⚠ LANGSMITH_API_KEY 未设置 (需要在.env文件中配置)")
    
    print(f"✓ LANGSMITH_PROJECT: {LANGSMITH_PROJECT}")
    print(f"✓ LANGSMITH_TRACING: {LANGSMITH_TRACING}")
    
    return True

def test_langsmith_service():
    """测试LangSmith服务功能"""
    print("\n测试LangSmith服务功能...")
    
    from services.langsmith_service import langsmith_service
    
    if langsmith_service.is_enabled:
        print("✓ LangSmith 追踪已启用")
        
        # 测试追踪装饰器
        @langsmith_service.trace(name="test_trace", metadata={"test": True})
        def test_function(x, y):
            return x + y
        
        result = test_function(2, 3)
        print(f"✓ 追踪装饰器测试通过: 2 + 3 = {result}")
        
        # 测试上下文管理器
        with langsmith_service.trace_context(name="test_context", metadata={"test": True}) as trace:
            print("✓ 上下文管理器测试通过")
        
        print("✓ LangSmith 服务功能正常")
    else:
        print("⚠ LangSmith 追踪未启用 (需要设置LANGSMITH_API_KEY环境变量)")
    
    return True

def test_app_initialization():
    """测试应用初始化"""
    print("\n测试应用初始化...")
    
    try:
        from app import app, lifespan
        print("✓ FastAPI应用导入成功")
        
        # 测试应用配置
        if hasattr(app, 'routes'):
            print(f"✓ 应用已配置 {len(app.routes)} 个路由")
        
        print("✓ 应用初始化测试通过")
        return True
    except Exception as e:
        print(f"✗ 应用初始化测试失败: {e}")
        return False

def main():
    """主测试函数"""
    print("=" * 60)
    print("LangSmith 集成功能测试")
    print("=" * 60)
    
    tests = [
        test_imports,
        test_config_values,
        test_langsmith_service,
        test_app_initialization
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"✗ 测试执行失败: {e}")
            results.append(False)
    
    print("\n" + "=" * 60)
    print("测试结果汇总:")
    print("=" * 60)
    
    for i, test in enumerate(tests):
        status = "✓ 通过" if results[i] else "✗ 失败"
        print(f"{test.__name__}: {status}")
    
    all_passed = all(results)
    if all_passed:
        print("\n✅ 所有测试通过！LangSmith 集成功能正常。")
        print("   请在LangSmith控制台查看追踪数据: https://smith.langchain.com")
    else:
        print("\n⚠ 部分测试失败，请检查配置和依赖。")
        print("   需要安装依赖: pip install langsmith")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)