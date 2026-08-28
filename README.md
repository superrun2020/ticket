# PostPilot Ticket Desk

一个可运行的多邮箱邮件工单原型：统一收件箱、邮件自动建单、客户回复回流、工单内回复、状态/优先级管理，以及建单确认邮件。

## 启动

```bash
cd ticket-system
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --reload --port 8010
```

访问 <http://127.0.0.1:8010>。点击“模拟收到新邮件”可零配置体验自动建单和确认邮件；待发邮件保存在 SQLite `outbox` 表中，可通过 `GET /api/outbox` 查看。

## 接入真实邮箱

复制 `mailboxes.example.json` 为 `mailboxes.json`，每个项目邮箱添加一项，并把密码放在条目 `password_env` 指定的环境变量中（不要把密码写入 JSON）：

```bash
export TICKET_MAIL_PROJECT_A_PASSWORD='应用专用密码'
python3 -m uvicorn app:app --port 8010
```

后台线程默认每 30 秒轮询所有已启用邮箱的 IMAP，并消费 SQLite 待发队列走 SMTP。它支持 UID/Message-ID 去重、主题中的 `[TKT-xxxx]` 自动归并、客户回信重开、发送失败最多重试 5 次。可用 `TICKET_MAIL_POLL_SECONDS` 调整间隔，或用 `TICKET_MAIL_WORKER=0` 禁用真实收发。

首次连接默认仅建立当前 UID 基线，避免把历史邮件全部当作新邮件并批量发送确认信；确需导入历史邮件时，停服备份后设置 `TICKET_MAIL_BACKFILL=1` 再启动。

也可设置 `TICKET_MAILBOX_SOURCE=mysql`，由运行时环境提供 `TICKET_SOURCE_DB_*` 变量，系统会只读加载 `project_mailboxes` 中所有启用邮箱；工单数据仍保存在本系统自己的 SQLite 中。

数据只写入本目录的 `data/tickets.db`。生产环境建议优先改用 Gmail API / Microsoft Graph OAuth，并增加登录权限、附件存储和队列进程级锁。
