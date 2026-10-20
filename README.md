# 安徽文旅交通项目启动指南（Windows / Python 3.13）

本文以当前整合项目为准，覆盖首次安装、MySQL 建表与数据导入、大模型配置、前后端和爬虫启动，以及录像 P2PNet 计数。命令使用 **PowerShell**。

项目路径：`D:\李墨白\比赛项目\数字媒体\项目++++`。如果换了位置，请统一替换以下命令中的绝对路径。

已有环境、数据库和数据时，直接看第 8 节“日常启动”，不必重新安装和导入。

接收数据库与知识库运行资料快照时，参见 [server-migration/README.md](server-migration/README.md)。该包包含公开、估算及模拟数据，数据库导出已移除旧登录会话并禁用原账户，需按包内说明新建管理员。

## 1. 项目结构与运行方式

```text
项目++++/
├─ .venv/                   项目专用 Python 3.13 环境（不提交）
├─ api_platform/            D：FastAPI 后端、认证、任务调度、大模型接入
├─ data_foundation/         C：数据模型、Alembic 迁移、真实资料及导入模块
├─ intelligent_analysis/    E：RAG、客流预测、P2PNet 模型源码和权重
├─ 爬取工具/                独立爬虫、SQLite 采集库、8765 采集控制台
├─ web_dashboard/           全域总览、景区客流、假期专题等前端
├─ web_interactions/        交通、事件、问答、录像等前端及整合入口
├─ videos/                  直接放录像文件
├─ cap_images/              抽帧检测后的红点计数截图
├─ package.json             整合前端启动入口
└─ README.md
```

| 服务 | 地址 | 说明 |
|---|---|---|
| 整合前端 | http://127.0.0.1:5173 | 项目全部页面，只启动根目录这一套前端 |
| 后端 API | http://127.0.0.1:8000 | 前端通过 Vite 代理访问 `/api/v1` |
| API 文档 | http://127.0.0.1:8000/docs | 查看接口与参数 |
| 后台 Worker | 无网页端口 | 处理知识文档索引等队列任务 |
| 爬虫 UI | http://127.0.0.1:8765 | 独立采集控制台，可按需启动 |
| MySQL | 默认 127.0.0.1:3306 | 项目业务库，与爬虫 SQLite 分开 |

不要同时启动两个 5173 前端。无需单独启动一个 E 的 HTTP 服务：D 会直接调用已安装的 E 包。

## 2. 准备运行环境

需要 Git、Node.js/npm、pnpm、MySQL，以及创建项目 Python 环境的工具。本机核对环境为 Python 3.13.16、Node.js 25.0.0、pnpm 11.25.0；`package.json` 指定 pnpm 11.25.0。后端声明 Python `>=3.13,<3.14`。

### 2.1 Python 只使用项目环境，不单独安装全局 Python

**本机现有环境可直接复用**，Python 位于：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
.\.venv\python.exe -X utf8 -c "import sys; print(sys.version); print(sys.executable)"
```

本项目现有 `.venv` 是 Conda 前缀环境，所以解释器在 `.venv\python.exe`，不是 `.venv\Scripts\python.exe`。无需激活环境，直接调用完整路径即可。

新电脑或新克隆没有 `.venv` 时，可使用已安装的 Miniconda/Miniforge 创建同样结构的环境，无需单独安装系统 Python：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
conda create --prefix ".\.venv" python=3.13 pip -y
.\.venv\python.exe -X utf8 -c "import sys; print(sys.version)"
```

已有 `.venv` 时跳过创建；不要对正在使用的环境重复执行创建命令。如果使用 `uv venv` 或标准 `venv` 创建环境，其 Windows 解释器通常在 `.venv\Scripts\python.exe`，须统一替换本文所有 Python 路径，不能混用。

### 2.2 安装前端依赖

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
node --version
npm install -g pnpm@11.25.0
pnpm.cmd install --frozen-lockfile
```

已安装相同版本 pnpm 时跳过全局安装。使用 `pnpm.cmd` 可避免 PowerShell 对 `pnpm.ps1` 的执行策略限制。

### 2.3 安装后端、C、E 与爬虫依赖

全部在项目根目录执行：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
.\.venv\python.exe -X utf8 -m pip install --upgrade pip
.\.venv\python.exe -X utf8 -m pip install -e ".\api_platform[dev]"
.\.venv\python.exe -X utf8 -m pip install -e ".\data_foundation[data]"
.\.venv\python.exe -X utf8 -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.\.venv\python.exe -X utf8 -m pip install -e ".\intelligent_analysis[vision,forecast]"
.\.venv\python.exe -X utf8 -m pip install -e ".\爬取工具"
.\.venv\python.exe -X utf8 -m pip install python-dotenv "PyMySQL[rsa]"
```

这里直接安装现有源码，无需查找旧 wheel，也不要再执行依赖 `$eWheel.FullName` 的旧命令。`[vision,forecast]` 必须跟在有效包路径后面，不能单独作为安装参数。

快速检查：

```powershell
.\.venv\python.exe -X utf8 -c "import data_foundation, intelligent_analysis, anhui_crawler, torch, cv2, httpx; print('依赖导入成功'); print('PyTorch:', torch.__version__)"
```

## 3. 创建 MySQL 数据库并配置后端

### 3.1 确认 MySQL 服务运行

可以在 Windows“服务”中启动已安装的 MySQL 服务。以下命令只查看服务：

```powershell
Get-Service -Name '*mysql*'
```

已有可用数据库时复用，不要删除或重建。新库可执行：

```powershell
mysql -u root -p
```

输入 **MySQL 管理员密码**后，在 MySQL 提示符执行下面的 SQL。请先把示例密码替换为自己的密码：

```sql
CREATE DATABASE IF NOT EXISTS tourism_flow
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'tourism_app'@'127.0.0.1'
  IDENTIFIED BY 'ReplaceWithYourDatabasePassword';
GRANT ALL PRIVILEGES ON tourism_flow.* TO 'tourism_app'@'127.0.0.1';
EXIT;
```

这个数据库账号与网页登录的 `root` 不是同一个账号。已有同名 MySQL 用户时，`CREATE USER IF NOT EXISTS` 不会修改其密码，连接串应填写该账号的真实密码。

### 3.2 配置 `api_platform/.env`

首次使用才复制；已有文件时保留：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
if (-not (Test-Path -LiteralPath ".\api_platform\.env")) {
    Copy-Item -LiteralPath ".\api_platform\.env.example" -Destination ".\api_platform\.env"
}
notepad ".\api_platform\.env"
```

在文件中修改对应项，避免重复添加同名配置：

```dotenv
DATABASE_URL=mysql+pymysql://tourism_app:ReplaceWithYourDatabasePassword@127.0.0.1:3306/tourism_flow?charset=utf8mb4
DATA_DIR=./data
D_JOB_STORE_BACKEND=c_store
SECURE_COOKIES=false
ALLOWED_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173"]
CSRF_SECRET=替换为下面命令生成的随机字符串
VIDEO_STORAGE_DIR=../videos
P2PNET_BUNDLE_DIR=../intelligent_analysis/model_bundle/p2pnet
RAG_INDEX_PATH=./data/rag_index.json
VIDEO_TMP_DIR=./data/video_tmp
COUNTS_STORAGE_PATH=./data/counts
LLM_ENABLED=false
```

生成 CSRF 密钥，复制输出填到 `CSRF_SECRET`：

```powershell
.\.venv\python.exe -X utf8 -c "import secrets; print(secrets.token_urlsafe(48))"
```

数据库密码如果含 `@`、`#`、`%`、`:` 等 URL 特殊字符，需要对连接串中的密码部分进行 URL 编码。不要把 `.env` 提交到 Git，也不要用示例密码覆盖现有配置。

所有后端命令均从 `api_platform` 目录执行，以便正确读取该目录的 `.env`。

## 4. 创建或升级数据表

Alembic 的 `env.py` 要求环境变量 `DATABASE_URL`，不会自动读取 `.env`。因此在**同一个 PowerShell 窗口**先载入配置，再执行迁移：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
$env:PYTHONUTF8 = "1"
$env:DATABASE_URL = (& ..\.venv\python.exe -X utf8 -c "from app.core.config import get_settings; print(get_settings().database_url)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $env:DATABASE_URL) { throw "数据库配置读取失败，请先检查 .env" }
$env:DATA_ROOT = Join-Path (Get-Location).Path "data"
..\.venv\python.exe -X utf8 -m alembic -c "..\data_foundation\alembic.ini" upgrade head
if ($LASTEXITCODE -ne 0) { throw "迁移失败，请解决当前错误后再继续" }
..\.venv\python.exe -X utf8 -m alembic -c "..\data_foundation\alembic.ini" current
```

当前源码的迁移末端为 `local_video_counts_04`。常用表包括：

| 表 | 用途 |
|---|---|
| `users`、`sessions` | 网页用户及会话 |
| `entities`、`metrics`、`sources`、`source_assets` | 地点、指标、来源与原件 |
| `observations` | 标准业务观测 |
| `public_data_facts` | 爬虫及 C 资料的公开事实归档 |
| `project_flow_data` | 项目客流补充序列，区分公开、估算和模拟 |
| `holidays` | 假期日历及分析窗口 |
| `events`、`event_entities`、`event_history` | 事件与变更历史 |
| `knowledge_documents`、`knowledge_indexes`、`knowledge_chunks` | 知识文档及索引登记 |
| `local_video_files` | `videos` 目录录像索引 |
| `local_video_analyses`、`local_video_count_points` | 当前目录录像的 P2PNet 分析及计数 |

不要手动把 `alembic_version` 改成 head 或用 `stamp head` 跳过失败的建表。迁移报错时先确认目标库、账号权限和现有表结构。

## 5. 初始化爬虫库、导入业务数据

### 5.1 初始化独立 SQLite 采集库

爬虫 UI 不会自动创建缺失的 SQLite 库。首次启动 UI 前执行：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\爬取工具"
..\.venv\python.exe -X utf8 -m anhui_crawler --data-root ".\data\local" init
```

已有当前版本的库时可复用。旧版库提示需要迁移时，按其提示执行 `migrate`，不要删除数据库：

```powershell
..\.venv\python.exe -X utf8 -m anhui_crawler --data-root ".\data\local" migrate --report ".\data\migration.json"
```

`init` 只初始化对象和来源配置，不会凭空产生采集数据。爬虫数据保存在 `爬取工具/data/local/crawler.sqlite3`，不是项目 MySQL。

### 5.2 检查真实资料包是否完整

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 "..\data_foundation\examples\import_real_dataset.py" --manifest "..\data_foundation\real_data\2026-10-12-v2\manifest.json"
```

这条命令只校验。当前资料包应有 `manifest.json` 以及它引用的原件、CSV、浏览器存档、假期文件等；只拷贝 manifest 无法完成导入。新克隆缺少本机未提交的原件或数据包时，先恢复完整数据目录。

### 5.3 推荐：通过统一入口导入 MySQL

该入口读取后端 `.env`，会导入 C 的真实资料包、独立 SQLite 中的事实，并在存在项目补充包时导入它：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
# 先预览，不写数据库
..\.venv\python.exe -X utf8 -m scripts.import_public_data
```

**全新业务库需要先登记补充包涉及的地点。** 现有爬虫库有完整事实时，桥接导入会登记这些对象；但刚执行 `init` 的空爬虫库没有事实，不能靠它自动登记全部地点。以下命令根据本项目已有清单补齐地点目录，不生成客流数值，也不会覆盖已有地点：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
@'
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import sqlalchemy as sa
from app.core.providers import get_c_store
from data_foundation import EntityCreate
from data_foundation.db.models import Entity

root = Path.cwd().parent
package = root / 'data_foundation/real_data/project_flow/manifest.json'
if not package.is_file():
    print('没有项目补充包，跳过其地点目录登记')
    raise SystemExit(0)
needed = {r['entity_name'] for r in json.loads(package.read_text(encoding='utf-8'))['rows']}
registry = json.loads((root / '爬取工具/src/anhui_crawler/config/registry.json').read_text(encoding='utf-8'))
baseline = json.loads((root / 'data_foundation/real_data/2026-10-12-v2/manifest.json').read_text(encoding='utf-8'))
catalog = {e['name']: dict(e, id=str(uuid5(NAMESPACE_URL, 'public-entity:' + e['id']))) for e in registry['entities']}
catalog.update({e['name']: e for e in baseline['catalog']['entities']})
store = get_c_store()
added = 0
with store.write() as u:
    existing = {(e.name, e.type) for e in u.session.scalars(sa.select(Entity))}
    for name in sorted(needed):
        e = catalog[name]
        if (name, e['type']) not in existing:
            u.catalog.create_entity(EntityCreate(id=e['id'], name=name, type=e['type'], region_code=e['region_code']))
            added += 1
print('新增地点目录：', added)
'@ | ..\.venv\python.exe -X utf8 -
```

然后执行正式导入：

```powershell
# 确认预览成功后导入；--migrate 同时确保增量表已建立
..\.venv\python.exe -X utf8 -m scripts.import_public_data --apply --migrate --report ".\data\public-import-report.json"
```

当前项目补充包位于 `data_foundation/real_data/project_flow/manifest.json`，包含 2023-01-01 至 2026-10-07 的景区、铁路和机场序列，并保留公开、估算、模拟标记。以命令输出和数据清单为准，不把这部分都称为实测。

若只需要导入 C 的真实资料包，可在第 4 节已设置 `DATABASE_URL`、`DATA_ROOT` 的**同一个终端**执行以下替代命令；它不是统一导入后必须再执行的一步：

```powershell
..\.venv\python.exe -X utf8 "..\data_foundation\examples\import_real_dataset.py" --manifest "..\data_foundation\real_data\2026-10-12-v2\manifest.json" --apply
```

必须由 Python 执行 `.py` 文件；`manifest.json` 是参数，不是可执行程序。所有命令使用 `-X utf8`，避免中文 Windows 默认 GBK 导致 `UnicodeDecodeError`。

### 5.4 更新项目补充数据与坐标

已有补充包仅重新导入：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 "..\data_foundation\examples\import_project_flow.py" --apply
```

需要按已登记依据重新生成补充序列并导入时，可执行：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\爬取工具"
..\.venv\python.exe -X utf8 scripts\update_project_data.py --offline --apply
```

去掉 `--offline` 会尝试重新获取已登记的公开依据。该流程仍依赖已有真实资料、爬虫库及 `reviewed_sources.json`，不是任意网站自动爬取器。

补齐缺失经纬度：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 -m scripts.update_entity_coordinates
```

已知地点优先使用内置坐标；其他地点可能查询网络地理编码。已有完整坐标保留，无法确认的地点不会随意生成坐标。

## 6. 创建网页登录管理员

先完成建表，再执行：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 -m scripts.create_admin root --display-name "管理员"
```

按提示输入两次密码。沿用本机演示账号时，用户名为 `root`，密码为 `12345678`；新部署也可设置自己的密码。密码存入 `users` 表时会进行哈希处理。

如果提示用户名已存在，说明不应重复创建；直接使用已有密码登录。此命令不重置已有账户密码。网页没有公开注册入口；MySQL 管理员密码不能代替网页登录密码。

## 7. 配置大模型、前端真实数据模式和 P2PNet

### 7.1 OpenAI 兼容大模型接口

编辑 `api_platform/.env` 的对应配置：

```dotenv
LLM_ENABLED=true
LLM_BASE_URL=填写供应商的兼容接口基础地址
LLM_API_KEY=填写本机密钥
LLM_MODEL=填写该密钥有权限使用的模型名称
LLM_TIMEOUT_SECONDS=60
LLM_MAX_TOKENS=2048
```

本项目会在基础地址后拼接 `/chat/completions`；如果供应商要求 `/v1/chat/completions`，基础地址应带 `/v1`。也支持直接填写完整 `/chat/completions` 地址。不要重复拼接。模型名按供应商账号实际可用值填写，不直接照抄旧模板中的模型名。

手动测试一次 API 连接（会向供应商发送一条测试请求）：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 -m scripts.check_llm
```

修改配置后重启 API 和 Worker。密钥只放后端 `.env`，不放前端 `VITE_` 变量。暂不接大模型时保持 `LLM_ENABLED=false`；此时不能期待当前智能体的完整自然语言能力。

### 7.2 整合前端必须使用 API 模式

打开或创建 `web_dashboard/.env.local`：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
notepad ".\web_dashboard\.env.local"
```

至少设置：

```dotenv
VITE_DATA_MODE=api
```

保留已有其他配置，避免重复同名项。整合入口未设置 `VITE_DATA_MODE=api` 时会固定进入演示模式，使用本地静态样例；即使后端已经启动，也不会自动切换到 API 数据。

配置文件必须放在 `web_dashboard/.env.local`，不是项目根目录或 `api_platform` 目录。该文件仅包含可公开的 `VITE_DATA_MODE=api`，已从两层 Git 忽略规则中放行，可以随项目提交；后端 `.env` 仍被忽略。不要把密钥加入这个前端文件。

保存后，开发服务器通常会自动重载。刷新浏览器并使用真实账号登录；若仍显示旧页面，按 `Ctrl+F5`，未生效时在前端终端按 `Ctrl+C`，再按第 8 节命令重新启动前端。仅启动或重启后端不会改变前端数据模式。

排查时访问 `http://127.0.0.1:8000/api/v1/health/live` 和 `http://127.0.0.1:5173/api/v1/health/live`，两者都返回 `{"status":"ok"}` 表示后端存活且前端代理可达。`/api/v1/auth/me` 在未登录时返回 `401` 是正常认证状态，不代表后端未连接；健康检查本身不验证数据库业务数据是否完整。

### 7.3 核对 P2PNet 模型包

完整结构至少包括：

```text
intelligent_analysis/model_bundle/p2pnet/
├─ SHTechA.pth
├─ models/
├─ util/
└─ MODEL-RECEIPT.json
```

后端配置必须指向上述目录：

```dotenv
P2PNET_BUNDLE_DIR=../intelligent_analysis/model_bundle/p2pnet
```

不要使用旧模板的 `./data/p2pnet_bundle`，除非已将完整模型包放到那个位置。只有 `.pth` 文件、缺少模型源码时也不能运行。模型按登记摘要校验，当前网页计数使用 **P2PNet + PyTorch CPU**。

## 8. 日常启动：分别打开终端，保持运行

前置条件：MySQL 服务正在运行，依赖、数据库和 `.env` 已完成配置。以下终端相互独立，无需激活 Python 环境。

### 终端 A：启动后端 API

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

检查 http://127.0.0.1:8000/api/v1/health/live，应返回 `{"status":"ok"}`。健康检查成功只代表 API 存活，数据库和模型仍需通过实际页面确认。

### 终端 B：启动后台 Worker

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\api_platform"
..\.venv\python.exe -X utf8 -m app.worker
```

用于上传知识文档、重建索引等后台任务。当前本地目录录像的“开始计数”和数据库知识更新按钮也会唤起各自的后台处理，不要求为每次点击另开命令。

### 终端 C：启动整合前端

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++"
pnpm.cmd dev --host 127.0.0.1 --port 5173 --strictPort
```

打开 http://127.0.0.1:5173，使用第 6 节创建的网页登录账号。各次访问保持使用同一个主机名，避免在 `localhost` 与 `127.0.0.1` 间切换造成会话不一致。

### 终端 D：按需启动爬虫 UI

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\爬取工具"
..\.venv\python.exe -X utf8 scripts\dashboard.py --data-root ".\data\local" --port 8765 --open
```

访问 http://127.0.0.1:8765。首次运行之前必须完成第 5.1 节初始化，否则可能报 `sqlite3.OperationalError: unable to open database file`。

停止某服务：在对应终端按 `Ctrl+C`。修改后端 `.env` 后需重启 API/Worker，修改前端 `.env.local` 后需重启前端。已有服务占用端口时复用或停止对应服务，不要重复启动。

## 9. 首次启动后的实际操作

### 9.1 知识库与数据库 RAG

进入“智能问答 → 知识库管理”：

1. 点击“更新数据库”，将支持的数据库业务字段导出为知识资料并建立索引。
2. 等待任务完成、资料状态变为可用，再提问。
3. 可上传 TXT、Markdown、PDF 等资料，补充来源和发布时间；索引任务由 Worker 处理。
4. 后续业务数据发生变化时，再点击“更新数据库”同步。

助手有部分客流指标的直接数据库查询能力，也会检索已索引资料。它并不能任意查询所有表；录像 P2PNet 计数等数据目前没有接入助手的直接查询或数据库 RAG 导出。数据库存在记录，不代表任意措辞的问题都能被正确识别和回答。

### 9.2 录像回放、人数计数与截图

1. 把自己的录像直接放进 `项目++++/videos/` 根层，支持 MP4、WebM、MOV、MKV、AVI；浏览器可否播放还取决于内部编码。
2. 打开“录像回放”，刷新列表并选择视频。
3. 管理员选择抽帧间隔，点击“开始计数”。页面会显示进度，完成后可播放、拖动查看对应抽样时刻的人数。
4. 每个成功检测的抽样帧自动保存带红色人头检测点的 JPG，右上角显示人数。新分析按北京时间建立单层目录，例如 `cap_images/2026-10-19T18-50-12/`，该次分析的图片直接放在里面。同一秒发生重名时顺延到下一可用秒，重试同一分析沿用已有目录。
5. 分析和人数分别保存在 `local_video_analyses`、`local_video_count_points`。没有实际录制时间的视频使用相对秒数，不制造日期。
6. “固定统计区域 ROI”下方的“检测图片”区域支持依次选择检测文件夹与图片，也可点击“上一张”“下一张”浏览。计数完成会自动刷新，每15秒检查一次目录，亦可手动点击“刷新图片目录”。原先 `cap_images/<视频编号>/<分析编号>/` 中的历史截图仍可查看，界面标为“历史记录”。

文件名如 `frame_000002_000005000ms.jpg` 表示第二个抽样帧、视频第 5 秒。红点与人数使用相同统计区域和置信度规则。相同输入和参数且时间目录内截图完整时复用结果；打开已有计数结果或再次点击开始时，缺失截图、旧 UUID 目录或旧版无标记截图会触发后台补生成，完成后自动显示。

文件夹时间表示分析截图保存批次的创建时间，不是录像拍摄时间。每个新目录中的 `analysis.json` 保存视频与分析关联，`captures.json` 用于检查截图是否完整，请与图片一并保留。检测目录、图片列表与图片读取接口均需登录，只允许读取 `cap_images` 内的图片。

这里是画面可见人数估计，不是累计进出人次；各帧人数不能直接相加作为总客流。短录像也不一定满足人数预测所需历史窗口。

后端配置统一以 `api_platform/` 为路径基准，使用 `DATA_DIR=./data`、`VIDEO_STORAGE_DIR=../videos`、`CAPTURE_STORAGE_DIR=../cap_images` 和 `P2PNET_BUNDLE_DIR=../intelligent_analysis/model_bundle/p2pnet`。截图保存和读取共用 `CAPTURE_STORAGE_DIR`，不依赖终端工作目录，也不会随录像目录变化而保存到其他位置；截图目录不可写时任务报错，不会静默改存临时目录。图片接口使用相对的文件夹标识和 `/api/v1/videos/capture-image?...` 地址，浏览器无需知道服务器磁盘路径。

检测时按需创建 `cap_images` 及其时间子目录，前端不创建服务器目录。`videos` 中的录像和 `cap_images` 中的检测截图已纳入 Git；其中 MP4 使用 Git LFS 保存。克隆前先安装并初始化 Git LFS（`git lfs install`），已有克隆运行 `git lfs pull` 下载录像实体；若只拿到 LFS 文本指针，视频无法播放。后台检测在提交之后新生成的截图需要另行提交，Git 不会自动同步运行内容。

截图字体可选放在 `api_platform/assets/fonts/counting.ttf`（需支持中文并有使用授权）。未放置时使用 Pillow 自带字体显示 `People: 人数`，Windows 与 Ubuntu 均不依赖 `C:/Windows` 字体目录。

### 9.3 爬虫 UI 的操作范围

- “刷新状态”和每 15 秒自动刷新：只读取 SQLite 状态，不启动采集。
- “采集结果”的条件筛选：筛选已经采集到的数据，可导出并查看来源证据。
- “采集并同步 MySQL”：执行已配置的项目客流补充流程，不使用当前页面的筛选条件，也不会采集所有来源。
- 当前没有在 UI 上选择来源和日期后启动原始采集的入口，也没有新增事件通知入口。

需要真正启动已配置来源的采集时，另开终端执行：

```powershell
cd "D:\李墨白\比赛项目\数字媒体\项目++++\爬取工具"
# 检查已启用且到期的来源
..\.venv\python.exe -X utf8 -m anhui_crawler --data-root ".\data\local" update --due
```

或指定来源及日期范围：

```powershell
..\.venv\python.exe -X utf8 -m anhui_crawler --data-root ".\data\local" crawl --source-id caac-east-monthly --start 2023-01-01 --end (Get-Date -Format 'yyyy-MM-dd') --max-pages 20 --max-documents 50
```

原始采集完成后，如需同步到项目 MySQL，回到 `api_platform` 执行第 5.3 节的 `scripts.import_public_data --apply`。采集范围受来源配置、解析规则、请求预算和公开数据可用性限制。

## 10. 常见错误定位

| 现象 | 处理 |
|---|---|
| 找不到 Python | 使用当前环境真实路径 `.venv\python.exe`；不要只输入 `python` |
| `Invalid requirement: '[vision,forecast]'` | 使用第 2.3 节的完整源码路径安装命令，不用未赋值的 wheel 变量 |
| Alembic 要求 `DATABASE_URL` | 在同一终端执行第 4 节读取 `.env` 的命令，再迁移 |
| MySQL `Access denied` / 连接失败 | 核对服务、端口、库名、账号授权、密码和密码 URL 编码；网页登录账号无关 |
| 找不到 `public_data_facts` 等表 | 核对是否连接同一个业务库，并完成 `upgrade head` |
| PowerShell 说 `manifest.json` 无法运行 | 执行 Python 导入脚本，把 JSON 放在 `--manifest` 后 |
| `UnicodeDecodeError: gbk` | 使用 `-X utf8`，确认取得的是当前 UTF-8 修正版代码 |
| 爬虫 SQLite 无法打开 | 先执行爬虫 `init`，并明确指定 `--data-root .\data\local` |
| 导入提示原件缺失或摘要不符 | 恢复完整数据包和爬虫原件；不要伪造文件或修改校验摘要 |
| 后端已启动，前端仍是静态演示内容 | 按第 7.2 节在 `web_dashboard/.env.local` 设置 `VITE_DATA_MODE=api`，刷新并登录；未生效时重启前端。缺少该配置会固定使用演示数据，不会随后端启动自动切换 |
| 登录失败 | 确认 `users` 表中有管理员、密码正确、后端连对数据库；多次失败后需等待限速窗口 |
| 知识问答没有资料 | 检查 LLM 配置、索引任务状态；执行“更新数据库”；确认问题在已支持的数据范围内 |
| 大模型 401/404/429 或 503 | 核对 key、模型权限、额度、基础地址是否缺少或重复 `/v1`，运行 `scripts.check_llm` |
| P2PNet 未配置或计数失败 | 检查完整模型包和 `P2PNET_BUNDLE_DIR`，检查 `cap_images` 写入权限与空间，并查看 API 日志 |
| 端口被占用 | 复用正在运行的服务，或在原终端 Ctrl+C 停止后重启 |

本 README 的命令入口已按当前源码核对；真实资料包校验和统一导入预览已通过。新电脑的 MySQL 账号、模型与数据文件、网络 API 密钥仍须按本机实际情况配置。
