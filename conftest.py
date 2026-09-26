"""pytest 配置：把项目根加入 sys.path，使测试能 import 项目模块。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
