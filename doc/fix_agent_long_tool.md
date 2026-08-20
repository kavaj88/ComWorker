现在的SSE流式输出可能会有这个问题吗？下面的分析对吗？
      agent 执行工具（生成 PPT）时，gateway 发出的是 [exec] 事件，不是 chat 事件。但 bridge 的 SSE 只转发 chat 事件，所以前端在第一个
      final（文字回复）后完全失去了对 agent 活动状态的感知，就会产生一个中断


  问题根源

   1. Gateway 发出的事件类型：
      - chat 事件：用于聊天文本增量（delta）和最终回复（final）
      - agent 事件：用于 agent 内部事件，包括工具执行（stream: "tool"）

   2. Bridge 的 SSE 过滤逻辑（comworker/bridge/routes/events.ts:18-21）：
      const listener = (evt: GatewayEvent) => {
        // Only forward chat events (delta, final, started, error, aborted)
        if (evt.event === "chat") {
          res.write(`data: ${JSON.stringify(evt)}\n\n`);
        }
      };

   3. Platform 的 SSE 代理（platform/app/routes/proxy.py:193-212）：
      - 只是简单转发 bridge 的 SSE 流，没有额外处理

  具体场景

  当 agent 执行工具（如生成 PPT）时：
   1. Gateway 发出 agent 事件（stream: "tool"）
   2. Bridge 的 SSE 只转发 chat 事件，忽略 `agent` 事件
   3. 前端收到第一个 final 事件（文字回复）后，无法再感知 agent 的工具执行活动
   4. 前端认为响应已结束，实际上 agent 可能还在执行耗时工具（如生成 PPT）

  影响

   - 前端 UI 可能显示"响应已完成"
   - 用户不知道 agent 还在后台工作
   - 无法显示工具执行进度或状态

  解决方案

  需要修改 comworker/bridge/routes/events.ts，让它也转发 agent 事件：

   const listener = (evt: GatewayEvent) => {
     // Forward both chat and agent events
     if (evt.event === "chat" || evt.event === "agent") {
       res.write(`data: ${JSON.stringify(evt)}\n\n`);
     }
   };

✦ 这样前端就能接收到工具执行事件，可以正确显示 agent 的活动状态。

⏺ 你的分析基本正确，但有一些细节需要补充和修正：

  确认的部分

  1. Bridge 确实只转发 chat 事件 — comworker/bridge/routes/events.ts 第 23-28 行明确只过滤 event === "chat"，agent 事件被静默丢弃。
  2. Platform proxy 是透传的 — platform/app/routes/proxy.py 只做字节级转发，不做任何过滤或变换，所以问题出在 bridge 层。
  3. 前端会失去对 agent 工具执行的感知 — 收到 final 后没有后续事件，前端无法知道 agent 还在后台执行工具。

  需要进一步确认的点

  在确定修复方案前，需要确认一个关键问题：gateway 是否真的会发出 event: "agent" 类型的事件？

  从 comworker/src/gateway/server-chat.ts 的代码看，目前发现的事件发射都是 broadcast("chat", ...) 和 nodeSendToSession(sessionKey, "chat",
  ...)。如果 gateway 本身没有发出 agent 事件，那问题就不仅仅是 bridge 过滤的问题，还需要在 gateway 层添加工具执行状态的事件发射。

  需要我去检查 gateway 中是否有 agent 事件的发射逻辑吗？


    Gateway 确实会发出 "agent" 事件，而且内容非常丰富：

  Gateway 发出的 agent 事件详情

  在 server-chat.ts 中（第 747、773、796 行），gateway 通过 broadcast("agent", payload) 发出事件，payload 结构为：

  {
    runId: string;
    seq: number;
    stream: "lifecycle" | "tool" | "assistant" | "error";
    ts: number;
    data: Record<string, unknown>;
    sessionKey?: string;
  }

  其中 stream: "tool" 时，data.phase 包含："start" | "call" | "result" | "partial" — 完整覆盖了工具执行的生命周期。

  问题链条确认

  Gateway 发出 agent 事件 (stream: "tool", phase: "start"/"call"/"result")
      ↓
  Bridge 的 SSE listener 只检查 evt.event === "chat" → agent 事件被丢弃
      ↓
  Platform proxy 透传 bridge 的流 → 也没有 agent 事件
      ↓
  前端收到 chat final 后无后续事件 → 误认为响应结束

  修复方案

  你提出的方案是对的，修改 comworker/bridge/routes/events.ts：

  const listener = (evt: GatewayEvent) => {
    if (evt.event === "chat" || evt.event === "agent") {
      res.write(`data: ${JSON.stringify(evt)}\n\n`);
    }
  };

  另外，前端也需要相应处理 agent 事件，至少：
  - 收到 agent 事件（stream: "tool", phase: "start"）时显示工具执行状态
  - 收到 agent 事件（stream: "tool", phase: "result"）时更新完成状态
  - 收到 agent 事件（stream: "lifecycle"）时感知 agent run 的开始/结束


Fix: SSE 转发 agent 事件，前端感知工具执行状态

 Context

 当 agent 执行工具（如生成 PPT）时，gateway 发出 agent 事件（stream: "tool"），但 bridge 的 SSE 只转发 chat 事件。前端收到 chat final 后 3 秒
 debounce 结束就认为响应完成，实际上 agent 可能还在执行耗时工具。用户看到"响应已完成"但后台仍在工作。

 修改文件

 1. Bridge SSE 事件转发

 文件: comworker/bridge/routes/events.ts

 修改 listener，增加转发 agent 事件：
 const listener = (evt: GatewayEvent) => {
   if (evt.event === "chat" || evt.event === "agent") {
     res.write(`data: ${JSON.stringify(evt)}\n\n`);
   }
 };
 更新注释说明转发的事件类型。

 2. 前端 SSE 事件处理

 文件: frontend/src/pages/Chat.tsx

 在 sse.onmessage 中增加对 agent 事件的处理：

 - 收到 agent 事件且 stream === "tool" 时：
   - phase: "start" / "call" → 取消 debounce timer，保持 agentRunning = true
   - phase: "result" → 不做特殊处理（等后续 chat final 来结束）
 - 收到 agent 事件且 stream === "lifecycle" 时：
   - 如果是 run 结束事件，确保清理状态

 关键逻辑：当 chat final 的 3 秒 debounce timer 在运行时，如果收到新的 agent tool 事件，说明 agent 还在工作，需要取消 timer 并保持加载状态。

 3. 前端通知组件（可选）

 文件: frontend/src/components/NotificationProvider.tsx

 如果需要在通知中也感知 agent 工具执行状态，可同步更新。优先级低，可后续处理。

 验证

 1. 启动项目，发送一个需要工具执行的请求（如生成 PPT）
 2. 观察浏览器控制台是否收到 agent 事件
 3. 确认前端在工具执行期间保持加载状态（不会提前显示"完成"）
 4. 确认正常聊天（无工具执行）的流程不受影响


 ⏺ Update(comworker/bridge/routes/events.ts)
  ⎿  Added 3 lines, removed 2 lines
      21      res.write(": connected\n\n");
      22
      23      const listener = (evt: GatewayEvent) => {
      24 -      // Only forward chat events (delta, final, started, error, aborted)
      25 -      if (evt.event === "chat") {
      24 +      // Forward chat events (delta, final, started, error, aborted)
      25 +      // and agent events (tool execution lifecycle, assistant streaming)
      26 +      if (evt.event === "chat" || evt.event === "agent") {
      27          res.write(`data: ${JSON.stringify(evt)}\n\n`);
      28        }
      29      };

⏺ Now let me read the full Chat.tsx SSE handling section to make a precise edit.

  Read 1 file (ctrl+o to expand)

⏺ Now I'll add the handleAgentEvent callback and wire it into the SSE onmessage handler.

⏺ Update(frontend/src/pages/Chat.tsx)
  ⎿  Added 51 lines
      497      }
      498    }, [fetchSessions])
      499
      500 +  // Handle agent events (tool execution, lifecycle) to maintain loading state
      501 +  const handleAgentEvent = useCallback((payload: any) => {
      502 +    const { stream, data, sessionKey } = payload
      503 +    const currentKey = activeSessionKeyRef.current
      504 +    if (!sessionKey || !currentKey) return
      505 +
      506 +    const normalizedGw = sessionKey.replace(/:/g, '')
      507 +    const normalizedActive = currentKey.replace(/:/g, '')
      508 +    const isCurrentSession = normalizedGw === normalizedActive || sessionKey === currentKey
      509 +    if (!isCurrentSession) return
      510 +
      511 +    if (stream === 'tool') {
      512 +      const phase = data?.phase
      513 +      console.log('[SSE] agent tool event:', { phase })
      514 +      // Tool starting/calling — agent is still working, cancel any completion timer
      515 +      if (phase === 'start' || phase === 'call') {
      516 +        if (sseFinalTimerRef.current) {
      517 +          clearTimeout(sseFinalTimerRef.current)
      518 +          sseFinalTimerRef.current = null
      519 +        }
      520 +        setAgentRunning(true)
      521 +      }
      522 +    } else if (stream === 'lifecycle') {
      523 +      const phase = data?.phase
      524 +      console.log('[SSE] agent lifecycle event:', { phase })
      525 +      // Agent run ended — no more events expected, allow completion
      526 +      if (phase === 'end') {
      527 +        // Use the same debounce pattern as chat final
      528 +        if (sseFinalTimerRef.current) clearTimeout(sseFinalTimerRef.current)
      529 +        sseFinalTimerRef.current = setTimeout(() => {
      530 +          const key = activeSessionKeyRef.current
      531 +          if (key) {
      532 +            getSession(key).then(detail => {
      533 +              setMessages(detail.messages || [])
      534 +              setStreamingText('')
      535 +              setSending(false)
      536 +              setAgentRunning(false)
      537 +              sseCompletedRef.current = true
      538 +              fetchSessions()
      539 +            }).catch(() => {
      540 +              setStreamingText('')
      541 +              setSending(false)
      542 +              setAgentRunning(false)
      543 +              sseCompletedRef.current = true
      544 +            })
      545 +          }
      546 +        }, 1000)
      547 +      }
      548 +    }
      549 +  }, [fetchSessions])
      550 +
      551    // Connect SSE on mount
      552    useEffect(() => {
      553      console.log('[SSE] useEffect 触发')

⏺ Now wire handleAgentEvent into the onmessage handler:

⏺ Update(frontend/src/pages/Chat.tsx)
  ⎿  Added 2 lines
      572          const msg = JSON.parse(evt.data)
      573          if (msg.event === 'chat' && msg.payload) {
      574            handleChatEvent(msg.payload)
      575 +        } else if (msg.event === 'agent' && msg.payload) {
      576 +          handleAgentEvent(msg.payload)
      577          }
      578        } catch {
      579          // ignore

⏺ Now update the useEffect dependency array to include handleAgentEvent:

  Read 1 file (ctrl+o to expand)

⏺ Update(frontend/src/pages/Chat.tsx)
  ⎿  Added 1 line, removed 1 line
      591        sse.close()
      592        sseRef.current = null
      593      }
      594 -  }, [handleChatEvent])
      594 +  }, [handleChatEvent, handleAgentEvent])
      595
      596    const waitForResponse = async (key: string, runId: string | null) => {
      597      // SSE handles incremental display. Completion should come from runId-based

⏺ 修改完成。总结两处改动：

  1. Bridge (comworker/bridge/routes/events.ts)
  - SSE listener 增加转发 agent 事件（原来只转发 chat）

  2. 前端 (frontend/src/pages/Chat.tsx)
  - 新增 handleAgentEvent 回调，处理两类 agent 事件：
    - stream: "tool" + phase: "start"/"call" → 取消 debounce timer，保持 agentRunning = true，防止前端误认为响应结束
    - stream: "lifecycle" + phase: "end" → agent run 真正结束，1 秒后加载最终消息并清理状态
  - sse.onmessage 中增加对 msg.event === 'agent' 的分发

  核心逻辑：当 chat final 触发 3 秒 debounce 后，如果期间收到 agent tool 事件，timer 会被取消，agentRunning 保持为 true，直到 agent lifecycle
  end 才真正完成。

