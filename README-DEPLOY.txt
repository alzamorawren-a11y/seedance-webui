Seedance 视频生成网站 - 云端部署说明（Render 免费层方案 C）
================================================================

部署前须知
----------
- Render 免费层：512MB 内存，首次访问需冷启动（约 30-60 秒），长时间无流量会休眠。
- 免费层数据易失：SQLite 数据库、上传素材、下载的视频都存在实例本地磁盘，
  实例重启/重新部署会被清空。重要数据请及时下载备份。
- 部署后需重新在管理端配置：平台接入地址 + API Key + 模型积分单价。

部署步骤
----------
1. 把本目录推送到 GitHub（公有/私有仓库均可，需在 GitHub 创建账号和仓库）。
   git init
   git add .
   git commit -m "seedance webui"
   git remote add origin https://github.com/你的用户名/仓库名.git
   git push -u origin main

2. 打开 Render Blueprint 一键部署链接（把下面链接换成你的仓库地址）：
   https://dashboard.render.com/blueprint/new?repo=https://github.com/你的用户名/仓库名

3. 首次会要求登录/注册 Render 账号，并授权连接 GitHub。

4. 确认服务配置（免费 Free 实例，Region 可选 Singapore 离国内更近），点 Apply。

5. 等部署完成（约 3-5 分钟），打开服务地址 https://xxx.onrender.com。

6. 用 admin / admin123 登录管理端（https://xxx.onrender.com/admin），
   在“系统设置”填入平台接入地址和 API Key，“模型管理”设置积分单价，
   然后创建用户并发放积分即可使用。

管理员默认账号：admin / admin123（登录后请立即修改密码）。
