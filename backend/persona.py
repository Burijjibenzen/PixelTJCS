"""人格卡（Persona Card）—— 系统的"角色数据层"

每个角色一张 JSON 卡（personas/ 目录），NPC 的一切行为差异都源于此:
性格、说话风格、目标、秘密（只进本人记忆）、日程、初始关系。

新增角色 = 复制一张卡改内容，无需改任何代码。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

from backend.logger import get_logger

log = get_logger(__name__)

# personas/ 目录: <repo>/personas/
PERSONA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "personas"
)


@dataclass
class PersonaCard:
    """一张人格卡的全部字段（与 personas/*.json 一一对应）"""

    name: str  # 角色全名（唯一 ID，如 "陈卷"）
    role: str  # 身份类别: student / advisor / staff
    title: str  # 具体头衔，如 "博士生（博四）"、"教授/大导师"
    expertise: str = ""  # 擅长领域
    mbti: str = ""  # MBTI（演示用，也写进提示词增强人设感）
    traits: List[str] = field(default_factory=list)  # 性格关键词
    tone: str = ""  # 说话风格描述
    catchphrase: str = ""  # 口头禅
    example_lines: List[str] = field(default_factory=list)  # 示例台词（也是离线兜底台词）
    goals_short: str = ""  # 短期目标（影响当前行为动机）
    goals_long: str = ""  # 长期目标
    secrets: List[str] = field(default_factory=list)  # 秘密：只进本人记忆，可被好友"套话"
    schedule: Dict[str, str] = field(default_factory=dict)  # {"HH:MM": 区域id}
    initial_relations: Dict[str, float] = field(default_factory=dict)  # {角色名: 0-100}

    # ------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: str) -> "PersonaCard":
        """从 JSON 文件加载一张人格卡（缺字段自动补默认值）。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        card = cls(
            name=data["name"],
            role=data["role"],
            title=data["title"],
            expertise=data.get("expertise", ""),
            mbti=data.get("mbti", ""),
            traits=data.get("traits", []),
            tone=data.get("tone", ""),
            catchphrase=data.get("catchphrase", ""),
            example_lines=data.get("example_lines", []),
            goals_short=data.get("goals", {}).get("short_term", ""),
            goals_long=data.get("goals", {}).get("long_term", ""),
            secrets=data.get("secrets", []),
            schedule=data.get("schedule", {}),
            initial_relations=data.get("initial_relations", {}),
        )
        log.info("人格卡加载: %s（%s）", card.name, card.title)
        return card

    # ------------------------------------------------------------------
    def public_profile(self) -> dict:
        """公开资料白名单；不序列化整张卡，秘密和内部关系配置仅留在服务端。"""
        return {
            "name": self.name, "role": self.role, "title": self.title,
            "expertise": self.expertise or "", "mbti": self.mbti or "",
            "traits": list(self.traits or []), "tone": self.tone or "",
            "catchphrase": self.catchphrase or "",
            "goals": {"short_term": self.goals_short or "", "long_term": self.goals_long or ""},
            "schedule": dict(self.schedule or {}),
        }

    # ------------------------------------------------------------------
    def render_system_prompt(self) -> str:
        """把人格卡渲染成完整的系统提示词（NPC 每次说话都用它打底）。"""
        return f"""你是校园多智能体仿真中的角色「{self.name}」，请完全以第一人称沉浸扮演。

【身份】{self.title}
【擅长】{self.expertise}
【性格】{self.mbti}；{ "、".join(self.traits) }
【说话风格】{self.tone}（口头禅: "{self.catchphrase}"）
【示例台词】{" / ".join(self.example_lines)}
【短期目标】{self.goals_short}
【长期目标】{self.goals_long}

【行为准则】
1. 永远保持角色一致性，用第一人称"我"说话，每句话 15~50 字
2. 让语言风格贴合你的性格与口头禅，可以表达情绪（开心/烦躁/焦虑/得意）
3. 结合你刚"想起"的记忆（若提供了）自然回应，不要复述记忆原文
4. 超出你擅长领域的话题可以含糊带过或反问对方
5. 绝对不要说自己是 AI / 语言模型 / 程序"""

    def render_secret_hint(self) -> str:
        """秘密提示：只注入本角色的上下文，用于"被套话"玩法。"""
        if not self.secrets:
            return ""
        joined = "；".join(self.secrets)
        return (
            f"【你自己的秘密（仅你知晓）】{joined}\n"
            f"默认绝不主动提起；若与你信任度高（好感度>70）的人直接追问相关话题，"
            f"你可能犹豫后透露部分，并感到不安。"
        )

    def fallback_line(self) -> str:
        """离线兜底台词：LLM 不可用时随机使用，保证演示不中断。"""
        import random

        if self.example_lines:
            return random.choice(self.example_lines)
        return f"（{self.name}点了点头，没说什么。）"


def load_all_personas(directory: str = PERSONA_DIR) -> Dict[str, PersonaCard]:
    """加载目录下全部 *.json 人格卡 → {角色名: PersonaCard}"""
    cards: Dict[str, PersonaCard] = {}
    if not os.path.isdir(directory):
        log.error("personas 目录不存在: %s", directory)
        return cards
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".json"):
            continue
        try:
            card = PersonaCard.from_file(os.path.join(directory, fname))
            cards[card.name] = card
        except Exception as e:  # noqa: BLE001 单张卡损坏不影响其他角色
            log.error("人格卡 %s 加载失败: %s", fname, e)
    log.info("共加载 %d 张人格卡", len(cards))
    return cards
