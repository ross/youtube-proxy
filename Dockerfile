FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
# The first time av is imported it takes a long time so do it during build
RUN python3 -m av
COPY . .
RUN find /app

CMD script/run
