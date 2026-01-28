FROM python:3.8-slim

RUN pip install "eventlet==0.30.2" ryu networkx requests

WORKDIR /app
COPY ryu_scenario/controller/ .
COPY ryu_scenario/BaseLogger.py .

CMD [ "ryu-manager", "controller.py" ]