import logging
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        # 可以添加文件日志处理器
        # logging.FileHandler('app.log')
    ]
)

# 创建日志记录器
logger = logging.getLogger(__name__)
