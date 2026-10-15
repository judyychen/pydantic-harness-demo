# Pydantic Harness Demo

个人应用开发 Demo：Pydantic AI / Harness 驱动模型读写沙箱中的 Next.js 应用，AG-UI 展示对话与工具调用，Next.js Fast Refresh 更新预览。

仓库包含工作台、宿主程序、开发 Skill、Next.js 茶铺示例源码及离线检查。不包含任何公司的后端或平台实现、内部方案文档、密钥、登录凭据、聊天记录、运行数据库或原沙箱引用。

## 启动

需要 Python 3.12、Node.js 22，以及自己的 E2B 兼容沙箱配置。模型沿用 Pydantic AI 的 OpenAICodexProvider，读取运行者本机已有的登录信息；仓库不提供账号或凭据。当前固定模型为 `gpt-6.1-sol`。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix chat-ui ci --no-audit --no-fund
npm --prefix chat-ui run build
cp .env.example .env.local
```

编辑 `.env.local`，填入控制台提供的 `E2B_API_URL`、`E2B_API_KEY` 和 `E2B_TEMPLATE_ID`。沙箱模板需要 Node.js 22、npm、Git、bash、tar、setsid、sha256sum、ripgrep 和支持 `--relative-to` 的 realpath；初始化需要 root 权限、useradd，以及 `/workspace` 和 `/data` 可写。宿主会创建受限开发用户，再运行 Agent 命令。如需网络代理，填写可选的 `SPIKE_PROXY_URL`。这些配置只供本机读取。

```sh
sh run.sh
```

打开 <http://127.0.0.1:7948/spike/workbench>。打开页面不会自动创建或恢复沙箱，点击工作台的连接按钮才会操作云端资源。首次连接会把 `next-app/` 的示例源码复制到新沙箱，再安装应用依赖并启动预览。

默认 Python 为 `.venv/bin/python`；可通过 `SPIKE_PYTHON` 指定已有环境。`SPIKE_E2B_ENV_FILE` 可指定其他私密配置文件，`SPIKE_STATE_DIR` 可指定独立运行数据目录。

## 当前能力与边界

- 代码浏览、真实文件修改、Next.js 实时预览、AskUser 追问与回答、执行中补充及停止。
- 本机 SQLite 保存对话和执行记录；重新打开页面可查看已经保存的消息。没有接入多用户权限、多实例 SSE 恢复或 Temporal。
- 单条消息可附 1 个附件：PNG/JPEG/WebP 最大 4,000,000 字节，UTF-8 文本或代码文件最大 256,000 字节。没有 PDF、Office、ZIP 解析。
- 视觉候选的本地 SQLite 保存与查看代码已包含。历史候选数据库、截图和聊天不会随仓库复制；新环境不会凭空出现之前的候选。TOS 没有接入，独立导出版不引用其他项目的存储实现。
- 保留 Demo 原有的手动暂停、恢复及超时暂停逻辑；这不是正式 M1 生命周期方案的实现。按 Ctrl+C 退出工作台不会立即停止云端沙箱。
- 这是单人、本机工作台。默认只监听 `127.0.0.1`，没有平台登录鉴权，不要直接作为公网服务部署。云端运行会使用自己的沙箱资源。

## Git 保存

这个仓库的 `main` 保存工作台和内置应用源码。沙箱的自动 Git 同步默认关闭；当前源文件打包上传不需要启用它。

如果之后要测试自动保存，先在本机运行数据目录 `state-next/` 中创建 `git-sync-config.json`，设置 `enabled` 为 `true`、`remote` 为 `https://github.com/judyychen/pydantic-harness-demo.git`、`branch` 为 `app-checkpoints`，并准备已登录且具有仓库写权限的 GitHub CLI。生成的应用源码只推到 `app-checkpoints`，与工作台 `main` 分开。换机或仓库访问者不要沿用仓库所有者的写入目标或凭据。

自动保存由宿主固定程序处理，模型不接触 GitHub 凭据。无变更不新增提交；推送失败不会被标成远端已保存。公开分支中的应用代码也会公开，启用前应确认生成内容适合公开。

## 离线检查

先安装 Python 依赖、构建聊天前端。以下命令为检查使用独立临时运行数据目录，避免读取正在使用的对话状态：

```sh
export SPIKE_STATE_DIR="$(mktemp -d)"
export PYDANTIC_AI_NO_BANNER=1
.venv/bin/python check_next.py
.venv/bin/python check_interactions.py
.venv/bin/python check_visual.py
.venv/bin/python check_worker_restart.py
.venv/bin/python git_sync.py --self-check
```

这些检查使用模拟模型、模拟云 SDK 和临时本地仓库，不会创建沙箱、调用真实模型或推送 GitHub。它们不代表真实云端和浏览器验收。

`requirements.txt` 和 `python-lock.txt` 沿用 Demo 已用版本：Pydantic AI 2.54.0、Harness 0.54.0、E2B 2.48.0。两个 Node 项目各自保留 `package-lock.json`。
