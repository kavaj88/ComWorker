目录： 
hermes-agent是Agent后端
platform是管理控制hermes-agent
frontend是连接platform，进行对话
deploy_copy会被拷贝到hermes-agent
.env文件会被platform使用

# 部署成容器
python deploy_docker.py --rebuild hermes,gateway,frontend --fast

ec5a24e03a8b   comworker-frontend             "/docker-entrypoint.…"   19 minutes ago   Up 19 minutes                     80/tcp, 0.0.0.0:3080->3000/tcp       comworker-frontend
7daabe89cc4d   comworker-gateway              "uvicorn app.main:ap…"   19 minutes ago   Up 19 minutes                     0.0.0.0:8080->8080/tcp               comworker-gateway

#真正的用户容器
ec4b38f8aa3c   comworker-hermes-agent:latest   "/opt/hermes/docker/…"   4 minutes ago                                          hermes-user-f0536784
