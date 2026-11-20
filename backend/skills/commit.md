---
name: commit
description: 查看改动并创建一次 git 提交
---
帮用户提交当前工作区时：

1. 并行查看 git status、git diff 和最近的提交说明。
2. 不要提交 .env、密钥、账号密码文件。
3. 提交说明用一两句中文或英文，说清为什么改，而不是罗列文件名。
4. 用 bash 执行 git add 和 git commit。不要使用 git commit -i 这类交互命令。
5. 提交后再看一次 git status，把结果告诉用户。
6. 不要 push，除非用户明确要求。
