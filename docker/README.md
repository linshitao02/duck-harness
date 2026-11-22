# 在 Ubuntu 上用 Docker 运行

服务器需要 Docker Engine 和 Compose 插件。不需要安装 MySQL。

```bash
cd docker
cp .env.example .env
# 编辑 .env，至少填上 DEEPSEEK_API_KEY。用本机 Ollama 时可以留空。
docker compose up -d --build
```

浏览器打开 `http://服务器地址:8787`。先播放启动动画，再注册或登录。登录后会记住状态。

会话、上传文件和密钥保存在卷 `duck-data`。设置里保存的密钥会加密，钥匙也在这个卷里。容器删掉再重建，这份数据还在。要改服务器上的项目目录时，把 `.env` 里的 `HARNESS_WORKSPACE_HOST` 写成那个目录的绝对路径，并让 uid 1000 可以写入。

已经有 MySQL 时，把 `DATABASE_URL` 写成连接串。连得上就用 MySQL，连不上会自动回到容器里的 SQLite。

本机 Ollama 用 `http://host.docker.internal:11434`。Ollama 需要允许 Docker 来源访问。

停掉服务：

```bash
docker compose down
```

`docker compose down -v` 会连数据卷一起删掉。
