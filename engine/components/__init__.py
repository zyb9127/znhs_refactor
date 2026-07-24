"""
engine.components —— 内置组件包

导入本包即触发四个内置组件的 @register_component 注册：
- data_fetch      数据采集（包装 steps.DataStep）
- recommend       推荐筛选（包装 steps.RecommendStep）
- script_generate 话术生成（包装 steps.ScriptStep.run_batch）
- output_guard    话术输出护栏（敏感词/长度，纯字符串处理，默认 no-op）
"""
from engine.components.data_fetch import DataFetchComponent
from engine.components.recommend import RecommendComponent
from engine.components.script_generate import ScriptGenerateComponent
from engine.components.output_guard import OutputGuardComponent

__all__ = [
    "DataFetchComponent",
    "RecommendComponent",
    "ScriptGenerateComponent",
    "OutputGuardComponent",
]
