# 测试dedicated和shared模式的SSE流式输出

/Users/admin/miniforge3/envs/a2a/bin/python /Users/admin/Documents/yifu/comworker/call_agent_api.py 
测试 dedicated 账号 SSE:
[INFO] username=user2 runtime_mode=dedicated session_key=agent:main:session-1775998834223-7a3b67
[INFO] send_result={"ok": true, "runId": "1aa23a21-6019-456b-85f8-7c696d006c79"}
[user2:dedicated] state=delta
[user2:dedicated] delta: 我
[user2:dedicated] state=delta
[user2:dedicated] delta: 我是 **Medclaw助手**，来自 Info X Med（医赋科技）平台，为医学专业人士提供一站式学术支持服务。🧬

我精通论文检索与
[user2:dedicated] state=delta
[user2:dedicated] delta: 我是 **Medclaw助手**，来自 Info X Med（医赋科技）平台，为医学专业人士提供一站式学术支持服务。🧬

我精通论文检索与解读、循证医学问答、文献思维导图、科研空白点挖掘，以及学术PPT制作。
[user2:dedicated] state=delta
[user2:dedicated] delta: 我是 **Medclaw助手**，来自 Info X Med（医赋科技）平台，为医学专业人士提供一站式学术支持服务。🧬

我精通论文检索与解读、循证医学问答、文献思维导图、科研空白点挖掘，以及学术PPT制作。📄

我的使命是"**技术数据驱动，知识赋能医生**"——助你更高效地获取、理解和运用医学知识。
[user2:dedicated] state=delta
[user2:dedicated] delta: 我是 **Medclaw助手**，来自 Info X Med（医赋科技）平台，为医学专业人士提供一站式学术支持服务。🧬

我精通论文检索与解读、循证医学问答、文献思维导图、科研空白点挖掘，以及学术PPT制作。📄

我的使命是"**技术数据驱动，知识赋能医生**"——助你更高效地获取、理解和运用医学知识。💡
[user2:dedicated] state=final
[user2:dedicated] payload: {"runId": "1aa23a21-6019-456b-85f8-7c696d006c79", "sessionKey": "agent:main:session-1775998834223-7a3b67", "seq": 7, "state": "final", "message": {"role": "assistant", "content": [{"type": "text", "text": "我是 **Medclaw助手**，来自 Info X Med（医赋科技）平台，为医学专业人士提供一站式学术支持服务。🧬\n\n我精通论文检索与解读、循证医学问答、文献思维导图、科研空白点挖掘，以及学术PPT制作。📄\n\n我的使命是\"**技术数据驱动，知识赋能医生**\"——助你更高效地获取、理解和运用医学知识。💡"}], "timestamp": 1775998842102}}
[INFO] run_result={"runId": "1aa23a21-6019-456b-85f8-7c696d006c79", "status": "ok", "startedAt": null, "endedAt": 1775998842102, "error": null}
[INFO] final_message_count=2
[INFO] final_last_message={"role": "assistant", "content": " 我是 **Medclaw助手**，来自 Info X Med（医赋科技）平台，为医学专业人士提供一站式学术支持服务。🧬\n\n我精通论文检索与解读、循证医学问答、文献思维导图、科研空白点挖掘，以及学术PPT制作。📄\n\n我的使命是\"**技术数据驱动，知识赋能医生**\"——助你更高效地获取、理解和运用医学知识。💡", "timestamp": "2026-04-12T13:00:35.731Z"}

测试 shared 账号 SSE:
[INFO] username=share2 runtime_mode=shared session_key=agent:usr_92eb50f312bf49258667abb2:session-1775998846442-a48d32
[INFO] send_result={"ok": true, "runId": "92d89bf1-ee7e-4849-9e6a-a033f7c23c85", "session_key": "agent:usr_92eb50f312bf49258667abb2:session-1775998846442-a48d32"}
[share2:shared] state=delta
[share2:shared] delta: ComWorker 是一个个人 AI 助手平台，让你把 AI 接入自己的生活——邮件、日历、文件、消息应用，一网打尽。它支持多渠道接入（Telegram、Discord、WhatsApp、Signal 等），让你随时随地通过习惯的工
[share2:shared] state=delta
[share2:shared] delta: ComWorker 是一个个人 AI 助手平台，让你把 AI 接入自己的生活——邮件、日历、文件、消息应用，一网打尽。它支持多渠道接入（Telegram、Discord、WhatsApp、Signal 等），让你随时随地通过习惯的工具跟 AI 对话。通过技能（Skills）和工作区（Workspace）机制，ComWorker 的 AI 能真正记住你、了解你，而不是每次都从零开始。
[share2:shared] state=final
[share2:shared] payload: {"runId": "92d89bf1-ee7e-4849-9e6a-a033f7c23c85", "sessionKey": "agent:usr_92eb50f312bf49258667abb2:session-1775998846442-a48d32", "seq": 4, "state": "final", "message": {"role": "assistant", "content": [{"type": "text", "text": "ComWorker 是一个个人 AI 助手平台，让你把 AI 接入自己的生活——邮件、日历、文件、消息应用，一网打尽。它支持多渠道接入（Telegram、Discord、WhatsApp、Signal 等），让你随时随地通过习惯的工具跟 AI 对话。通过技能（Skills）和工作区（Workspace）机制，ComWorker 的 AI 能真正记住你、了解你，而不是每次都从零开始。"}], "timestamp": 1775998855430}}
[INFO] run_result={"runId": "92d89bf1-ee7e-4849-9e6a-a033f7c23c85", "status": "ok", "startedAt": null, "endedAt": 1775998855429, "error": null}
[INFO] final_message_count=2
[INFO] final_last_message={"role": "assistant", "content": "ComWorker 是一个个人 AI 助手平台，让你把 AI 接入自己的生活——邮件、日历、文件、消息应用，一网打尽。它支持多渠道接入（Telegram、Discord、WhatsApp、Signal 等），让你随时随地通过习惯的工具跟 AI 对话。通过技能（Skills）和工作区（Workspace）机制，ComWorker 的 AI 能真正记住你、了解你，而不是每次都从零开始。", "timestamp": "2026-04-12T13:00:47.975Z"}

Process finished with exit code 0
