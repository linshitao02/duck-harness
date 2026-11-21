<p align="center">
  <img src="docs/images/logo.png" alt="duck-harness" width="96" />
</p>

<h1 align="center">duck-harness</h1>

<p align="center">
  跑在你自己电脑上的编程 Agent。<br/>
  它能读代码、改文件、跑命令，也能把几只 Agent 拉进同一个群里交接。
</p>

<p align="center">
  <img src="docs/images/app.png" alt="一次真实任务：实现中位数并跑通测试" width="920" />
</p>

<p align="center">
  <sub>工作区里原来没有这两个文件。它写好 <code>stats.py</code> 和 <code>test_stats.py</code>，<code>python -m unittest test_stats</code> 的 3 个测试全部通过。</sub>
</p>

网页和桌面用的是同一套界面、同一份会话。后端是 Python，会话、记忆、任务和用量记在 MySQL。模型可以接 DeepSeek，也可以接本机 Ollama。

## 演示

下面三张图都是本机真实跑出来的，没有摆拍。

**几只 Agent 在一个群里把事情做完。** 用户 `@项目经理` 做班次加班费。项目经理拆开任务交给实现工程师，验收工程师独立跑过测试，再交回项目经理。

<p align="center">
  <img src="docs/images/group.png" alt="班次加班费群聊：实现、验收、交回" width="920" />
</p>

**新会话先问两轮，再开始干活。** 这只「技术架构师」问了做什么、做到哪一层，问完就停住，等你把具体的事发过来。中途直接打字，提问也会结束。

<p align="center">
  <img src="docs/images/intake.png" alt="新会话的两轮开场提问" width="920" />
</p>

**每只 Agent 是一只鸭子。** 创建时从八只里挑一只。颜色每次打开窗口随机取一个。已经建好的会话仍用原来的图标。

<p align="center">
  <img src="docs/images/agents.png" alt="创建新 Agent 时的鸭子和随机颜色" width="920" />
</p>

## 能做什么

- **工具循环。** 模型决定下一步，后端在本机执行，结果写回对话，再继续问模型，直到它给出正文，或你停掉这一轮。
- **群聊。** `@名字` 指定下一个开口的成员。成员可以交接，也可以私聊。每个成员仍是自己的会话。
- **开场提问。** 新建的普通会话先问最多两轮选择题。问清楚之后，你把要做的事发过来。
- **本机操作。** 读改文本、PDF、Word 和 Jupyter，搜索文件，在 PowerShell 里执行命令，也能把长命令放到后台。
- **联网。** 搜索和抓取网页，再把需要的部分放进上下文。
- **公式。** 对话里的数学公式会直接渲染出来。
- **记得住。** 长期记忆、当前会话的任务，以及工作区里的 `AGENTS.md` / `HARNESS.md`，会在下一轮继续生效。
- **权限。** `auto` 直接执行，`plan` 只读并写计划，`ask` 在改文件或跑命令前等你点允许，`edits` 改文件免问、命令仍要确认，`dontAsk` 把需要确认的操作直接拒绝。密钥文件和少数破坏性命令即使在自动模式下也会被拦住。
- **删会话时收干净。** 这次上传的副本，和只属于这次对话的撤销记录，会跟着会话一起删掉。工作区里的项目文件和长期记忆还留着。
- **扩展。** 技能、MCP、定时任务，以及 explore / plan / general 子代理。

## 快速开始

需要 Python 3.11+、Node.js、MySQL 8。

```bat
mysql -u root -p < sql\init.sql
cd backend
copy .env.example .env
pip install -r requirements.txt
cd ..\frontend
npm install
npm run build
cd ..
启动网页.bat
```

浏览器会打开 http://127.0.0.1:8787 。桌面窗口用 `启动桌面.bat`。服务还没起来时，它会自己启动同一个后端。两个入口可以同时开。

DeepSeek 密钥写在界面的设置里，也可以写在 `backend/.env` 的 `DEEPSEEK_API_KEY`。`.env` 已经在 `.gitignore` 里，不要提交。

本机如果用 Anaconda 的 `agent` 环境，两个启动脚本会优先找这个环境里的 Python。

## 配置

`backend/.env.example` 里是本地默认值：

| 变量 | 作用 |
| --- | --- |
| `DATABASE_URL` | MySQL 连接串。示例库名 `harness`，账号 `harness` |
| `DEEPSEEK_API_KEY` | DeepSeek 密钥。留空时可以改在界面里填写 |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `OLLAMA_BASE_URL` | 默认 `http://127.0.0.1:11434` |
| `HARNESS_WORKSPACE` | 新会话的默认工作区。留空时用本仓库目录 |

`sql/init.sql` 用来建库和基础表。服务启动时会建表，并补上后来增加的字段。`AUTO_SEED=true` 时，第一次打开会放进一组示例会话。

## 目录

```text
backend/     FastAPI、工具循环、群聊、权限和测试
frontend/    Vue 3 + Pinia 界面
desktop/     pywebview 桌面壳
sql/         MySQL 初始结构
docs/        架构说明和界面图
```

更细的运行说明在 `docs/架构说明.md`。

## 开发

```bat
cd backend
python -m pytest -q
```

`backend/scripts/verify_live.py` 会真实请求 DeepSeek 和 Ollama。跑它之前要先配好密钥，并确认 Ollama 已经启动。

## 许可

[MIT](LICENSE)
