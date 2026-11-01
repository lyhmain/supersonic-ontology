FROM docker.m.daocloud.io/library/python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
COPY app.py engine.py insight.py config.py nl.py ontology.py jev.py spec.py badcase.py kb.py ui.html dash.html echarts.min.js ./
COPY profiles/ ./profiles/
RUN mkdir -p /app/data
EXPOSE 9102
ENV PORT=9102
CMD ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-9102}"]
