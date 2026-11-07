"""Roamchestra —— 校园多智能体交互系统后端包

模块导览（依赖方向自上而下，禁止反向依赖）:
    config   全局配置
    logger   统一日志
    llm      大模型客户端（唯一出口）
    persona  人格卡数据层
    memory   自研记忆服务（存储/检索/遗忘/反思）
    relations 关系矩阵
    world    游戏世界（时钟/区域/事件）
    npc      NPC Agent（人格+记忆+关系的载体）
    conversation  NPC↔NPC 对话引擎
    api      FastAPI 接口层
"""

__version__ = "0.1.0"
