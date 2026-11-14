# 知学 Mate MCP 服务
#
# 构建：docker build -t zhixue-mcp .
# 运行：docker run -p 8765:8765 -e MCP_API_KEY=*** -v $(pwd)/data:/app/data zhixue-mcp
#
# 依赖说明：10 个工具只用标准库，但 create_grounded_exercise 会经
# app.services.grounded_exercises 间接导入 app.api（题库），那条链路需要 Flask；
# 真正出题还要 openai。为免"本地能跑、容器里缺包"，这里显式装上这三个轻量依赖。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765

WORKDIR /app

RUN pip install --no-cache-dir flask flask-cors openai

# 业务逻辑：domain / tools / decision / agent.knowledge / agentarts.session / repositories
COPY app ./app
COPY zhixue_mcp ./zhixue_mcp
COPY config.py ./config.py

# 数据卷（JSON 仓储）；生产建议改 sqlite 或外部数据库
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8765

# 健康检查：/healthz 也要 API Key，故这里只探端口连通性
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import socket,sys; s=socket.socket(); s.settimeout(3); sys.exit(0 if s.connect_ex(('127.0.0.1',8765))==0 else 1)"

CMD ["python", "-m", "zhixue_mcp.server"]
