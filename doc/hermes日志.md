Hermes gateway 默认的 stderr 输出级别是 WARNING（verbosity=0），所以 docker logs 只能看到 WARNING 以上的日志。正常的请求处理、agent 运行这些 INFO 级别的日志不会输出到 stderr，而是写到容器内的日志文件里：
/opt/data/logs/agent.log — 主日志（INFO+）
/opt/data/logs/gateway.log — gateway 组件日志（INFO+）
/opt/data/logs/errors.log — 错误日志（WARNING+）

从 gateway run 改为 gateway run -v
