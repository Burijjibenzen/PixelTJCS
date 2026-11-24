# PixelTJCS：Ubuntu 与 Web 前端迁移交接

更新日期：2026-11-22。交接分支：`windows-completed`，基于 `better-interaction` 的 M6 实现。

> **当前状态**：Ubuntu 已完成单线程 Web 导出与浏览器试玩。服务使用固定端口 8765。Godot 4.8 beta 4 的单线程测试入口为 `/http-preview/`，可在同网段用普通 HTTP 打开。

## Ubuntu 内网单实例演示

模型与 Web 默认在后台运行，关闭终端后继续运行。先启动模型，确认 `status` 显示模型已就绪，再启动 Web。可以在同一个终端操作。

```bash
bash scripts/serve_qwen.sh
bash scripts/serve_qwen.sh status
bash scripts/serve_web.sh
bash scripts/serve_web.sh status
```

组员直接打开 [PixelTJCS HTTP 预览](http://100.64.162.146:8765/http-preview/)。NPC 的自主对话和其他后台任务使用本机 Qwen3.5-9B。玩家发起的对话、记忆总结和关系评估优先通过 `.env` 中配置的网关调用 `GLM-5.3-Flash`。API 出错、超时或返回空正文时，这一轮余下的模型请求改用本地 Qwen，下一轮重新尝试 API。默认 API 请求超时设置为 12 秒。回退时为本轮保留本地模型队列的下一个空位，已开始的本地推理会先完成。

模型服务只监听回环地址。需要全程使用本地模型时，运行 `PIXELTJCS_LLM=local bash scripts/serve_web.sh`。可以通过 `LLM_PLAYER_API_MODEL` 和 `LLM_PLAYER_API_TIMEOUT` 调整玩家 API 的模型和超时。

模型和 Web 日志分别写入 `logs/qwen.log` 和 `logs/web.log`。两条启动脚本都支持 `logs` 实时查看和 `stop` 停止服务。查看日志时按 `Ctrl+C` 退出跟踪。

模型默认使用 `qwen35-serve` 环境和 `/data/zhuyakun/Qwen3.5-9B` 权重。每次请求的输入和输出合计最多 8192 token。停止模型后，可用 `QWEN_CONTEXT_TOKENS=12288 bash scripts/serve_qwen.sh` 试 12K。长输入会增加显存和处理时间，先用 8K 试玩，再根据实际显存余量调整。

### 重新导出 Godot 4.5.2 备用页面

在服务器终端运行以下命令。安装模板只需一次，后续前端更新后重新导出即可。

```bash
cd /home/zhuyakun/Roamchestra
godot_bin=/home/zhuyakun/Godot-4.5.2/Godot_v4.5.2-stable_linux.x86_64
template_dir="${XDG_DATA_HOME:-$HOME/.local/share}/godot/export_templates/4.5.2.stable"
mkdir -p "$template_dir" build/web
curl --fail --location --output build/godot-4.5.2-export-templates.tpz \
  'https://github.com/godotengine/godot-builds/releases/download/4.5.2-stable/Godot_v4.5.2-stable_export_templates.tpz'
unzip -jo build/godot-4.5.2-export-templates.tpz \
  'templates/web_nothreads_release.zip' -d "$template_dir"
"$godot_bin" --headless --path frontend --import
"$godot_bin" --headless --path frontend --export-release Web ../build/web/index.html
```

Godot 4.5.2 的 Web 加载器即使在单线程导出下也会检查浏览器的安全上下文。浏览器认可可信 HTTPS 和本机回环地址。直接打开服务器内网 IP 的 HTTP 地址会停在加载检查。启动前请停止同一存档上已有的 Roamchestra 实例。

### 通过 SSH 从另一台机器预览

如需单独预览 4.5.2 备用页面，可在服务器只监听回环地址。

```bash
PIXELTJCS_WEB_HOST=127.0.0.1 bash scripts/serve_web.sh
```

在看页面的电脑上运行以下命令。保持 SSH 窗口打开，然后访问 `http://127.0.0.1:18000/`。

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18000:127.0.0.1:8765 zhuyakun@100.64.162.146
```

### 使用 HTTPS 提供团队访问

如需使用 HTTPS 备用入口，可以复用团队已有的可信 HTTPS 服务。以 `PIXELTJCS_WEB_HOST=127.0.0.1` 启动 Web，将整个游戏站点代理到 `127.0.0.1:8765`，包括 `/api/ws/events` 的 WebSocket 升级。把 HTTPS 地址发给组员即可，访问者使用默认浏览器设置。

如果没有现成入口，但团队有可管理的域名，可以为游戏分配一个子域名，让内网 DNS 将它解析到服务器，并用 DNS-01 验证签发浏览器信任的证书。DNS-01 通过 DNS 记录验证域名，游戏服务可以只监听内网地址。[Let's Encrypt DNS-01 说明](https://letsencrypt.org/docs/challenge-types/#dns-01-challenge)

如果已有访问者浏览器信任、与访问域名或 IP 匹配的证书，也可直接让 Uvicorn 在内网地址上提供 HTTPS。使用以下方式时，先停止本机预览实例。

```bash
PIXELTJCS_WEB_HOST=100.64.162.146 bash scripts/serve_web.sh \
  --ssl-certfile /path/to/fullchain.pem --ssl-keyfile /path/to/privkey.pem
```

用浏览器先打开预览地址的 `/api/health`，然后打开首页。浏览器开发者工具中应看到 `index.wasm`、`index.pck` 和 `/api/world` 成功加载，`/api/ws/events` 完成升级并收到世界快照。Godot 使用页面的 origin 生成 API 基址，Uvicorn 在一个进程里处理静态资源、HTTP 与 WebSocket。

Web 玩家直接以「同小济」加入校园，一次只允许一个页面操作。其他页面可以浏览楼层、角色和事件。没有活跃玩家时，服务端指定一个旁观页面自动驱动 NPC，其他页面通过世界快照同步坐标。玩家加入时接过 NPC 上报权。玩家离开或约 20 秒未报告存活后，名额会释放。重新导出 4.8 测试入口可运行 `bash scripts/build_http_preview.sh`。

要一次性清除旧玩家存档，先正常停止 PixelTJCS 后端，再从仓库根目录运行 `.venv/bin/python -B -m scripts.reset_demo_player`。脚本会在 `data/` 下备份 SQLite 数据库，清理玩家资料、对话和关联记录，并创建干净的「同小济」资料。随后用原命令启动后端。

## 1. Windows 交接参考

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
- `frontend/scripts/api_client.gd` 每约 0.25 秒上报所有 NPC 的位置。Web 入口只接受当前玩家页面或空闲时被指定的旁观页面的坐标。游戏移动仍由浏览器计算，公网多人使用前需要服务端权威移动和多玩家模型。
- 后端不在线时 Godot 回退为本地 NPC 演示日程；此状态没有真实模型交谈和完整玩家会话。

## 3. Ubuntu 后端先行验证

若服务器尚无仓库，先配置 GitHub 访问并执行 `git clone git@github.com:Burijjibenzen/Roamchestra.git PixelTJCS`，然后进入 `PixelTJCS`。已有仓库则直接在仓库根目录执行以下步骤：

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

另一个终端检查 `curl http://127.0.0.1:8000/health`、`/world` 和 `/docs`。本机通过测试时使用的是 `/usr/local/miniconda3/bin/python` 创建的 Python 3.12 虚拟环境。模型网关客户端在 `backend/llm.py` 中设置了 `trust_env=False`，需要 Ubuntu 主机直连 `.env` 中的网关地址，并通过一条短请求验证密钥权限。

更换模型后，在服务器终端运行下面的命令检查当前网关是否接受配置中的模型 ID。命令从 `.env` 读取密钥，仅输出模型 ID 和验证结果。检查通过后重启现有服务。

```bash
.venv/bin/python -B - <<'PY'
import httpx
from backend.config import LLM

headers = {"Authorization": f"Bearer {LLM.api_key}"}
with httpx.Client(base_url=LLM.base_url.rstrip("/") + "/", headers=headers,
                  timeout=15, trust_env=False) as client:
    response = client.get("models")
    response.raise_for_status()
    model_ids = {entry["id"] for entry in response.json()["data"]}
    requested = {LLM.chat_model, LLM.think_model}
    missing = requested - model_ids
    if missing:
        candidates = sorted(name for name in model_ids if "deepseek" in name.lower())
        raise SystemExit(f"网关未列出 {sorted(missing)}。网关提供的 DeepSeek 模型：{candidates}")
    print("网关已列出配置中的模型：", ", ".join(sorted(requested)))
PY
```

运行数据位于 `data/memory.db`，日志位于 `logs/`，两者均被 Git 忽略。若需迁移 Windows 既有世界，应停机后单独备份并安全传输数据库；若从新世界开始，则不必复制。`scripts/run_demo.py` 使用 `fresh_db=True`，**会清空运行数据库**，不要对正式存档运行。`.env`、`launcher.local.bat` 和玩家凭据不得进入 Git。

## 4. 浏览器前端必须处理的差异

1. **导出工程**使用 Godot 4.5.2、Compatibility 渲染和仓库中的单线程 `Web` 预设。先安装匹配的模板并导入资源，再导出。浏览器需要支持 WebAssembly 和 WebGL 2。[Godot 4.5 Web 导出说明](https://docs.godotengine.org/en/4.5/tutorials/export/exporting_for_web.html)

   ```bash
   godot --headless --path frontend --import
   mkdir -p build/web
   godot --headless --path frontend --export-release Web ../build/web/index.html
   ```

   上述命令以已创建的 `Web` 预设和已安装的导出模板为前提；导出路径相对于 `frontend/project.godot` 所在目录。按服务器上的实际目录核对产物位置。[Godot 4.5 命令行导出文档](https://docs.godotengine.org/en/4.5/tutorials/editor/command_line_tutorial.html)。

2. **浏览器运行时 API 地址**由 `api_client.gd` 从页面 origin 和 `/api` 生成。HTTP 与 WebSocket 共用该地址，`social_ui.gd` 将 `http/https` 转换为 `ws/wss`。原生桌面应用默认使用 `http://127.0.0.1:8000`，可通过 `PIXELTJCS_BACKEND_URL` 覆盖。

3. **同源入口**由 `backend.web` 提供。根路径提供 Godot 静态文件，`/api` 挂载 FastAPI，事件流路径为 `/api/ws/events`。如后续增加 TLS 反向代理，需转发 WebSocket 升级请求。[Web 文件服务要求](https://docs.godotengine.org/en/4.5/tutorials/export/exporting_for_web.html#serving-the-files)

4. **浏览器行为**：Web 玩家以临时会话加入「同小济」，原生端仍可把玩家身份凭据写在 `user://pixeltjcs-player.cfg`。浏览器后台标签页可能暂停 Godot 的帧循环，NPC 位置上报随之停止，后端时钟却仍在推进；需验证重连与恢复，并考虑把权威 NPC 行走移到服务端。[Godot Web 限制](https://docs.godotengine.org/en/4.5/tutorials/export/exporting_for_web.html#limitations)。

5. **公开访问的权限边界**：目前 `/world/reset`、`/world/cast`、`/world/whisper`、公告发布/删除、`/world/report` 和 `/debug/*` 等接口并无面向公网的统一鉴权。`world_id` 与确认字段用于防误操作，不能充当身份认证。公开部署前应明确管理员身份、普通玩家权限、位置上报可信来源，并限制调试接口；模型密钥只能留在服务端。

## 5. 建议的实施顺序与验收

1. Ubuntu 安装依赖、通过后端测试、单 worker 启动并检查 `/health`、`/world`；先不开放公网。
2. 建立 Godot Web 预设并导出，先在本机/内网浏览器检查地图、字体、交互和资源加载。
3. 实现浏览器运行时 API 基址和同源反向代理；验证 HTTP、`wss://` 事件流、断线重连和玩家加入/聊天。
4. 再处理管理接口权限和单客户端/多客户端的位置权威问题；在这一步完成之前只作为受控演示开放。
5. 用真实模型连续试玩：找人邀约、公告阅读、旁听记忆、退场再出演、玩家离开重入、浏览器后台恢复、重启后 SQLite 存档。记录请求失败、模型延迟和内存/数据库增长。

Windows 交接时已通过 **148 项后端自动测试**、Godot 社交界面检查和公告栏 HTTP/Godot 联调；这些测试主要使用模拟模型，**不等于 Ubuntu 或浏览器验收**。M6 已知的赴约时间估算、正式八卦候选等行为边界见 `docs/M6.md` §6。
