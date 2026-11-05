# Luna_QQbot

一个 QQ 群机器人，角色是露娜。能接群聊、找图、做 Q 弹动图、总结群聊，也会去 QQ 频道发美图和梗图。

## 功能

- 群聊对话：被 @、提到名字或发 `/露娜` 时接话，带上下文和身份判断
- 找图：按角色名或英文 tag 从图库搜图，最多两张
- Q 弹动图：把图片揉成果冻，导出 GIF
- 聊天总结：把当天群聊压成一张卡片
- 自动任务：主动插话、B 站分享、晚安、节日祝福、频道发帖
- 输入检测：刷屏限流，拦截伪系统消息和套提示词
- 玩家身份卡：本地统计昵称、发言数和话题，不额外调模型
- 提醒：对姐姐说「明天下午三点提醒我交表」，到点在群里提醒
- 小工具：天气、时间、算数，命中才查，不占普通聊天的 prompt
- 长期记忆：聊天记录索引进 SQLite，问「还记得上次……」时检索旧消息

## 需要

- Windows + Python 3.10 以上（字体用的是系统微软雅黑）
- QQ 开放平台的机器人 AppID 和 AppSecret
- AI API Key（对话、总结、看图）
- 想用频道发帖的话，还需要一个 QQ 频道和一个 CloudBase 静态托管

## 安装

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 配置

```powershell
Copy-Item scripts\qq-channel.config.example.json scripts\qq-channel.config.json
```

然后填 `scripts/qq-channel.config.json`：

| 字段 | 说明 |
|---|---|
| `appId` / `clientSecret` | QQ 机器人凭证 |
| `deepseekApiKey` | 也可以改用环境变量 `DEEPSEEK_API_KEY` |
| `groupOpenid` | 主群 openid |
| `testGroupOpenids` / `dailyGroupOpenids` / `extraGroupOpenids` | 其他群 |
| `sisterOpenid` | 机器人唯一认的「姐姐」的 openid |
| `helpUrl` | 帮助卡片后面附的说明链接，留空就不发链接 |
| `forumGuildId` | 频道 ID，只有频道发帖需要 |
| `forumImageChannelId` / `forumMemeChannelId` | 美图、梗图发到哪个子频道 |
| `cloudbaseEnv` / `cloudbaseDomain` | 图片转存用的 CloudBase 环境和域名 |

群 openid 不是群号。机器人收到第一条群消息后会把群写进 `logs/groups.json`，也可以从日志里找。

## 运行

```powershell
python scripts\qq-sticker-bot.py
```

想让它开机自启，用「任务计划程序」建一个任务，程序填虚拟环境里的 `pythonw.exe`，参数填 `scripts\qq-sticker-bot.py`。

## 指令

| 指令 | 用法 |
|---|---|
| `/帮助` | 发一张说明卡片 |
| `/露娜 问题` | 和露娜对话 |
| `/找图 角色名 [1\|2]` | 找图，例：`/找图 角色名`、`/找图 作品_角色名 2` |
| `/Q弹` | 图片和指令放同一条消息，或引用图片后发 |
| `/聊天总结` | 总结当天群聊 |
| `/对着露娜发电` | 三段蔑视表情，第三次禁言 10 分钟 |

## 自动任务

| 时间 | 任务 |
|---|---|
| 08:00–22:30 | 主动插话、被 @ 接话 |
| 10:00–23:00 | B 站分享 |
| 22:30 | 晚安 |
| 09:00 | 节日祝福 |
| 周六日 09:00 / 16:00 | 频道美图 |
| 周六日 12:30 | 频道梗图 |

主动插话默认关闭，在配置里把 `proactive` 改成 `true` 再开。

## 数据目录

运行后会生成：

- `logs/`：日志、群聊记录、token 统计、频道发帖状态
- `picture/`：找图缓存

这些都不要提交到仓库。

## 限制

- QQ 官方接口只能拿到 @ 机器人的群消息；群设置成「接收全部消息」才能记录完整群聊。
- 主动消息可能要过平台审核，不保证立刻出现。
- 频道发帖只对私域机器人开放，帖子评论还要单独申请白名单权限。
- 找图源是 safebooru、yande.re、wallhaven，网络不稳时会失败。
- `/对着露娜发电` 用的三张图在 `表情包/sticker-1.jpg` 到 `sticker-3.jpg`，可以直接换成自己的。
- 想让她回答角色设定，可以自己写一份 `scripts/luna-lore.txt`，没有也能正常聊天。

## 许可

MIT
