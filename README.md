<p align="center">
  <img src="docs/images/logo.png" alt="duck-harness" width="96" />
</p>

<h1 align="center">duck-harness</h1>

<p align="center">
  跑在你自己电脑上的编程 Agent。<br/>
  打开一个会话，它就能读代码、改文件、跑命令，并把这次对话留下来。
</p>

<p align="center">
  <img src="docs/images/app.png" alt="duck-harness 界面" width="860" />
</p>

网页和桌面用的是同一套界面。后端是 Python，会话、记忆、任务和用量记在 MySQL。模型可以接 DeepSeek，也可以接本机 Ollama。

## 能做什么

一个会话里只有一个主 Agent。它自己做不完的调查，可以派子代理去查，再把结果收回来。这不是把几个会话拉进同一个群聊。

- **工具循环。** 模型决定下一步，后端在本机执行，结果写回对话，再继续问模型，直到它给出正文或你停掉这一轮。
- **本机操作。** 读改文本、PDF、Word 和 Jupyter，搜索文件，在 PowerShell 里执行命令，也能把长命令放到后台。
- **联网。** 搜索和抓取网页，再把需要的部分放进上下文。
- **记得住。** 长期记忆、当前会话的任务，以及工作区里的 `AGENTS.md` / `HARNESS.md`，会在下一轮继续生效。
- **权限。** `auto` 直接执行，`plan` 只读并写计划，`ask` 在改文件或跑命令前等你点允许。密钥文件和少数破坏性命令即使在自动模式下也会被拦住。
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

浏览器会打开 http://127.0.0.1:8787 。桌面窗口用 `启动桌面.bat`，如果服务还没起来，它会自己启动同一个后端。两个入口可以同时开，用的是同一份会话数据。

DeepSeek 密钥写在 `backend/.env` 的 `DEEPSEEK_API_KEY`，或者在界面的设置里保存。这个文件已经在 `.gitignore` 里，不要提交。

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

表在服务启动时创建，结构也写在 `sql/init.sql`。`AUTO_SEED=true` 时，第一次打开会放进一组示例会话。

## 目录

```text
backend/     FastAPI、工具循环、权限和测试
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
