1. postgres
作用：
- 平台数据库
- 存用户、容器信息、配额、审计日志、shared agent 绑定关系等

端口：
- 15432 -> 5432

2. gateway
作用：
- 整个系统的核心后端
- 负责登录认证、用户管理、LLM 代理、配额统计
- 负责给 dedicated 用户启动“每用户一个 comworker 容器”
- 负责把 shared 用户路由到共享 comworker
- 前端基本都先请求它

端口：
- 8080

3. frontend
作用：
- 完整版前端
- 面向原来的完整 ComWorker 使用场景
- 更适合 dedicated 模式

端口：
- 3080

4. shared-comworker
作用：
- 新增的“共享 ComWorker 运行时”
- 所有 shared 用户共用这一个 comworker 实例
- 但 platform 会把每个用户映射成独立 agent + 独立 workspace
- 主要服务 share-comworker-front 和 shared API

不直接对外暴露端口：
- 只在内部网络里给 gateway 调用

5. manage-front
作用：
- 管理后台
- 管理员可以看用户、改配额、改状态
- 现在也可以把用户切换成：
  - dedicated
  - shared

端口：
- 3081

6. simple-front
作用：
- 轻量聊天前端
- 类似简化版聊天界面
- 更偏向原来已有的轻量入口

端口：
- 3082

7. share-comworker-front
作用：
- 新增的共享模式前端
- 专门给 shared 用户使用
- 用户只看到“自己的共享 Agent、自己的会话、自己的 workspace”
- 不会看到别人的 agent

端口：
- 3083

补充一句整体关系：

- dedicated 用户：
  frontend/simple-front -> gateway -> 该用户自己的 comworker 容器

- shared 用户：
  share-comworker-front -> gateway -> shared-comworker


注意：对于已经是shared用户，使用默认的frontend前端时，是无法连接和显示容器的，因为shared模式连接的是comworker_shared容器
