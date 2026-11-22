# Roamchestra：Ubuntu 与 Web 前端迁移交接

更新日期：2026-11-22。交接分支：`windows-completed`，基于 `better-interaction` 的 M6 实现。

> **当前状态**：Windows 上的 Python 后端与 Godot 桌面前端已联调；**尚未制作 Godot Web 导出、Ubuntu 部署脚本或公网多用户架构**。服务器上的接手者应先让单实例演示在 Ubuntu 跑通，再实施浏览器适配。不要将桌面版的通过记录当作 Web 验收结果。

## 1. 先读这些文件

| 文件 | 用途 |
| --- | --- |
| `README.md`、`docs/M6.md` | 功能、操作和已知边界 |
| `backend/api.py`、`backend/social.py` | FastAPI 路由、世界单例、调度生命周期、WebSocket |
| `backend/config.py`、`.env.example` | 模型与运行配置 |
| `frontend/project.godot`、`frontend/scripts/api_client.gd`、`frontend/scripts/social_ui.gd` | Godot 项目、HTTP 地址、事件 WebSocket |
| `frontend/scripts/player_chat.gd`、`backend/player_mode.py` | 玩家会话与本地身份凭据 |
| `requirements.txt` | Python 依赖，部分版本已固定 |

## 2. 当前架构和运行状态

```text
Godot 客户端 ── HTTP /world、/player、/noticeboards 等 ──> 单进程 FastAPI
             └─ WebSocket /ws/events ──────────────────────> 同一进程
FastAPI ── SQLite data/memory.db、内存中的世界/调度器 ──> 模型网关
```

- 后端启动时创建 `SocialService` 并启动 NPC 调度循环。默认没有玩家，NPC 自主移动、交谈、读公告、记录记忆与事件。玩家加入同一世界后可行走和交谈，NPC 调度继续运行。上帝模式可发布公告、悄悄话指派、退场/出演及重置。
- `backend/api.py` 的 `_service` 是**进程内单例**。目前请用 **一个 Uvicorn worker、一个后端实例**；开多个 worker 会形成多个不一致的世界，即使共用 SQLite 也不能解决内存状态分裂。
- `frontend/scripts/api_client.gd` 每约 0.25 秒上报所有 NPC 的位置。现有服务端信任客户端上报的坐标。**多个浏览器同时打开会互相覆盖 NPC 位置**，而 `backend/player_mode.py` 同时只允许一个活跃玩家会话。当前只适合受控的单客户端演示；公网多人使用前必须确定服务端权威移动/位置和多玩家模型。
- 后端不在线时 Godot 回退为本地 NPC 演示日程；此状态没有真实模型交谈和完整玩家会话。

## 3. Ubuntu 后端先行验证

若服务器尚无仓库，先配置 GitHub 访问并执行 `git clone git@github.com:Burijjibenzen/Roamchestra.git`，然后进入 `Roamchestra`。已有仓库则直接在仓库根目录执行以下步骤：

```bash
git fetch origin
git switch --track origin/windows-completed
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# 在服务器上编辑 .env，填写服务器自己的 LLM_API_KEY；不要提交密钥
python -X utf8 -B -m unittest discover -s tests -q
python -X utf8 -B -m uvicorn backend.api:app --host 127.0.0.1 --port 8000 --workers 1
```

另一个终端检查 `curl http://127.0.0.1:8000/health`、`/world` 和 `/docs`。Linux 依赖安装与运行尚未在本分支验证，遇到原生扩展包问题先记录 Python 版本、架构及完整错误。模型网关客户端在 `backend/llm.py` 中设置了 `trust_env=False`，不会自动走系统代理；确认 Ubuntu 主机能直接访问 `.env` 中的网关地址，并通过一条短请求验证密钥权限。

运行数据位于 `data/memory.db`，日志位于 `logs/`，两者均被 Git 忽略。若需迁移 Windows 既有世界，应停机后单独备份并安全传输数据库；若从新世界开始，则不必复制。`scripts/run_demo.py` 使用 `fresh_db=True`，**会清空运行数据库**，不要对正式存档运行。`.env`、`launcher.local.bat` 和玩家凭据不得进入 Git。

## 4. 浏览器前端必须处理的差异

1. **导出工程**：项目使用 Godot 4.5 的 GDScript 和 Compatibility 渲染，具备尝试 Web 导出的基础，但仓库目前没有 `frontend/export_presets.cfg`。在 Godot **4.5.2** 编辑器中安装匹配的 Web 导出模板，建立名为 `Web` 的导出预设，优先尝试默认的单线程导出。先导入资源，再导出；预设和产物应在浏览器实际验证后决定哪些纳入仓库。Godot 官方说明 Web 导出需浏览器支持 WebAssembly、WebGL 2，且单线程是首选默认方式：[Godot 4.5 Web 导出说明](https://docs.godotengine.org/en/4.5/tutorials/export/exporting_for_web.html)。

   ```bash
   godot --headless --path frontend --import
   mkdir -p build/web
   godot --headless --path frontend --export-release Web ../build/web/index.html
   ```

   上述命令以已创建的 `Web` 预设和已安装的导出模板为前提；导出路径相对于 `frontend/project.godot` 所在目录。按服务器上的实际目录核对产物位置。[Godot 4.5 命令行导出文档](https://docs.godotengine.org/en/4.5/tutorials/editor/command_line_tutorial.html)。

2. **浏览器运行时 API 地址**：现在 `api_client.gd` 默认是 `http://127.0.0.1:8000`，原生应用可从 `ROAMCHESTRA_BACKEND_URL` 环境变量覆盖。Web 导出运行在访问者的浏览器中，服务器环境变量不会自动变成浏览器环境变量；继续用默认值会访问**访问者自己的电脑**。需实现浏览器可读取的运行时配置（如同源 `/config.json`、自定义 HTML shell/JavaScriptBridge，或明确的同源 `/api` 基址），并让 HTTP 与 WebSocket 共用该基址。`social_ui.gd` 已能把 `http/https` 转换为 `ws/wss`，但尚未在浏览器验证。

3. **推荐先用同源入口**：例如浏览器访问 `https://example.org/` 获取 Godot 静态文件，反向代理 `https://example.org/api/*` 到一个 FastAPI 实例并去掉 `/api` 前缀，WebSocket `/api/ws/events` 也要代理升级。客户端基址为 `https://example.org/api`。这样可避免跨域调用；若使用独立 API 域名，须修改 `backend/api.py` 当前仅允许 localhost 的 CORS 配置，并处理浏览器跨域、HTTPS/WSS 与凭据。导出的 `.wasm`、`.pck` 等文件应由正式静态文件服务器提供；Godot 文档列出了 MIME、压缩和线程模式所需响应头。[Web 文件服务要求](https://docs.godotengine.org/en/4.5/tutorials/export/exporting_for_web.html#serving-the-files)。

4. **浏览器行为**：`frontend/scripts/player_chat.gd` 把玩家身份凭据写在 `user://roamchestra-player.cfg`。Web 端的 `user://` 持久化依赖浏览器存储设置，需验证刷新、隐私窗口和不同浏览器的行为。浏览器后台标签页可能暂停 Godot 的帧循环，NPC 位置上报随之停止，后端时钟却仍在推进；需验证重连与恢复，并考虑把权威 NPC 行走移到服务端。[Godot Web 限制](https://docs.godotengine.org/en/4.5/tutorials/export/exporting_for_web.html#limitations)。

5. **公开访问的权限边界**：目前 `/world/reset`、`/world/cast`、`/world/whisper`、公告发布/删除、`/world/report` 和 `/debug/*` 等接口并无面向公网的统一鉴权。`world_id` 与确认字段用于防误操作，不能充当身份认证。公开部署前应明确管理员身份、普通玩家权限、位置上报可信来源，并限制调试接口；模型密钥只能留在服务端。

## 5. 建议的实施顺序与验收

1. Ubuntu 安装依赖、通过后端测试、单 worker 启动并检查 `/health`、`/world`；先不开放公网。
2. 建立 Godot Web 预设并导出，先在本机/内网浏览器检查地图、字体、交互和资源加载。
3. 实现浏览器运行时 API 基址和同源反向代理；验证 HTTP、`wss://` 事件流、断线重连和玩家加入/聊天。
4. 再处理管理接口权限和单客户端/多客户端的位置权威问题；在这一步完成之前只作为受控演示开放。
5. 用真实模型连续试玩：找人邀约、公告阅读、旁听记忆、退场再出演、玩家离开重入、浏览器后台恢复、重启后 SQLite 存档。记录请求失败、模型延迟和内存/数据库增长。

Windows 交接时已通过 **148 项后端自动测试**、Godot 社交界面检查和公告栏 HTTP/Godot 联调；这些测试主要使用模拟模型，**不等于 Ubuntu 或浏览器验收**。M6 已知的赴约时间估算、正式八卦候选等行为边界见 `docs/M6.md` §6。
