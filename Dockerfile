FROM python:3.8-slim

RUN pip install "eventlet==0.30.2" ryu networkx requests

WORKDIR /app
COPY controller/ .

CMD [ "ryu-manager", "controller.py" ]