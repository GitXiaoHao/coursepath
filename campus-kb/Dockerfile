# 自研外部知识库服务镜像。
# 评审可核对点：基础镜像只用官方 python-slim，不引入 AGPL 组件；依赖见 requirements.txt。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖再拷代码：代码改动不会击穿依赖层缓存。
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY core ./core
COPY app ./app
COPY eval ./eval
COPY data/samples ./data/samples

# 容器内以非 root 运行；语料目录只读挂载即可。
RUN useradd --create-home --uid 10001 kbsvc && chown -R kbsvc:kbsvc /app
USER kbsvc

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"KB_PORT\",\"8080\")}/health', timeout=3)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${KB_PORT:-8080}"]
