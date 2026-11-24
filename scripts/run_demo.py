"""命令行演示脚本 —— 答辩主入口（不依赖 Godot 前端）

演示故事线（对应需求文档的答辩演示脚本）:
    场景一  导师召见: 严正 批评 噜噜 实验进度 → 对话 + 记忆 + 关系
    场景二  实验室吐槽: 噜噜 遇到 格林，情绪传染 → 八卦源头
    场景三  情报传播: 辍学熊 与 阿沫 在茶水间交换消息 → 八卦接力
    时间快进 3 天 → 遗忘检查（衰减/容量）+ 反思压缩
    最后展示: 噜噜的记忆面板（活跃 vs 已遗忘）+ 全员关系矩阵

运行:
    cd PixelTJCS
    python scripts/run_demo.py          # 联网完整演示
    python scripts/run_demo.py --quick  # 缩短对话轮次，快速跑通链路

注意: 需要 .env 中配置好 LLM_API_KEY；若网关不可用，对话会自动降级为
预设台词（记忆/关系链路照常演示）。
"""

import argparse
import os
import sys

# 让脚本可以直接 `python scripts/run_demo.py` 运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.logger import get_logger  # noqa: E402
from backend.service import GameService  # noqa: E402

log = get_logger(__name__)

SEP = "=" * 62


def banner(title: str) -> None:
    print(f"\n{SEP}\n🎬 {title}\n{SEP}")


def print_transcript(result: dict) -> None:
    """打印一场对话的完整过程与产物。"""
    print(f"\n📍 地点: {result['area_name']}   🕐 {result['game_time']}")
    print(f"💬 触发: {result['trigger']}\n" + "-" * 62)
    for turn in result["transcript"]:
        print(f"  【{turn['speaker']}】{turn['text']}")
    print("-" * 62)
    for who, mem in result["memories"].items():
        if mem:
            print(f"  🧠 {who} 写下记忆(重要性{mem['importance']:.2f}/{mem['emotion']}): "
                  f"{mem['content']}")
    rel = result["relation"]
    print(f"  ❤️ 关系变化: {rel['delta']:+.1f} → 当前 {rel['score']:.0f}（{rel['reason']}）")


def print_memory_panel(service: GameService, name: str, top_n: int = 5) -> None:
    """打印某角色的记忆面板（活跃 vs 已遗忘，核心演示环节）。"""
    panel = service.memory_panel(name)
    print(f"\n🧠【{name} 的记忆面板】")
    active = panel["active"]
    forgotten = panel["forgotten"]
    reflections = panel["reflections"]
    print(f"  活跃记忆 {len(active)} 条 / 已遗忘 {len(forgotten)} 条 / 洞察 {len(reflections)} 条")

    print(f"\n  ── 最新 {top_n} 条活跃记忆 ──")
    for m in active[:top_n]:
        mark = "🧠洞察" if m["type"] == "reflection" else "  "
        print(f"   {mark} [{m['type']}/{m['emotion']}] {m['content']}")

    if reflections:
        print(f"\n  ── 反思洞察 ──")
        for m in reflections[:3]:
            print(f"   💡 {m['content']}")

    print(f"\n  ── 遗忘档案（最新 {top_n} 条，含遗忘原因） ──")
    for m in forgotten[:top_n]:
        reason = {"strength_decay": "时间衰减", "capacity_evict": "容量淘汰"}.get(
            m["forget_reason"], m["forget_reason"])
        print(f"   🫥 [{reason}] {m['content']}")
    if not forgotten:
        print("   （暂无遗忘记录 —— 时间推进后这里会出现内容）")


# <!--DEMO_PART2-->
def print_relations(service: GameService) -> None:
    """打印全员关系矩阵（精简版）。"""
    matrix = service.relations.matrix()
    print("\n❤️【全员关系矩阵】(0-100, — 为陌生人)")
    names = list(matrix.keys())
    print("      " + " ".join(f"{n[:2]:>4}" for n in names))
    for a in names:
        cells = []
        for b in names:
            v = matrix[a][b]
            cells.append("  —" if v is None else f"{v:4.0f}")
        print(f"{a[:4]:>6} " + " ".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser(description="PixelTJCS 命令行演示")
    parser.add_argument("--quick", action="store_true",
                        help="快速模式: 对话 1 回合（默认 2 回合）")
    args = parser.parse_args()
    if args.quick:
        from backend.config import WORLD
        WORLD.max_rounds = 1
        print("⚡ 快速模式: 每场对话 1 回合")

    banner("PixelTJCS 校园多智能体交互系统 · M1 演示")
    service = GameService(fresh_db=True)
    print(f"\n🌍 世界开局: {service.world.clock.fmt()}")
    for npc in service.npc_list():
        print(f"  👤 {npc['name']}（{npc['title']}）@ {npc['area']}")

    # ================= 场景一: 导师召见 =================
    banner("场景一 · 导师召见：严正 批评 噜噜")
    service.goto("严正", "zx4_yan")
    service.goto("噜噜", "zx4_yan")
    service.world.inject_event("严正在办公室召见噜噜，要听实验进展汇报",
                               ["严正", "噜噜"], "zx4_yan")
    result = service.run_conversation(
        "严正", "噜噜", "导师召见学生汇报进度，严正对进度不满意，语气严厉")
    if result:
        print_transcript(result)

    # ================= 场景二: 实验室吐槽 =================
    banner("场景二 · 实验室吐槽：噜噜 × 格林")
    service.goto("噜噜", "j3_lab")
    service.goto("格林", "j3_lab")
    result = service.run_conversation(
        "噜噜", "格林", "噜噜刚被导师批评，情绪低落，在工位遇到格林想吐槽")
    if result:
        print_transcript(result)

    # ================= 场景三: 情报传播 =================
    banner("场景三 · 情报传播：辍学熊 × 阿沫")
    service.goto("辍学熊", "j3_pantry")
    service.goto("阿沫", "j3_pantry")
    result = service.run_conversation(
        "辍学熊", "阿沫", "茶水间偶遇，辍学熊好奇严老师组里最近在忙什么，和阿沫聊起实验室近况")
    if result:
        print_transcript(result)

    # ================= 日常观察: 琐事入库（遗忘演示素材） =================
    banner("日常观察: 角色们各自记下几件琐事（低重要性记忆）")
    observations = {
        "噜噜": [("路过公告栏看到周五羽毛球团体赛的海报，想组队但没空", 0.20),
                 ("食堂麻辣香锅排队太长，随便扒了两口就回实验室", 0.15)],
        "格林": [("茶水间微波炉坏了，热饭排了十分钟队", 0.25),
                   ("编译又挂了，摸鱼刷了会儿手机", 0.20)],
        "辍学熊": [("听说三楼打印机换新了，刷校园卡就能用", 0.20),
                   ("傍晚操场有人踢球，好像是研究生联赛", 0.15)],
        "阿沫": [("图书馆四楼靠窗的座位下午人最少", 0.20),
                   ("开题报告模板在学院官网哪里下载还没搞清楚", 0.30)],
        "严正": [("行政楼门口的玉兰开了，今年开得早", 0.15),
                 ("评审意见里有一条措辞比较尖锐，改天再回复", 0.35)],
    }
    for who, items in observations.items():
        for text, imp in items:
            service.observe(who, text, imp)
            print(f"  📝 {who} 记下琐事(重要性{imp:.2f}): {text}")

    # ================= 时间快进: 遗忘演示 =================
    banner("时间快进 3 天 → 反思压缩 + 记忆遗忘")
    stats = service.advance_time(3 * 24 * 60)
    print(f"\n⏩ 现在: {stats['now']}")
    print(f"   🧠 触发反思: {'、'.join(stats['reflected']) or '（无人达到阈值）'}")
    print(f"   🫥 时间衰减淡忘: {stats['forget'].get('forgotten_decay', 0)} 条")
    print(f"   🗄️ 容量淘汰: {stats['forget'].get('forgotten_evict', 0)} 条")

    # ================= 记忆面板 =================
    banner("记忆可视化: 噜噜 的记忆面板")
    print_memory_panel(service, "噜噜")
    banner("记忆可视化: 格林 的记忆面板")
    print_memory_panel(service, "格林")

    # ================= 关系矩阵 =================
    banner("社交网络状态")
    print_relations(service)

    banner("演示结束 · 后端 API: uvicorn backend.api:app --port 8000 → /docs")


if __name__ == "__main__":
    main()

